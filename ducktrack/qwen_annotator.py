import base64
import io
import json
import os
import re
import time
import urllib.request
from pathlib import Path


SYSTEM_PROMPT = """You are a Windows GUI trajectory annotation model.
Use the operation event and ordered screenshots to label one real user action.
The screenshots are ordered as before, about 250ms after, and settled state.
Do not invent invisible controls, scientific meaning, or experimental success.
success only means that the expected visible GUI change occurred.
For uncertain information, use null or unknown and lower confidence.
The target bbox is [left, top, right, bottom] in pixels of the before screenshot.
Return exactly one JSON object without markdown or additional text:
{
  "step_instruction": "concise description of the actual action",
  "intent": "probable local intent",
  "action_type": "click|double_click|right_click|drag|scroll|type|key_press|wait|unknown",
  "target": {
    "name": "visible target name or position description",
    "control_type": "button|input|menu|tab|checkbox|dialog|canvas|chart|unknown",
    "monitor_id": "display id or null",
    "bbox": [0, 0, 0, 0]
  },
  "input_text": "original input text or null",
  "result": "directly observable GUI change",
  "status": "success|failed|pending|no_visible_change|uncertain",
  "risk_level": "low|medium|high|critical",
  "requires_confirmation": false,
  "confidence": 0.0,
  "uncertainties": []
}"""


class AnnotationResponseError(ValueError):
    def __init__(self, message: str, attempts: list[dict]):
        super().__init__(message)
        self.attempts = attempts


