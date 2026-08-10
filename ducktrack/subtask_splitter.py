"""Model-based whole-trajectory sub-task splitting (Feature-2).

v1: text action summary only (subtask-split-v1 / qwen_subtask_v1)
v2: text summary + up to N keyframe screenshots (subtask-split-v2 / qwen_subtask_v2)
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from .subtask_schema import (
    action_name,
    apply_model_sub_tasks,
    last_actionable_step_index,
    make_sub_task,
    validate_sub_tasks,
)


PROMPT_VERSION_V1 = "subtask-split-v1"
PROMPT_VERSION_V2 = "subtask-split-v2"
SPLITTER_NAME_V1 = "qwen_subtask_v1"
SPLITTER_NAME_V2 = "qwen_subtask_v2"
CONFIDENCE_REVIEW_THRESHOLD = 0.7

# Legacy aliases (v1 default names kept for imports/tests)
PROMPT_VERSION = PROMPT_VERSION_V2
SPLITTER_NAME = SPLITTER_NAME_V2

SYSTEM_PROMPT_V1 = """You are a Windows GUI trajectory sub-task segmentation model.
Given an ordered list of atomic GUI actions (and optional per-step instructions/intents),
split the trajectory into contiguous sub-tasks. Each sub-task represents one user intent /
operation goal (not one atomic click).

Rules:
1. Return ONLY a JSON array, no markdown or extra text.
2. Each item: {"start_step": int, "end_step": int, "instruction": string, "confidence": number}
3. instruction must be a non-empty concise Chinese or English intent description.
4. confidence is in [0, 1].
5. Ranges are inclusive, contiguous, non-overlapping, and must cover every step index from 0
   through last_step_index (provided in the user message). No gaps, no empty segments.
6. Prefer fewer meaningful intent segments over one-step-per-segment.
7. Do not invent scientific outcomes; describe GUI operation intent only.

Example:
[
  {"start_step": 0, "end_step": 2, "instruction": "打开面板并拖到目标区域", "confidence": 0.86},
  {"start_step": 3, "end_step": 5, "instruction": "滚动浏览列表", "confidence": 0.8}
]
"""

SYSTEM_PROMPT_V2 = """You are a Windows GUI trajectory sub-task segmentation model with vision.
You will receive:
(1) an ordered text summary of atomic GUI actions, and
(2) up to 12 keyframe screenshots, each labeled with its step index and action type.

Your job: split the trajectory into contiguous sub-tasks. Each sub-task is one user intent /
GUI operation goal (not one atomic click). Also write a clear instruction for each sub-task.

Evidence priority:
1. Screenshot content (window titles, buttons, menus, input fields, dialogs) is primary for writing instruction.
2. Action names/parameters define order and ranges; use them to decide start_step/end_step.
3. If text summary conflicts with what is visible in screenshots, trust the screenshots.
4. Screenshot path strings in JSON are NOT visual evidence; ignore paths; only provided images count.
5. If a span has no screenshot, infer cautiously from neighboring actions/images and lower confidence.

Rules:
1. Return ONLY a JSON array, no markdown or extra text.
2. Each item MUST be:
   {"start_step": int, "end_step": int, "instruction": string, "confidence": number}
3. instruction must be non-empty, concise, and concrete (Chinese preferred unless UI is English-only).
   Good: "在开始菜单中搜索并打开记事本"
   Bad: "执行后续界面操作" / "进行一些点击"
4. confidence is in [0, 1]. Lower confidence when images are missing or UI text is unreadable.
5. Ranges are inclusive, contiguous, non-overlapping, and MUST cover every index from 0 through
   last_step_index. No gaps, no empty segments. Do not include terminate-only trailing steps if
   last_step_index already excludes them.
6. Prefer fewer meaningful intent segments over one-step-per-segment.
7. Place boundaries where the user goal changes (e.g. switch app/window, finish typing and submit,
   open a new panel, start a different workflow).
8. Do not invent scientific outcomes or hidden system state; describe only GUI operation intent.

