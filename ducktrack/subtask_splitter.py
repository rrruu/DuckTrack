"""Model-based whole-trajectory sub-task splitting (Feature-2).

v1: text action summary only (subtask-split-v1 / qwen_subtask_v1)
v2: text summary + up to N keyframe screenshots (subtask-split-v2 / qwen_subtask_v2)
v3/v4: hierarchical coarse + fine split for long trajectories (subtask-split-v3 / qwen_subtask_v3)
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
PROMPT_VERSION_V3 = "subtask-split-v3"
SPLITTER_NAME_V1 = "qwen_subtask_v1"
SPLITTER_NAME_V2 = "qwen_subtask_v2"
SPLITTER_NAME_V3 = "qwen_subtask_v3"
CONFIDENCE_REVIEW_THRESHOLD = 0.7

# Legacy aliases (v2 default names kept for imports/tests)
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
(2) up to 16 keyframe screenshots, each labeled with its step index and action type.

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
   last_step_index. No gaps, no empty segments.
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

SYSTEM_PROMPT_COARSE = """You are a Windows GUI trajectory coarse segmentation model.
Given a compressed ordered list of GUI actions for a LONG recording, split the full trajectory
into a small number of contiguous HIGH-LEVEL chunks. Each chunk is one major user phase /
workflow stage (NOT one atomic click, NOT final sub-task instructions).

Rules:
1. Return ONLY a JSON array, no markdown or extra text.
2. Each item MUST be: {"start_step": int, "end_step": int, "theme": string}
3. theme is a short Chinese or English phrase describing the phase (e.g. "登录系统", "导出报表").
4. Ranges are inclusive, contiguous, non-overlapping, and MUST cover 0 through last_step_index.
5. Prefer about 4-10 chunks for long trajectories; merge trivial phases; do not create one-step chunks.
6. Place boundaries where the user's overall goal clearly changes (app switch, major task finished).

Example:
[
  {"start_step": 0, "end_step": 127, "theme": "打开浏览器并登录"},
  {"start_step": 128, "end_step": 263, "theme": "检索并导出数据"}
]
"""

SYSTEM_PROMPT_V3 = """You are a Windows GUI trajectory sub-task segmentation model with vision.
You will receive ONE chunk (a contiguous slice) of a longer recording:
(1) chunk_theme describing the overall phase,
(2) a compressed action summary with GLOBAL step indices inside this chunk only,
(3) up to 16 keyframe screenshots from this chunk.

Split ONLY this chunk into finer sub-tasks. Each sub-task is one user intent within the chunk.

Evidence priority:
1. Screenshot content is primary for writing instruction.
2. Action summary defines order and ranges.
3. chunk_theme provides context; do not contradict visible UI.