class QwenAnnotator:
    def __init__(self):
        self.url = os.environ.get("DUCKTRACK_QWEN_URL", "").strip()
        self.model = os.environ.get("DUCKTRACK_QWEN_MODEL", "Qwen3.6-27B")
        self.token = os.environ.get("DUCKTRACK_QWEN_TOKEN", "").strip()
        self.max_tokens = int(os.environ.get("DUCKTRACK_QWEN_MAX_TOKENS", "2048"))
        self.timeout = float(os.environ.get("DUCKTRACK_QWEN_TIMEOUT", "1800"))
        self.max_image_edge = int(os.environ.get("DUCKTRACK_QWEN_IMAGE_MAX_EDGE", "1600"))
        self.max_attempts = max(1, int(os.environ.get("DUCKTRACK_QWEN_MAX_ATTEMPTS", "2")))
        self.enable_thinking = os.environ.get("DUCKTRACK_QWEN_ENABLE_THINKING", "0").lower() in {
            "1", "true", "yes", "on"
        }

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def annotate_trajectory(self, recording: Path, progress_callback=None) -> dict:
        trajectory_path = recording / "trajectory.json"
        payload = json.loads(trajectory_path.read_text(encoding="utf-8"))
        steps = payload.get("trajectory", [])
        response_dir = recording / "model_responses"
        response_dir.mkdir(parents=True, exist_ok=True)
        metadata = self._read_json(recording / "metadata.json", {})
        history: list[dict] = []
        failures = 0

        labelable = [index for index, step in enumerate(steps) if self._action_name(step) != "terminate"]
        total = len(labelable)
        for position, index in enumerate(labelable, start=1):
            step = steps[index]
            response_path = response_dir / f"step_{index:06d}.json"
            if progress_callback:
                progress_callback(position - 1, total, index, "running")
            try:
                cached = self._read_json(response_path, {})
                if cached.get("validation_status") == "passed" and isinstance(cached.get("parsed_annotation"), dict):
                    annotation = cached["parsed_annotation"]
                    raw = cached
                    raw["resumed_from_cache"] = True
                else:
                    annotation, raw = self._annotate_step(recording, step, metadata, history)
                self._validate_geometry(annotation, step, metadata)
                self._apply_annotation(step, annotation)
                raw["parsed_annotation"] = annotation
                raw["validation_status"] = "passed"
                history.append({
                    "action": step.get("action"),
                    "step_instruction": annotation.get("step_instruction"),
                    "result": annotation.get("result"),
                })
            except Exception as exc:
                failures += 1
                raw = {
                    "validation_status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                if isinstance(exc, AnnotationResponseError):
                    raw["attempts"] = exc.attempts
                step["annotation"] = {
                    "status": "uncertain",
                    "confidence": 0.0,
                    "uncertainties": [raw["error"]],
                    "needs_review": True,
                }
            response_path.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            if progress_callback:
                progress_callback(position, total, index, "running")

        payload["annotation_metadata"] = {
            "model": self.model,
            "provider": "openai_compatible_chat_completions",
            "prompt_version": "gui-label-v1",
            "completed_steps": total - failures,
            "failed_steps": failures,
            "total_steps": total,
            "annotated_at_unix": time.time(),
        }
        return payload

    def _annotate_step(self, recording: Path, step: dict, metadata: dict, history: list[dict]):
        content: list[dict] = [{
            "type": "text",
            "text": json.dumps({
                "monitor_layout": metadata.get("monitors", []),
                "virtual_desktop": metadata.get("virtual_desktop"),
                "recent_history": history[-3:],
                "current_action": step.get("action"),
                "elapsed_time": step.get("elapsed_time"),
                "image_order": ["before", "after_250ms", "settled"],
                "screenshot_coordinate_space": {
                    "width": step.get("width") or (metadata.get("virtual_desktop") or {}).get("width"),
                    "height": step.get("height") or (metadata.get("virtual_desktop") or {}).get("height"),
                    "origin_x": (metadata.get("virtual_desktop") or {}).get("x", 0),
                    "origin_y": (metadata.get("virtual_desktop") or {}).get("y", 0),
                },
            }, ensure_ascii=False),
        }]
        image_paths = self._step_images(step)
        action_point = self._action_point(step, metadata)
        for label, relpath in image_paths:
            path = self._safe_recording_path(recording, relpath)
            if path and path.is_file():
                content.append({"type": "text", "text": f"Screenshot: {label}"})
                content.append({
                    "type": "image_url",
                    "image_url": {"url": self._image_data_url(path)},
                })
                if label in {"before", "observation"} and action_point is not None:
                    crop_box = self._focus_crop(path, action_point)
                    if crop_box is not None:
                        content.append({
                            "type": "text",
                            "text": (
                                "Screenshot: before_focus_crop. The red ring marks the recorded action point. "
                                f"Crop origin in the full screenshot is ({crop_box[0]}, {crop_box[1]})."
                            ),
                        })
                        content.append({
                            "type": "image_url",
                            "image_url": {
                                "url": self._image_data_url(path, crop_box=crop_box, marker=action_point)
                            },
                        })
        if not any(item.get("type") == "image_url" for item in content):
            content[0]["text"] += "\nNo screenshot was available; lower confidence accordingly."

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]
        attempts = []
        last_error = None
        for attempt in range(1, self.max_attempts + 1):
            if attempt > 1:
                messages.append({
                    "role": "user",
                    "content": "The previous response was empty or invalid. Return one complete valid JSON object only.",
                })
            response = self._chat(messages)
            attempts.append(response)
            try:
                annotation = self._parse_annotation(response)
                return annotation, {
                    "model": self.model,
                    "attempt_count": attempt,
                    "attempts": attempts,
                }
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
        raise AnnotationResponseError(
            f"model response remained invalid after {self.max_attempts} attempts: {last_error}",
            attempts,
        )

    def _chat(self, messages: list[dict]) -> dict:
        request_payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": self.max_tokens,
            "chat_template_kwargs": {"enable_thinking": self.enable_thinking},
        }
        body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def _parse_annotation(self, response: dict) -> dict:
        choices = response.get("choices") or []
        if not choices:
            raise ValueError("response has no choices")
        content = (choices[0].get("message") or {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("response content is empty")
        cleaned = re.sub(r"^\s*```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned).strip()
        start = cleaned.find("{")
        if start < 0:
            raise ValueError("response does not contain a JSON object")
        annotation, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        if not isinstance(annotation, dict):
            raise ValueError("annotation is not an object")
        instruction = annotation.get("step_instruction")
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("step_instruction is missing")
        confidence = float(annotation.get("confidence", 0.0))
        annotation["confidence"] = max(0.0, min(1.0, confidence))
        annotation.setdefault("uncertainties", [])
        annotation.setdefault("status", "uncertain")
        annotation.setdefault("risk_level", "low")
        annotation.setdefault("requires_confirmation", False)
        return annotation

    def _validate_geometry(self, annotation: dict, step: dict, metadata: dict):
        actions = step.get("action") or []
        params = actions[0].get("parameters", {}) if actions and isinstance(actions[0], dict) else {}
        screen_x = params.get("screen_x")
        screen_y = params.get("screen_y")
        bbox = (annotation.get("target") or {}).get("bbox")
        if screen_x is None or screen_y is None or not isinstance(bbox, list) or len(bbox) != 4:
            return
        desktop = metadata.get("virtual_desktop") or {}
        point_x = float(screen_x) - float(desktop.get("x", 0))
        point_y = float(screen_y) - float(desktop.get("y", 0))
        try:
            left, top, right, bottom = (float(value) for value in bbox)
        except (TypeError, ValueError):
            return
        if not (left <= point_x <= right and top <= point_y <= bottom):
            target = annotation.setdefault("target", {})
            target["model_bbox"] = target.get("bbox")
            target["bbox"] = None
            target["bbox_valid"] = False
            annotation["geometry_valid"] = False
            annotation["confidence"] = min(float(annotation.get("confidence", 0.0)), 0.75)
            uncertainties = annotation.setdefault("uncertainties", [])
            uncertainties.append("Model target bbox does not contain the recorded click coordinate.")

    def _apply_annotation(self, step: dict, annotation: dict):
        step["step_instruction"] = annotation.get("step_instruction", "")
        step["rationale"] = annotation.get("intent")
        step["screenshot_caption"] = annotation.get("result")
        step["annotation"] = annotation
        step["annotation"]["needs_review"] = (
            annotation.get("confidence", 0.0) < 0.7 or annotation.get("geometry_valid") is False
        )

    def _step_images(self, step: dict) -> list[tuple[str, str]]:
        frames = step.get("frames") or {}
        images = []
        for key, label in (("before", "before"), ("after", "after_250ms"), ("settled", "settled")):
            value = frames.get(key)
            if isinstance(value, str) and value:
                images.append((label, value))
        if not images:
            fallback = step.get("observation") or step.get("objects")
            if isinstance(fallback, str) and fallback:
                images.append(("observation", fallback))
        return images

    def _safe_recording_path(self, recording: Path, relpath: str) -> Path | None:
        base = recording.resolve()
        target = (base / relpath.replace("\\", "/")).resolve()
        if target != base and base not in target.parents:
            return None
        return target

    def _image_data_url(self, path: Path, crop_box=None, marker=None) -> str:
        try:
            from PIL import Image, ImageDraw

            with Image.open(path) as image:
                image = image.convert("RGB")
                if crop_box is not None:
                    image = image.crop(crop_box)
                    if marker is not None:
                        marker_x = marker[0] - crop_box[0]
                        marker_y = marker[1] - crop_box[1]
                        draw = ImageDraw.Draw(image)
                        radius = 16
                        draw.ellipse(
                            (marker_x - radius, marker_y - radius, marker_x + radius, marker_y + radius),
                            outline=(255, 32, 32),
                            width=5,
                        )
                image.thumbnail((self.max_image_edge, self.max_image_edge))
                output = io.BytesIO()
                image.save(output, "JPEG", quality=82, optimize=False)
                data = output.getvalue()
            mime = "image/jpeg"
        except ImportError:
            data = path.read_bytes()
            mime = "image/webp" if path.suffix.lower() == ".webp" else "image/png"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    def _action_point(self, step: dict, metadata: dict):
        actions = step.get("action") or []
        params = actions[0].get("parameters", {}) if actions and isinstance(actions[0], dict) else {}
        desktop = metadata.get("virtual_desktop") or {}
        if params.get("screen_x") is not None and params.get("screen_y") is not None:
            return (
                int(float(params["screen_x"]) - float(desktop.get("x", 0))),
                int(float(params["screen_y"]) - float(desktop.get("y", 0))),
            )
        if params.get("x") is not None and params.get("y") is not None:
            width = step.get("width") or desktop.get("width")
            height = step.get("height") or desktop.get("height")
            if width and height:
                return (int(float(params["x"]) * width), int(float(params["y"]) * height))
        return None

    def _focus_crop(self, path: Path, point):
        try:
            from PIL import Image

            with Image.open(path) as image:
                width, height = image.size
        except (ImportError, OSError):
            return None
        crop_width = min(1200, width)
        crop_height = min(900, height)
        left = max(0, min(width - crop_width, int(point[0] - crop_width / 2)))
        top = max(0, min(height - crop_height, int(point[1] - crop_height / 2)))
        return (left, top, left + crop_width, top + crop_height)

    def _action_name(self, step: dict) -> str | None:
        actions = step.get("action") or []
        return actions[0].get("name") if actions and isinstance(actions[0], dict) else None

    def _read_json(self, path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default
