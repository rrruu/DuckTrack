"""Model-based whole-trajectory sub-task splitting (Feature-2 one-click)."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from pathlib import Path

from .subtask_schema import (
    action_name,
    apply_model_sub_tasks,
    last_actionable_step_index,
    make_sub_task,
    validate_sub_tasks,
)


PROMPT_VERSION = "subtask-split-v1"
SPLITTER_NAME = "qwen_subtask_v1"
CONFIDENCE_REVIEW_THRESHOLD = 0.7

SYSTEM_PROMPT = """You are a Windows GUI trajectory sub-task segmentation model.
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


class SubtaskSplitError(ValueError):
    pass


class SubtaskSplitter:
    def __init__(self):
        self.url = (
            os.environ.get("DUCKTRACK_SUBTASK_URL", "").strip()
            or os.environ.get("DUCKTRACK_QWEN_URL", "").strip()
        )
        self.model = (
            os.environ.get("DUCKTRACK_SUBTASK_MODEL", "").strip()
            or os.environ.get("DUCKTRACK_QWEN_MODEL", "Qwen3.6-27B")
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
        self.enable_thinking = os.environ.get(
            "DUCKTRACK_SUBTASK_ENABLE_THINKING",
            os.environ.get("DUCKTRACK_QWEN_ENABLE_THINKING", "0"),
        ).lower() in {"1", "true", "yes", "on"}

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def split_trajectory_payload(self, payload: dict, *, response_dir: Path | None = None) -> dict:
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
        parsed, raw = self._call_model(summary, last)

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
                    prompt_version=PROMPT_VERSION,
                )
            )

        errors = validate_sub_tasks(steps, sub_tasks)
        # Also require non-empty instructions (already checked) and coverage via validate
        if errors:
            raise SubtaskSplitError("模型划分未通过校验: " + "; ".join(errors))

        if response_dir is not None:
            response_dir.mkdir(parents=True, exist_ok=True)
            (response_dir / "subtask_split.json").write_text(
                json.dumps(
                    {
                        "model": self.model,
                        "prompt_version": PROMPT_VERSION,
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
            splitter=SPLITTER_NAME,
            prompt_version=PROMPT_VERSION,
        )
        return payload

    def split_recording(self, recording: Path) -> dict:
        recording = Path(recording)
        # Prefer reviewed > annotated > raw when splitting on disk
        for name in ("trajectory.reviewed.json", "trajectory.annotated.json", "trajectory.json"):
            path = recording / name
            if path.is_file():
                payload = json.loads(path.read_text(encoding="utf-8"))
                updated = self.split_trajectory_payload(
                    payload, response_dir=recording / "model_responses"
                )
                # Write back to the same preferred file; also sync trajectory.json if different
                path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
                raw_path = recording / "trajectory.json"
                if path.resolve() != raw_path.resolve():
                    # Keep reviewed/annotated as source of truth; still update raw for local tools
                    # that only read trajectory.json — only if splitting annotated/reviewed.
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
                for key in ("text", "key", "clicks", "button", "x", "y", "start_x", "start_y", "end_x", "end_y"):
                    if key in params:
                        compact_params[key] = params[key]
            items.append(
                {
                    "index": index,
                    "action": action_name(step) or action0.get("name"),
                    "parameters": compact_params,
                    "step_instruction": step.get("step_instruction") or ann.get("step_instruction") or "",
                    "intent": ann.get("intent") or step.get("rationale") or "",
                    "before": frames.get("before") or step.get("observation") or step.get("objects"),
                }
            )
        return {
            "query": payload.get("query") or "",
            "last_step_index": last,
            "steps": items,
        }

    def _call_model(self, summary: dict, last: int) -> tuple[list, dict]:
        user_text = (
            f"last_step_index={last}\n"
            "Split the following trajectory into sub-tasks.\n"
            + json.dumps(summary, ensure_ascii=False)
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
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
                return parsed, {"model": self.model, "attempt_count": attempt, "attempts": attempts}
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
        raise SubtaskSplitError(
            f"模型响应无效（{self.max_attempts} 次）: {last_error}"
        )

    def _chat(self, messages: list[dict]) -> dict:
        request_payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": self.max_tokens,
            # Qwen3 / SiliconFlow: disable thinking so final JSON lands in message.content
            "enable_thinking": bool(self.enable_thinking),
            "chat_template_kwargs": {"enable_thinking": bool(self.enable_thinking)},
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

    def _message_text(self, response: dict) -> str:
        choices = response.get("choices") or []
        if not choices:
            raise ValueError("response has no choices")
        message = choices[0].get("message") or {}
        for key in ("content", "reasoning_content", "reasoning"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value
        # Some gateways put text on the choice itself
        for key in ("text", "content"):
            value = choices[0].get(key)
            if isinstance(value, str) and value.strip():
                return value
        raise ValueError(
            "response content is empty "
            f"(finish_reason={choices[0].get('finish_reason')!r}; "
            "Qwen3 可能把结果放在思考字段，请关闭 thinking 或提高 max_tokens)"
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