Rules:
1. Return ONLY a JSON array, no markdown or extra text.
2. Each item: {"start_step": int, "end_step": int, "instruction": string, "confidence": number}
3. start_step/end_step MUST stay within chunk_start .. chunk_end (global indices provided).
4. Ranges must be contiguous and cover the entire chunk with no gaps.
5. The first few steps may overlap the previous chunk. Split by actual user intent, not by the chunk edge.
6. instruction must be concrete (Chinese preferred). Avoid vague phrases.
7. Prefer fewer meaningful segments over one-step-per-segment.
8. confidence in [0, 1].
"""

# Back-compat name
SYSTEM_PROMPT = SYSTEM_PROMPT_V2


class SubtaskSplitError(ValueError):
    pass


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)) or default)
    except Exception:
        return default


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
        self.coarse_model = (
            os.environ.get("DUCKTRACK_SUBTASK_COARSE_MODEL", "").strip() or self.model
        )
        self.token = (
            os.environ.get("DUCKTRACK_SUBTASK_TOKEN", "").strip()
            or os.environ.get("DUCKTRACK_QWEN_TOKEN", "").strip()
        )
        self.max_tokens = int(
            os.environ.get(
                "DUCKTRACK_SUBTASK_MAX_TOKENS",
                os.environ.get("DUCKTRACK_QWEN_MAX_TOKENS", "8192"),
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
        self.vision_enabled = _env_flag("DUCKTRACK_SUBTASK_VISION", "1")
        self.max_images = max(0, _env_int("DUCKTRACK_SUBTASK_MAX_IMAGES", 16))
        self.max_image_edge = int(
            os.environ.get(
                "DUCKTRACK_SUBTASK_IMAGE_MAX_EDGE",
                os.environ.get("DUCKTRACK_QWEN_IMAGE_MAX_EDGE", "1024"),
            )
        )
        self.fallback_text = _env_flag("DUCKTRACK_SUBTASK_FALLBACK_TEXT", "1")
        self.hierarchical_enabled = _env_flag("DUCKTRACK_SUBTASK_HIERARCHICAL", "1")
        self.hier_threshold = max(1, _env_int("DUCKTRACK_SUBTASK_HIER_THRESHOLD", 150))
        self.chunk_target = max(20, _env_int("DUCKTRACK_SUBTASK_CHUNK_TARGET", 100))
        self.chunk_min = max(10, _env_int("DUCKTRACK_SUBTASK_CHUNK_MIN", 40))
        self.chunk_max = max(self.chunk_min, _env_int("DUCKTRACK_SUBTASK_CHUNK_MAX", 180))
        # Adjacent fine-cut windows share this many steps, then results are merged.
        self.chunk_overlap = max(0, _env_int("DUCKTRACK_SUBTASK_CHUNK_OVERLAP", 8))

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

        recording_path = Path(recording) if recording else None
        step_count = last + 1
        use_hierarchical = (
            self.hierarchical_enabled
            and step_count > self.hier_threshold
        )

        if use_hierarchical:
            result = self._split_hierarchical(payload, steps, last, recording_path)
        else:
            result = self._split_single_pass(payload, steps, last, recording_path)

        sub_tasks = result["sub_tasks"]
        errors = validate_sub_tasks(steps, sub_tasks)
        if errors:
            repaired = self._repair_sub_tasks(steps, sub_tasks, last)
            errors = validate_sub_tasks(steps, repaired)
            if errors:
                raise SubtaskSplitError("模型划分未通过校验: " + "; ".join(errors))
            sub_tasks = repaired
            result["sub_tasks"] = sub_tasks
            result["repaired"] = True

        if response_dir is not None:
            response_dir.mkdir(parents=True, exist_ok=True)
            (response_dir / "subtask_split.json").write_text(
                json.dumps(
                    {
                        **result.get("debug", {}),
                        "parsed_sub_tasks": sub_tasks,
                        "split_at_unix": time.time(),
                        "hierarchical": use_hierarchical,
                        "step_count": step_count,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

        apply_model_sub_tasks(
            payload,
            sub_tasks,
            splitter=result["splitter_name"],
            prompt_version=result["prompt_version"],
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

    def _split_single_pass(
        self,
        payload: dict,
        steps: list,
        last: int,
        recording_path: Path | None,
    ) -> dict:
        summary = self._build_summary(payload, last)
        keyframes: list[dict] = []
        use_vision = False
        if self.vision_enabled and self.max_images > 0 and recording_path is not None:
            keyframes = self._select_keyframes(
                steps, 0, last, recording_path,
            )
            use_vision = bool(keyframes)
        elif self.vision_enabled and self.max_images > 0 and recording_path is None:
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
        sub_tasks = self._parsed_to_sub_tasks(parsed, prompt_version)
        return {
            "sub_tasks": sub_tasks,
            "splitter_name": splitter_name,
            "prompt_version": prompt_version,
            "debug": {
                "model": self.model,
                "prompt_version": prompt_version,
                "splitter": splitter_name,
                "vision_enabled": use_vision,
                "keyframes": self._keyframes_debug(keyframes),
                "summary": summary,
                "raw": raw,
            },
        }

    def _split_hierarchical(
        self,
        payload: dict,
        steps: list,
        last: int,
        recording_path: Path | None,
    ) -> dict:
        coarse_chunks, coarse_raw = self._coarse_cut(payload, steps, last)
        chunk_runs: list[list[dict]] = []
        chunk_debug: list[dict] = []

        for chunk in coarse_chunks:
            c_start = int(chunk["start_step"])
            c_end = int(chunk["end_step"])
            theme = str(chunk.get("theme") or "").strip() or "本阶段操作"
            summary = self._build_summary(
                payload, c_end, start_step=c_start, chunk_theme=theme,
            )
            keyframes: list[dict] = []
            use_vision = False
            if self.vision_enabled and self.max_images > 0 and recording_path is not None:
                keyframes = self._select_keyframes(
                    steps, c_start, c_end, recording_path,
                )
                use_vision = bool(keyframes)
            elif not self.fallback_text and self.vision_enabled and recording_path is None:
                raise SubtaskSplitError(
                    "已启用看图划分但未提供录制目录（recording），无法读取截图"
                )

            if use_vision:
                system_prompt = SYSTEM_PROMPT_V3
            else:
                system_prompt = SYSTEM_PROMPT_V1

            parsed, raw = self._call_model(
                summary,
                c_end,
                system_prompt=system_prompt,
                keyframes=keyframes if use_vision else None,
                chunk_start=c_start,
                chunk_end=c_end,
                chunk_theme=theme,
            )
            chunk_tasks = self._parsed_to_sub_tasks(
                parsed,
                PROMPT_VERSION_V3 if use_vision else PROMPT_VERSION_V1,
                clamp_range=(c_start, c_end),
            )
            chunk_runs.append(chunk_tasks)
            chunk_debug.append({
                "chunk": chunk,
                "vision": use_vision,
                "keyframe_count": len(keyframes),
                "sub_task_count": len(chunk_tasks),
                "raw": raw,
            })

        all_sub_tasks, overlap_merges = self._merge_overlapping_chunk_tasks(
            coarse_chunks, chunk_runs, last,
        )
        for idx, task in enumerate(all_sub_tasks):
            task["index"] = idx

        return {
            "sub_tasks": all_sub_tasks,
            "splitter_name": SPLITTER_NAME_V3,
            "prompt_version": PROMPT_VERSION_V3,
            "debug": {
                "model": self.model,
                "coarse_model": self.coarse_model,
                "prompt_version": PROMPT_VERSION_V3,
                "splitter": SPLITTER_NAME_V3,
                "coarse_chunks": coarse_chunks,
                "chunk_overlap": self.chunk_overlap,
                "overlap_merges": overlap_merges,
                "coarse_raw": coarse_raw,
                "chunk_runs": chunk_debug,
                "vision_enabled": self.vision_enabled,
            },
        }

    def _coarse_cut(self, payload: dict, steps: list, last: int) -> tuple[list[dict], dict]:
        summary = self._build_summary(payload, last, compress=True)
        try:
            parsed, raw = self._call_model(
                summary,
                last,
                system_prompt=SYSTEM_PROMPT_COARSE,
                keyframes=None,
                model=self.coarse_model,
                expect_coarse=True,
            )
            chunks = self._normalize_coarse_chunks(parsed, last)
            return chunks, raw
        except Exception as exc:
            chunks = self._heuristic_coarse_chunks(last)
            return chunks, {"fallback": "heuristic", "error": str(exc)}

    def _heuristic_coarse_chunks(self, last: int) -> list[dict]:
        chunks: list[dict] = []
        start = 0
        while start <= last:
            end = min(start + self.chunk_target - 1, last)
            chunks.append({
                "start_step": start,
                "end_step": end,
                "theme": f"步骤 {start}-{end}",
            })
            start = end + 1
        return self._normalize_coarse_chunks(chunks, last)

    def _normalize_coarse_chunks(self, chunks: list, last: int) -> list[dict]:
        if not chunks:
            return self._heuristic_coarse_chunks(last)

        normalized: list[dict] = []
        for item in chunks:
            if not isinstance(item, dict):
                continue
            start = int(item.get("start_step", -1))
            end = int(item.get("end_step", -1))
            theme = str(item.get("theme") or item.get("instruction") or "").strip()
            if start < 0 or end < start:
                continue
            normalized.append({
                "start_step": max(0, min(start, last)),
                "end_step": max(0, min(end, last)),
                "theme": theme or f"步骤 {start}-{end}",
            })
        if not normalized:
            return self._heuristic_coarse_chunks(last)

        normalized.sort(key=lambda c: c["start_step"])
        merged: list[dict] = []
        for chunk in normalized:
            if not merged:
                merged.append(chunk)
                continue
            prev = merged[-1]
            if chunk["start_step"] <= prev["end_step"]:
                prev["end_step"] = max(prev["end_step"], chunk["end_step"])
                if len(chunk.get("theme", "")) > len(prev.get("theme", "")):
                    prev["theme"] = chunk["theme"]
            else:
                merged.append(chunk)

        # Fill gaps
        filled: list[dict] = []
        cursor = 0
        for chunk in merged:
            if chunk["start_step"] > cursor:
                filled.append({
                    "start_step": cursor,
                    "end_step": chunk["start_step"] - 1,
                    "theme": f"步骤 {cursor}-{chunk['start_step'] - 1}",
                })
            filled.append(chunk)
            cursor = chunk["end_step"] + 1
        if cursor <= last:
            filled.append({
                "start_step": cursor,
                "end_step": last,
                "theme": f"步骤 {cursor}-{last}",
            })

        # Merge too-small chunks
        compact: list[dict] = []
        for chunk in filled:
            if not compact:
                compact.append(dict(chunk))
                continue
            prev = compact[-1]
            prev_len = prev["end_step"] - prev["start_step"] + 1
            cur_len = chunk["end_step"] - chunk["start_step"] + 1
            if cur_len < self.chunk_min or prev_len < self.chunk_min:
                prev["end_step"] = chunk["end_step"]
                if chunk.get("theme"):
                    prev["theme"] = chunk["theme"]
            else:
                compact.append(dict(chunk))

        # Split too-large chunks
        final: list[dict] = []
        for chunk in compact:
            length = chunk["end_step"] - chunk["start_step"] + 1
            if length <= self.chunk_max:
                final.append(chunk)
                continue
            start = chunk["start_step"]
            end = chunk["end_step"]
            theme = chunk.get("theme", "")
            pos = start
            while pos <= end:
                sub_end = min(pos + self.chunk_target - 1, end)
                final.append({
                    "start_step": pos,
                    "end_step": sub_end,
                    "theme": theme,
                })
                pos = sub_end + 1

        # Ensure coverage starts at 0
        if final and final[0]["start_step"] > 0:
            final.insert(0, {
                "start_step": 0,
                "end_step": final[0]["start_step"] - 1,
                "theme": "开场",
            })
        if final and final[-1]["end_step"] != last:
            final[-1]["end_step"] = last

        return self._apply_chunk_overlap(final, last)

    def _apply_chunk_overlap(self, chunks: list[dict], last: int) -> list[dict]:
        """Keep abutting core ranges, then extend later chunks backward for fine-cut context."""
        if not chunks:
            return chunks
        overlap = min(self.chunk_overlap, max(0, self.chunk_target // 2))
        out: list[dict] = []
        for i, chunk in enumerate(chunks):
            core_start = int(chunk["start_step"])
            core_end = int(chunk["end_step"])
            start = core_start
            if i > 0 and overlap > 0:
                prev_core = int(out[i - 1]["core_start"])
                start = max(prev_core, core_start - overlap)
            out.append({
                "start_step": start,
                "end_step": core_end,
                "core_start": core_start,
                "core_end": core_end,
                "theme": chunk.get("theme") or "",
            })
        if out:
            out[-1]["end_step"] = last
            out[-1]["core_end"] = last
        return out

    def _overlap_score(self, tasks: list[dict], os: int, oe: int) -> tuple:
        """Higher is better: full cover, confidence, fewer fragments."""
        if not tasks or os > oe:
            return (-1, 0.0, 0)
        full = any(
            int(t.get("start_step", -1)) <= os and int(t.get("end_step", -1)) >= oe
            for t in tasks
        )
        confs = [float(t.get("confidence") or 0.0) for t in tasks]
        return (1 if full else 0, max(confs) if confs else 0.0, -len(tasks))

    def _merge_overlapping_chunk_tasks(
        self,
        chunks: list[dict],
        task_lists: list[list[dict]],
        last: int,
    ) -> tuple[list[dict], list[dict]]:
        """Paint exclusive cores, then assign overlap to the higher-scoring side."""
        if not task_lists:
            return [], []
        if len(task_lists) == 1 or self.chunk_overlap <= 0:
            merged: list[dict] = []
            for group in task_lists:
                merged.extend(dict(t) for t in group)
            merged.sort(key=lambda t: int(t.get("start_step", 0)))
            return merged, []

        ownership: list[dict | None] = [None] * (last + 1)
        decisions: list[dict] = []

        def paint(tasks: list[dict], lo: int, hi: int, overwrite: bool = False) -> None:
            if lo > hi:
                return
            for task in tasks:
                a = max(lo, int(task.get("start_step", -1)))
                b = min(hi, int(task.get("end_step", -1)))
                if a > b:
                    continue
                for step in range(a, b + 1):
                    if overwrite or ownership[step] is None:
                        ownership[step] = task

        for i, tasks in enumerate(task_lists):
            chunk = chunks[i]
            core_s = int(chunk.get("core_start", chunk["start_step"]))
            core_e = int(chunk.get("core_end", chunk["end_step"]))
            if i > 0:
                prev = chunks[i - 1]
                prev_core_e = int(prev.get("core_end", prev["end_step"]))
                core_s = max(core_s, prev_core_e + 1)
            paint(tasks, core_s, core_e, overwrite=False)

            if i == 0:
                continue
            prev_chunk = chunks[i - 1]
            os_ = int(chunk["start_step"])
            oe_ = int(prev_chunk.get("core_end", prev_chunk["end_step"]))
            if os_ > oe_:
                continue
            left_ol = [
                t for t in task_lists[i - 1]
                if int(t.get("end_step", -1)) >= os_ and int(t.get("start_step", 10**9)) <= oe_
            ]
            right_ol = [
                t for t in tasks
                if int(t.get("end_step", -1)) >= os_ and int(t.get("start_step", 10**9)) <= oe_
            ]
            left_score = self._overlap_score(left_ol, os_, oe_)
            right_score = self._overlap_score(right_ol, os_, oe_)
            use_right = right_score > left_score
            paint(right_ol if use_right else left_ol, os_, oe_, overwrite=True)
            decisions.append({
                "overlap_start": os_,
                "overlap_end": oe_,
                "winner": "right" if use_right else "left",
                "left_score": list(left_score),
                "right_score": list(right_score),
            })

        collapsed: list[dict] = []
        i = 0
        while i <= last:
            owner = ownership[i]
            j = i
            while j + 1 <= last and ownership[j + 1] is owner:
                j += 1
            if owner is not None:
                item = dict(owner)
                item["start_step"] = i
                item["end_step"] = j
                collapsed.append(item)
            i = j + 1
        return collapsed, decisions

    def _parsed_to_sub_tasks(
        self,
        parsed: list,
        prompt_version: str,
        *,
        clamp_range: tuple[int, int] | None = None,
    ) -> list[dict]:
        sub_tasks = []
        for index, item in enumerate(parsed):
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0.0))))
            instruction = str(item.get("instruction") or "").strip()
            if not instruction:
                raise SubtaskSplitError(f"模型返回的第 {index} 段 instruction 为空")
            start = int(item["start_step"])
            end = int(item["end_step"])
            if clamp_range is not None:
                c0, c1 = clamp_range
                start = max(c0, min(start, c1))
                end = max(c0, min(end, c1))
            sub_tasks.append(
                make_sub_task(
                    index=index,
                    instruction=instruction,
                    start_step=start,
                    end_step=end,
                    source="model",
                    confidence=confidence,
                    needs_review=confidence < CONFIDENCE_REVIEW_THRESHOLD,
                    model=self.model,
                    prompt_version=prompt_version,
                )
            )
        return sub_tasks

    def _repair_sub_tasks(self, steps: list, sub_tasks: list, last: int) -> list[dict]:
        """Best-effort fill small gaps so validate_sub_tasks can pass."""
        if not sub_tasks:
            return [make_sub_task(
                index=0,
                instruction="待复核：自动合并",
                start_step=0,
                end_step=last,
                source="model",
                confidence=0.3,
                needs_review=True,
                model=self.model,
                prompt_version=PROMPT_VERSION_V3,
            )]

        sorted_tasks = sorted(sub_tasks, key=lambda t: int(t.get("start_step", 0)))
        repaired: list[dict] = []
        expected = 0
        for pos, task in enumerate(sorted_tasks):
            start = int(task.get("start_step", -1))
            end = int(task.get("end_step", -1))
            if start > expected:
                repaired.append(make_sub_task(
                    index=len(repaired),
                    instruction="待复核：划分缺口自动补齐",
                    start_step=expected,
                    end_step=start - 1,
                    source="model",
                    confidence=0.25,
                    needs_review=True,
                    model=self.model,
                    prompt_version=PROMPT_VERSION_V3,
                ))
                expected = start
            if end < start:
                continue
            task = dict(task)
            task["index"] = len(repaired)
            if not str(task.get("instruction") or "").strip():
                task["instruction"] = "待复核：空意图自动补齐"
                task["needs_review"] = True
            repaired.append(task)
            expected = end + 1

        if expected <= last:
            repaired.append(make_sub_task(
                index=len(repaired),
                instruction="待复核：划分尾部自动补齐",
                start_step=expected,
                end_step=last,
                source="model",
                confidence=0.25,
                needs_review=True,
                model=self.model,
                prompt_version=PROMPT_VERSION_V3,
            ))

        for idx, task in enumerate(repaired):
            task["index"] = idx
        return repaired

    def _build_summary(
        self,
        payload: dict,
        last: int,
        *,
        start_step: int = 0,
        chunk_theme: str | None = None,
        compress: bool = False,
    ) -> dict:
        steps = payload.get("trajectory") or []
        start_step = max(0, min(start_step, last))
        items = []
        for index in range(start_step, last + 1):
            step = steps[index]
            actions = step.get("action") or []
            action0 = actions[0] if actions and isinstance(actions[0], dict) else {}
            params = action0.get("parameters") if isinstance(action0, dict) else {}
            ann = step.get("annotation") if isinstance(step.get("annotation"), dict) else {}
            compact_params = {}
            if isinstance(params, dict):
                for key in (
                    "text", "keys", "key", "clicks", "button", "duration",
                    "x", "y", "start_x", "start_y", "end_x", "end_y",
                ):
                    if key not in params:
                        continue
                    val = params[key]
                    if key == "text" and isinstance(val, str) and len(val) > 80:
                        val = val[:77] + "..."
                    if key in {"x", "y", "start_x", "start_y", "end_x", "end_y"} and isinstance(val, (int, float)):
                        val = round(float(val), 4)
                    compact_params[key] = val
            item = {
                "index": index,
                "action": action_name(step) or action0.get("name"),
            }
            if compact_params:
                item["parameters"] = compact_params
            if not compress:
                hint = (
                    step.get("step_instruction")
                    or ann.get("step_instruction")
                    or ann.get("intent")
                    or step.get("rationale")
                    or ""
                )
                if isinstance(hint, str) and hint.strip():
                    item["hint"] = hint.strip()[:120]
            items.append(item)

        if compress:
            items = self._compress_summary_items(items)

        summary = {
            "query": payload.get("query") or "",
            "last_step_index": last,
            "steps": items,
        }
        if start_step > 0:
            summary["chunk_start"] = start_step
        if chunk_theme:
            summary["chunk_theme"] = chunk_theme
        return summary

    def _compress_summary_items(self, items: list[dict]) -> list[dict]:
        if not items:
            return []
        compressed: list[dict] = []
        i = 0
        while i < len(items):
            item = items[i]
            action = item.get("action")
            if action == "type":
                texts = []
                start_idx = item["index"]
                j = i
                while j < len(items) and items[j].get("action") == "type":
                    p = items[j].get("parameters") or {}
                    t = p.get("text") if isinstance(p, dict) else None
                    if isinstance(t, str):
                        texts.append(t)
                    j += 1
                merged_text = "".join(texts)
                if len(merged_text) > 100:
                    merged_text = merged_text[:97] + "..."
                count = j - i
                entry = {"index": start_idx, "action": "type" if count == 1 else f"type×{count}"}
                if merged_text:
                    entry["parameters"] = {"text": merged_text}
                compressed.append(entry)
                i = j
                continue
            compressed.append(item)
            i += 1
        return compressed

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
        if n in {"hover", "moveto", "move_to"}:
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

    def _select_keyframes(
        self,
        steps: list,
        start: int,
        end: int,
        recording: Path,
    ) -> list[dict]:
        """Pick up to max_images frames within [start, end]."""
        if self.max_images <= 0 or end < start:
            return []

        candidates: dict[int, str] = {}

        def add_candidate(index: int, role: str) -> None:
            if index < start or index > end:
                return
            prev = candidates.get(index)
            if prev == "boundary":
                return
            candidates[index] = role

        add_candidate(start, "boundary")
        add_candidate(end, "boundary")

        prev_family = None
        for index in range(start, end + 1):
            name = action_name(steps[index])
            family = self._action_family(name)
            if name in {"hotkey", "hover"}:
                add_candidate(index, "boundary")
            if prev_family is not None and family != prev_family:
                add_candidate(index, "boundary")
                if index - 1 >= start:
                    add_candidate(index - 1, "boundary")
            if family in {"type", "drag"} and prev_family != family:
                add_candidate(index, "boundary")
            prev_family = family

        span_len = end - start
        if span_len == 0:
            span = [start]
        else:
            want = max(self.max_images, 1)
            span = sorted(
                {start + int(round(i * span_len / max(want - 1, 1))) for i in range(want)}
            )
        for index in span:
            add_candidate(index, "representative")

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
            key = str(path)
            if key in seen_files:
                continue
            seen_files.add(key)
            try:
                data_url = self._image_data_url(path)
            except Exception:
                continue
            selected.append({
                "step": index,
                "action": action_name(steps[index]),
                "role": role,
                "relpath": relpath,
                "data_url": data_url,
            })

        selected.sort(key=lambda item: item["step"])
        return selected

    def _keyframes_debug(self, keyframes: list[dict]) -> list[dict]:
        return [
            {
                "step": k["step"],
                "action": k["action"],
                "role": k["role"],
                "relpath": k.get("relpath"),
            }
            for k in keyframes
        ]

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
        model: str | None = None,
        chunk_start: int | None = None,
        chunk_end: int | None = None,
        chunk_theme: str | None = None,
        expect_coarse: bool = False,
    ) -> tuple[list, dict]:
        header_lines = [f"last_step_index={last}"]
        if chunk_start is not None and chunk_end is not None:
            header_lines.append(f"chunk_start={chunk_start}")
            header_lines.append(f"chunk_end={chunk_end}")
        if chunk_theme:
            header_lines.append(f"chunk_theme={chunk_theme}")

        if keyframes:
            content: list[dict] = [
                {
                    "type": "text",
                    "text": (
                        "\n".join(header_lines) + "\n"
                        f"max_images={len(keyframes)}\n"
                        + (
                            "Split this chunk into sub-tasks.\n"
                            if chunk_start is not None
                            else "Split the following trajectory into sub-tasks.\n"
                        )
                        + "Use BOTH the JSON summary and the attached keyframe screenshots.\n"
                        + "JSON summary:\n"
                        + json.dumps(summary, ensure_ascii=False)
                    ),
                }
            ]
            for frame in keyframes:
                content.append({
                    "type": "text",
                    "text": (
                        f"Keyframe: step={frame['step']}, "
                        f"action={frame.get('action') or 'unknown'}, "
                        f"role={frame.get('role') or 'representative'}"
                    ),
                })
                content.append({
                    "type": "image_url",
                    "image_url": {"url": frame["data_url"]},
                })
            user_message: dict = {"role": "user", "content": content}
        else:
            task = (
                "Split into coarse high-level chunks.\n"
                if expect_coarse
                else "Split the following trajectory into sub-tasks.\n"
            )
            user_message = {
                "role": "user",
                "content": "\n".join(header_lines) + "\n" + task + json.dumps(summary, ensure_ascii=False),
            }

        messages = [
            {"role": "system", "content": system_prompt},
            user_message,
        ]
        attempts = []
        last_error = None
        for attempt in range(1, self.max_attempts + 1):
            if attempt > 1:
                hint = (
                    "Return ONLY a JSON array of chunks covering 0..{last} with theme fields."
                    if expect_coarse
                    else f"Return ONLY a JSON array covering the required range through {last} with non-empty instructions."
                )
                messages.append({"role": "user", "content": f"Previous response was invalid. {hint}"})
            response = self._chat(messages, model=model)
            attempts.append(response)
            try:
                if expect_coarse:
                    parsed = self._parse_coarse_array(response)
                else:
                    parsed = self._parse_array(response)
                return parsed, {
                    "model": model or self.model,
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

    def _chat(self, messages: list[dict], *, model: str | None = None) -> dict:
        request_payload: dict = {
            "model": model or self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": self.max_tokens,
        }
        if self.enable_thinking:
            request_payload["enable_thinking"] = True
            request_payload["chat_template_kwargs"] = {"enable_thinking": True}

        body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

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

    def _parse_coarse_array(self, response: dict) -> list:
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
            theme = str(item.get("theme") or item.get("instruction") or "").strip()
            if not theme:
                raise ValueError("array item missing theme")
            item["theme"] = theme
        return parsed