Example:
[
  {"start_step": 0, "end_step": 4, "instruction": "打开文件资源管理器并进入目标文件夹", "confidence": 0.88},
  {"start_step": 5, "end_step": 9, "instruction": "在搜索框输入关键词并查看结果", "confidence": 0.84}
]
"""

# Back-compat name
SYSTEM_PROMPT = SYSTEM_PROMPT_V2


class SubtaskSplitError(ValueError):
    pass


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


class SubtaskSplitter:
    def __init__(self):
        self.url = (
            os.environ.get("DUCKTRACK_SUBTASK_URL", "").strip()
            or os.environ.get("DUCKTRACK_QWEN_URL", "").strip()
        )
        self.model = (
            os.environ.get("DUCKTRACK_SUBTASK_MODEL", "").strip()
            or os.environ.get("DUCKTRACK_QWEN_MODEL", "Qwen/Qwen3-VL-32B-Instruct")
        )
        self.token = (
            os.environ.get("DUCKTRACK_SUBTASK_TOKEN", "").strip()
            or os.environ.get("DUCKTRACK_QWEN_TOKEN", "").strip()
        )
        self.max_tokens = int(
            os.environ.get(
                "DUCKTRACK_SUBTASK_MAX_TOKENS",
                os.environ.get("DUCKTRACK_QWEN_MAX_TOKENS", "4096"),
            )
        )
        self.timeout = float(
            os.environ.get(
                "DUCKTRACK_SUBTASK_TIMEOUT",
                os.environ.get("DUCKTRACK_QWEN_TIMEOUT", "1800"),
            )
        )
        self.max_attempts = max(
            1,
            int(
                os.environ.get(
                    "DUCKTRACK_SUBTASK_MAX_ATTEMPTS",
                    os.environ.get("DUCKTRACK_QWEN_MAX_ATTEMPTS", "2"),
                )
            ),
        )
        self.enable_thinking = _env_flag(
            "DUCKTRACK_SUBTASK_ENABLE_THINKING",
            os.environ.get("DUCKTRACK_QWEN_ENABLE_THINKING", "0"),
        )
        # v2 vision on by default; set DUCKTRACK_SUBTASK_VISION=0 to force text-only v1
        self.vision_enabled = _env_flag("DUCKTRACK_SUBTASK_VISION", "1")
        self.max_images = max(
            0,
            int(os.environ.get("DUCKTRACK_SUBTASK_MAX_IMAGES", "12")),
        )
        self.max_image_edge = int(
            os.environ.get(
                "DUCKTRACK_SUBTASK_IMAGE_MAX_EDGE",
                os.environ.get("DUCKTRACK_QWEN_IMAGE_MAX_EDGE", "1024"),
            )
        )
        # If vision requested but no usable frames, optionally fall back to v1 text prompt
        self.fallback_text = _env_flag("DUCKTRACK_SUBTASK_FALLBACK_TEXT", "1")

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def split_trajectory_payload(
        self,
        payload: dict,
        *,
        response_dir: Path | None = None,
        recording: Path | None = None,
    ) -> dict:
        if not self.configured:
            raise SubtaskSplitError("未配置划分模型（请设置 DUCKTRACK_QWEN_URL 或 DUCKTRACK_SUBTASK_URL）")
        if not isinstance(payload, dict):
            raise SubtaskSplitError("trajectory payload 无效")
        steps = payload.get("trajectory")
        if not isinstance(steps, list) or not steps:
            raise SubtaskSplitError("trajectory 步骤为空")

        last = last_actionable_step_index(steps)
        if last < 0:
            raise SubtaskSplitError("没有可划分的操作步骤")

        summary = self._build_summary(payload, last)
        recording_path = Path(recording) if recording else None
        keyframes: list[dict] = []
        use_vision = False
        if self.vision_enabled and self.max_images > 0 and recording_path is not None:
            keyframes = self._select_keyframes(steps, last, recording_path)
            use_vision = bool(keyframes)
        elif self.vision_enabled and self.max_images > 0 and recording_path is None:
            # Vision desired but no recording dir — can only do text unless we forbid
            if not self.fallback_text:
                raise SubtaskSplitError(
                    "已启用看图划分但未提供录制目录（recording），无法读取截图"
                )

        if use_vision:
            prompt_version = PROMPT_VERSION_V2
            splitter_name = SPLITTER_NAME_V2
            system_prompt = SYSTEM_PROMPT_V2
        else:
            prompt_version = PROMPT_VERSION_V1
            splitter_name = SPLITTER_NAME_V1
            system_prompt = SYSTEM_PROMPT_V1

        parsed, raw = self._call_model(
            summary,
            last,
            system_prompt=system_prompt,
            keyframes=keyframes if use_vision else None,
        )

        sub_tasks = []
        for index, item in enumerate(parsed):
            confidence = float(item.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
            instruction = str(item.get("instruction") or "").strip()
            if not instruction:
                raise SubtaskSplitError(f"模型返回的第 {index} 段 instruction 为空")
            sub_tasks.append(
                make_sub_task(
                    index=index,
                    instruction=instruction,
                    start_step=int(item["start_step"]),
                    end_step=int(item["end_step"]),
                    source="model",
                    confidence=confidence,
                    needs_review=confidence < CONFIDENCE_REVIEW_THRESHOLD,
                    model=self.model,
                    prompt_version=prompt_version,
                )
            )

        errors = validate_sub_tasks(steps, sub_tasks)
        if errors:
            raise SubtaskSplitError("模型划分未通过校验: " + "; ".join(errors))

        if response_dir is not None:
            response_dir.mkdir(parents=True, exist_ok=True)
            (response_dir / "subtask_split.json").write_text(
                json.dumps(
                    {
                        "model": self.model,
                        "prompt_version": prompt_version,
                        "splitter": splitter_name,
                        "vision_enabled": use_vision,
                        "keyframes": [
                            {
                                "step": k["step"],
                                "action": k["action"],
                                "role": k["role"],
                                "relpath": k.get("relpath"),
                            }
                            for k in keyframes
                        ],
                        "summary": summary,
                        "raw": raw,
                        "parsed_sub_tasks": sub_tasks,
                        "split_at_unix": time.time(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        apply_model_sub_tasks(
            payload,
            sub_tasks,
            splitter=splitter_name,
            prompt_version=prompt_version,
        )
        return payload

    def split_recording(self, recording: Path) -> dict:
        recording = Path(recording)
        for name in ("trajectory.reviewed.json", "trajectory.annotated.json", "trajectory.json"):
            path = recording / name
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
                updated = self.split_trajectory_payload(
                    payload,
                    response_dir=recording / "model_responses",
                    recording=recording,
                )
                path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
                raw_path = recording / "trajectory.json"
                if path.resolve() != raw_path.resolve():
                    if name != "trajectory.json":
                        raw_path.write_text(
                            json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                return updated
        raise SubtaskSplitError("recording 中未找到 trajectory.json")

    def _build_summary(self, payload: dict, last: int) -> dict:
        steps = payload.get("trajectory") or []
        items = []
        for index in range(0, last + 1):
            step = steps[index]
            actions = step.get("action") or []
            action0 = actions[0] if actions and isinstance(actions[0], dict) else {}
            params = action0.get("parameters") if isinstance(action0, dict) else {}
            ann = step.get("annotation") if isinstance(step.get("annotation"), dict) else {}
            frames = step.get("frames") if isinstance(step.get("frames"), dict) else {}
            compact_params = {}
            if isinstance(params, dict):
                for key in (
                    "text",
                    "key",
                    "clicks",
                    "button",
                    "x",
                    "y",
                    "start_x",
                    "start_y",
                    "end_x",
                    "end_y",
                    "monitor_id",
                ):
                    if key in params:
                        compact_params[key] = params[key]
            items.append(
                {
                    "index": index,
                    "action": action_name(step) or action0.get("name"),
                    "parameters": compact_params,
                    "step_instruction": step.get("step_instruction")
                    or ann.get("step_instruction")
                    or "",
                    "intent": ann.get("intent") or step.get("rationale") or "",
                    "before": frames.get("before") or step.get("observation") or step.get("objects"),
                }
            )
        return {
            "query": payload.get("query") or "",
            "last_step_index": last,
            "steps": items,
        }

    def _step_frame_relpath(self, step: dict) -> str | None:
        frames = step.get("frames") if isinstance(step.get("frames"), dict) else {}
        for key in ("settled", "before", "after"):
            value = frames.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        for key in ("observation", "objects"):
            value = step.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _safe_recording_path(self, recording: Path, relpath: str) -> Path | None:
        base = recording.resolve()
        target = (base / relpath.replace("\\", "/")).resolve()
        if target != base and base not in target.parents:
            return None
        return target

    def _action_family(self, name: str | None) -> str:
        n = (name or "").lower()
        if n in {"click", "leftclick", "left_click", "rightclick", "right_click", "doubleclick", "double_click"}:
            return "pointer"
        if n in {"type", "type_text", "typing"}:
            return "type"
        if n in {"scroll", "wheel"}:
            return "scroll"
        if n in {"drag", "dragto", "drag_to"}:
            return "drag"
        if n in {"hotkey", "key", "key_press", "press"}:
            return "key"
        if n in {"wait"}:
            return "wait"
        return n or "other"

    def _select_keyframes(self, steps: list, last: int, recording: Path) -> list[dict]:
        """Pick up to max_images frames: boundary candidates first, then uniform fill."""
        if self.max_images <= 0 or last < 0:
            return []

        candidates: dict[int, str] = {}

        def add_candidate(index: int, role: str) -> None:
            if index < 0 or index > last:
                return
            prev = candidates.get(index)
            # boundary wins over representative
            if prev == "boundary":
                return
            candidates[index] = role

        # Always try first / last actionable
        add_candidate(0, "boundary")
        add_candidate(last, "boundary")

        prev_family = None
        for index in range(0, last + 1):
            name = action_name(steps[index])
            family = self._action_family(name)
            if prev_family is not None and family != prev_family:
                add_candidate(index, "boundary")
                if index - 1 >= 0:
                    add_candidate(index - 1, "boundary")
            # type / drag segment edges are strong intent cues
            if family in {"type", "drag"} and (prev_family != family):
                add_candidate(index, "boundary")
            prev_family = family

        # Uniform representatives
        if last == 0:
            span = [0]
        else:
            # aim to seed enough slots before capping
            want = max(self.max_images, 1)
            span = sorted(
                {
                    int(round(i * last / max(want - 1, 1)))
                    for i in range(want)
                }
            )
        for index in span:
            add_candidate(index, "representative")

        # Prefer boundary order, then index
        ordered = sorted(
            candidates.items(),
            key=lambda item: (0 if item[1] == "boundary" else 1, item[0]),
        )

        selected: list[dict] = []
        seen_files: set[str] = set()
        for index, role in ordered:
            if len(selected) >= self.max_images:
                break
            relpath = self._step_frame_relpath(steps[index])
            if not relpath:
                continue
            path = self._safe_recording_path(recording, relpath)
            if path is None or not path.is_file():
                continue
            # Dedup identical resolved paths (common when frames reused across steps)
            key = str(path)
            if key in seen_files:
                continue
            seen_files.add(key)
            try:
                data_url = self._image_data_url(path)
            except Exception:
                continue
            selected.append(
                {
                    "step": index,
                    "action": action_name(steps[index]),
                    "role": role,
                    "relpath": relpath,
                    "data_url": data_url,
                }
            )

        selected.sort(key=lambda item: item["step"])
        return selected

    def _image_data_url(self, path: Path) -> str:
        try:
            from PIL import Image

            with Image.open(path) as image:
                image = image.convert("RGB")
                image.thumbnail((self.max_image_edge, self.max_image_edge))
                output = io.BytesIO()
                image.save(output, "JPEG", quality=80, optimize=False)
                data = output.getvalue()
            mime = "image/jpeg"
        except ImportError:
            data = path.read_bytes()
            suffix = path.suffix.lower()
            mime = "image/webp" if suffix == ".webp" else ("image/png" if suffix == ".png" else "image/jpeg")
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    def _call_model(
        self,
        summary: dict,
        last: int,
        *,
        system_prompt: str,
        keyframes: list[dict] | None,
    ) -> tuple[list, dict]:
        if keyframes:
            content: list[dict] = [
                {
                    "type": "text",
                    "text": (
                        f"last_step_index={last}\n"
                        f"max_images={len(keyframes)}\n"
                        "Split the following trajectory into sub-tasks.\n"
                        "Use BOTH the JSON summary and the attached keyframe screenshots.\n"
                        "JSON summary:\n"
                        + json.dumps(summary, ensure_ascii=False)
                    ),
                }
            ]
            for frame in keyframes:
                content.append(
                    {
                        "type": "text",
                        "text": (
                            f"Keyframe: step={frame['step']}, "
                            f"action={frame.get('action') or 'unknown'}, "
                            f"role={frame.get('role') or 'representative'}"
                        ),
                    }
                )
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": frame["data_url"]},
                    }
                )
            user_message: dict = {"role": "user", "content": content}
        else:
            user_message = {
                "role": "user",
                "content": (
                    f"last_step_index={last}\n"
                    "Split the following trajectory into sub-tasks.\n"
                    + json.dumps(summary, ensure_ascii=False)
                ),
            }

        messages = [
            {"role": "system", "content": system_prompt},
            user_message,
        ]
        attempts = []
        last_error = None
        for attempt in range(1, self.max_attempts + 1):
            if attempt > 1:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Previous response was invalid. "
                            f"Return ONLY a JSON array covering 0..{last} with non-empty instructions."
                        ),
                    }
                )
            response = self._chat(messages)
            attempts.append(response)
            try:
                parsed = self._parse_array(response)
                return parsed, {
                    "model": self.model,
                    "attempt_count": attempt,
                    "vision": bool(keyframes),
                    "keyframe_count": len(keyframes or []),
                    "attempts": attempts,
                }
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
        raise SubtaskSplitError(
            f"模型响应无效（{self.max_attempts} 次）: {last_error}"
        )

    def _chat(self, messages: list[dict]) -> dict:
        request_payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": self.max_tokens,
        }
        # Only send thinking flags when explicitly enabled. Some VL gateways
        # return HTTP 400 if enable_thinking / chat_template_kwargs are present.
        if self.enable_thinking:
            request_payload["enable_thinking"] = True
            request_payload["chat_template_kwargs"] = {"enable_thinking": True}

        body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        # Serverless VL endpoints occasionally reject the first cold request with
        # HTTP 400/429/5xx; a short retry usually succeeds (matches user observation).
        http_attempts = max(1, int(os.environ.get("DUCKTRACK_SUBTASK_HTTP_RETRIES", "3")))
        last_error: Exception | None = None
        for attempt in range(1, http_attempts + 1):
            request = urllib.request.Request(
                self.url,
                data=body,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    detail = str(exc)
                last_error = urllib.error.HTTPError(
                    exc.url, exc.code, f"{exc.reason}: {detail[:800]}", exc.headers, None
                )
                # Retry transient / cold-start style failures
                if exc.code in {400, 408, 409, 429, 500, 502, 503, 504} and attempt < http_attempts:
                    time.sleep(min(2.0 * attempt, 6.0))
                    continue
                raise last_error
            except Exception as exc:
                last_error = exc
                if attempt < http_attempts:
                    time.sleep(min(2.0 * attempt, 6.0))
                    continue
                raise
        if last_error:
            raise last_error
        raise RuntimeError("model chat failed without response")

    def _message_text(self, response: dict) -> str:
        choices = response.get("choices") or []
        if not choices:
            raise ValueError("response has no choices")
        message = choices[0].get("message") or {}
        for key in ("content", "reasoning_content", "reasoning"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value
            # Some VL gateways return content as a list of parts
            if isinstance(value, list):
                parts = []
                for part in value:
                    if isinstance(part, str) and part.strip():
                        parts.append(part)
                    elif isinstance(part, dict):
                        text = part.get("text") or part.get("content")
                        if isinstance(text, str) and text.strip():
                            parts.append(text)
                if parts:
                    return "\n".join(parts)
        for key in ("text", "content"):
            value = choices[0].get(key)
            if isinstance(value, str) and value.strip():
                return value
        raise ValueError(
            "response content is empty "
            f"(finish_reason={choices[0].get('finish_reason')!r}; "
            "请确认 VL 模型支持 image_url，或关闭 thinking / 提高 max_tokens)"
        )

    def _parse_array(self, response: dict) -> list:
        content = self._message_text(response)
        cleaned = re.sub(r"^\s*```(?:json)?\s*", "", content, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned).strip()
        start = cleaned.find("[")
        if start < 0:
            raise ValueError("response does not contain a JSON array")
        parsed, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        if not isinstance(parsed, list) or not parsed:
            raise ValueError("parsed result is not a non-empty array")
        for item in parsed:
            if not isinstance(item, dict):
                raise ValueError("array item is not an object")
            if "start_step" not in item or "end_step" not in item:
                raise ValueError("array item missing start_step/end_step")
            if not str(item.get("instruction") or "").strip():
                raise ValueError("array item missing instruction")
        return parsed
