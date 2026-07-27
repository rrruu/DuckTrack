"""Shared sub-task schema helpers for Feature-1 (manual) and Feature-2 (model, later)."""

from __future__ import annotations

import time
import uuid
from typing import Any


VALID_SOURCES = frozenset({"manual", "model", "hybrid"})
VALID_STATUS = frozenset(
    {"none", "manual_complete", "completed", "skipped_manual", "failed"}
)


def new_sub_task_id() -> str:
    return f"st_{uuid.uuid4().hex}"


def make_sub_task(
    *,
    id: str | None = None,
    index: int = 0,
    instruction: str = "",
    start_step: int = 0,
    end_step: int = 0,
    source: str = "manual",
    confidence: float | None = None,
    needs_review: bool | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
) -> dict[str, Any]:
    text = instruction if isinstance(instruction, str) else ""
    if needs_review is None:
        needs_review = not bool(text.strip())
    if source not in VALID_SOURCES:
        source = "manual"
    return {
        "id": id or new_sub_task_id(),
        "index": int(index),
        "instruction": text,
        "start_step": int(start_step),
        "end_step": int(end_step),
        "source": source,
        "confidence": confidence,
        "needs_review": bool(needs_review),
        "model": model,
        "prompt_version": prompt_version,
    }


def make_sub_task_metadata(
    *,
    status: str = "none",
    splitter: str = "none",
    manual_boundary_count: int = 0,
    split_at_unix: float | None = None,
    prompt_version: str | None = None,
    previous_sub_tasks: list | None = None,
) -> dict[str, Any]:
    if status not in VALID_STATUS:
        status = "none"
    return {
        "status": status,
        "splitter": splitter,
        "manual_boundary_count": int(manual_boundary_count),
        "split_at_unix": split_at_unix,
        "prompt_version": prompt_version,
        "previous_sub_tasks": previous_sub_tasks,
    }


def action_name(step: dict) -> str:
    actions = step.get("action") or []
    if actions and isinstance(actions[0], dict):
        return str(actions[0].get("name") or "")
    return ""


def last_actionable_step_index(steps: list) -> int:
    """Last index that is not terminate; -1 if none."""
    for i in range(len(steps) - 1, -1, -1):
        if action_name(steps[i]) != "terminate":
            return i
    return -1


def validate_sub_tasks(steps: list, sub_tasks: list) -> list[str]:
    """Return list of validation error strings (empty = ok)."""
    errors: list[str] = []
    if not isinstance(sub_tasks, list):
        return ["sub_tasks must be a list"]
    last = last_actionable_step_index(steps if isinstance(steps, list) else [])
    if last < 0:
        if sub_tasks:
            errors.append("no actionable steps but sub_tasks is non-empty")
        return errors

    sorted_tasks = sorted(
        (t for t in sub_tasks if isinstance(t, dict)),
        key=lambda t: int(t.get("index", 0)),
    )
    if len(sorted_tasks) != len(sub_tasks):
        errors.append("sub_tasks contains non-object entries")

    expected = 0
    for pos, task in enumerate(sorted_tasks):
        start = int(task.get("start_step", -1))
        end = int(task.get("end_step", -1))
        if start < 0 or end < start:
            errors.append(f"sub_task[{pos}] invalid range {start}..{end}")
            continue
        if end > last:
            errors.append(f"sub_task[{pos}] end_step {end} exceeds last actionable {last}")
        if start != expected:
            errors.append(
                f"sub_task[{pos}] expected start_step={expected}, got {start}"
            )
        expected = end + 1
        if not task.get("id"):
            errors.append(f"sub_task[{pos}] missing id")
        src = task.get("source")
        if src not in VALID_SOURCES:
            errors.append(f"sub_task[{pos}] invalid source {src!r}")

    if sorted_tasks and expected != last + 1:
        errors.append(
            f"sub_tasks do not cover all actionable steps "
            f"(covered through {expected - 1}, last={last})"
        )
    return errors


def build_covering_sub_tasks(
    steps: list,
    *,
    ranges: list[dict] | None = None,
    manual_boundary_count: int = 0,
    initial_id: str | None = None,
) -> tuple[list[dict], dict]:
    """
    Build sub_tasks + metadata.

    ranges: list of {id, index, instruction, start_step}; end_step derived
    from the next range / last actionable step.
    If ranges is None/empty, create a single covering segment.
    """
    last = last_actionable_step_index(steps)
    if last < 0:
        meta = make_sub_task_metadata(
            status="none",
            splitter="none",
            manual_boundary_count=int(manual_boundary_count),
        )
        return [], meta

    if not ranges:
        task = make_sub_task(
            id=initial_id or new_sub_task_id(),
            index=0,
            instruction="",
            start_step=0,
            end_step=last,
            source="manual",
        )
        meta = make_sub_task_metadata(
            status="none",
            splitter="none",
            manual_boundary_count=0,
        )
        return [task], meta

    ordered = sorted(ranges, key=lambda r: int(r.get("index", 0)))
    tasks: list[dict] = []
    for i, item in enumerate(ordered):
        start = int(item.get("start_step", 0))
        if i + 1 < len(ordered):
            end = int(ordered[i + 1].get("start_step", start)) - 1
        else:
            end = last
        if end < start:
            continue
        tasks.append(
            make_sub_task(
                id=item.get("id") or new_sub_task_id(),
                index=int(item.get("index", i)),
                instruction=item.get("instruction") or "",
                start_step=start,
                end_step=end,
                source="manual",
            )
        )

    if not tasks:
        return build_covering_sub_tasks(
            steps, ranges=None, manual_boundary_count=0, initial_id=initial_id
        )

    tasks[0]["start_step"] = 0
    tasks[-1]["end_step"] = last
    for i in range(len(tasks) - 1):
        tasks[i]["end_step"] = max(
            tasks[i]["start_step"], tasks[i + 1]["start_step"] - 1
        )
        if tasks[i]["end_step"] < tasks[i]["start_step"]:
            tasks[i]["end_step"] = tasks[i]["start_step"]
        tasks[i + 1]["start_step"] = tasks[i]["end_step"] + 1
    tasks[-1]["end_step"] = last

    for i, task in enumerate(tasks):
        task["index"] = i

    count = int(manual_boundary_count)
    meta = make_sub_task_metadata(
        status="manual_complete" if count > 0 else "none",
        splitter="manual" if count > 0 else "none",
        manual_boundary_count=count,
        split_at_unix=time.time() if count > 0 else None,
    )
    return tasks, meta


def normalize_trajectory_subtasks(payload: dict) -> dict:
    """Ensure payload has sub_tasks / sub_task_metadata; mutate and return payload."""
    if not isinstance(payload, dict):
        return payload
    steps = payload.get("trajectory")
    if not isinstance(steps, list):
        steps = []

    raw_tasks = payload.get("sub_tasks")
    if not isinstance(raw_tasks, list):
        raw_tasks = []

    normalized: list[dict] = []
    for i, item in enumerate(raw_tasks):
        if not isinstance(item, dict):
            continue
        normalized.append(
            make_sub_task(
                id=item.get("id"),
                index=int(item.get("index", i)),
                instruction=item.get("instruction") or "",
                start_step=int(item.get("start_step", 0)),
                end_step=int(item.get("end_step", 0)),
                source=item.get("source") or "manual",
                confidence=item.get("confidence"),
                needs_review=item.get("needs_review"),
                model=item.get("model"),
                prompt_version=item.get("prompt_version"),
            )
        )

    meta_raw = payload.get("sub_task_metadata")
    if not isinstance(meta_raw, dict):
        meta_raw = {}

    if not normalized and steps:
        normalized, meta = build_covering_sub_tasks(steps, ranges=None)
        payload["sub_tasks"] = normalized
        payload["sub_task_metadata"] = make_sub_task_metadata(
            status=meta_raw.get("status") or meta["status"],
            splitter=meta_raw.get("splitter") or meta["splitter"],
            manual_boundary_count=int(
                meta_raw.get("manual_boundary_count", meta["manual_boundary_count"]) or 0
            ),
            split_at_unix=meta_raw.get("split_at_unix", meta.get("split_at_unix")),
            prompt_version=meta_raw.get("prompt_version"),
            previous_sub_tasks=meta_raw.get("previous_sub_tasks"),
        )
        return payload

    payload["sub_tasks"] = normalized
    payload["sub_task_metadata"] = make_sub_task_metadata(
        status=meta_raw.get("status") or ("manual_complete" if normalized else "none"),
        splitter=meta_raw.get("splitter") or "none",
        manual_boundary_count=int(meta_raw.get("manual_boundary_count") or 0),
        split_at_unix=meta_raw.get("split_at_unix"),
        prompt_version=meta_raw.get("prompt_version"),
        previous_sub_tasks=meta_raw.get("previous_sub_tasks"),
    )
    return payload


def rebuild_after_step_delete(sub_tasks: list, deleted_index: int) -> list[dict]:
    """Adjust ranges after removing trajectory[deleted_index]. Drops empty segments."""
    if not isinstance(sub_tasks, list):
        return []
    result: list[dict] = []
    for item in sub_tasks:
        if not isinstance(item, dict):
            continue
        start = int(item.get("start_step", 0))
        end = int(item.get("end_step", 0))
        if end < deleted_index:
            pass
        elif start > deleted_index:
            start -= 1
            end -= 1
        else:
            end -= 1
            if start > end:
                continue
        result.append(
            make_sub_task(
                id=item.get("id"),
                index=item.get("index", 0),
                instruction=item.get("instruction") or "",
                start_step=start,
                end_step=end,
                source=item.get("source") or "manual",
                confidence=item.get("confidence"),
                needs_review=item.get("needs_review"),
                model=item.get("model"),
                prompt_version=item.get("prompt_version"),
            )
        )
    for i, task in enumerate(result):
        task["index"] = i
    return result


def split_at_step(sub_tasks: list, step_index: int) -> list[dict]:
    """Split so a new sub-task starts at step_index."""
    if not isinstance(sub_tasks, list) or not sub_tasks:
        return list(sub_tasks or [])
    out: list[dict] = []
    for item in sub_tasks:
        if not isinstance(item, dict):
            continue
        start = int(item["start_step"])
        end = int(item["end_step"])
        if step_index <= start or step_index > end:
            out.append(dict(item))
            continue
        left = make_sub_task(
            id=item.get("id"),
            index=0,
            instruction=item.get("instruction") or "",
            start_step=start,
            end_step=step_index - 1,
            source="manual",
            confidence=item.get("confidence"),
            needs_review=item.get("needs_review"),
            model=None,
            prompt_version=None,
        )
        right = make_sub_task(
            id=new_sub_task_id(),
            index=0,
            instruction="",
            start_step=step_index,
            end_step=end,
            source="manual",
        )
        out.append(left)
        out.append(right)
    for i, task in enumerate(out):
        task["index"] = i
    return out


def merge_with_previous(sub_tasks: list, task_index: int) -> list[dict]:
    """Merge sub_tasks[task_index] into the previous segment."""
    if not isinstance(sub_tasks, list) or task_index <= 0 or task_index >= len(sub_tasks):
        return list(sub_tasks or [])
    out = [dict(t) for t in sub_tasks if isinstance(t, dict)]
    prev = out[task_index - 1]
    cur = out[task_index]
    instruction = (prev.get("instruction") or cur.get("instruction") or "")
    merged = make_sub_task(
        id=prev.get("id"),
        index=0,
        instruction=instruction,
        start_step=int(prev["start_step"]),
        end_step=int(cur["end_step"]),
        source="manual",
        confidence=None,
        needs_review=not bool(instruction.strip()),
        model=None,
        prompt_version=None,
    )
    out = out[: task_index - 1] + [merged] + out[task_index + 1 :]
    for i, task in enumerate(out):
        task["index"] = i
    return out


def find_sub_task_for_step(sub_tasks: list, step_index: int) -> dict | None:
    for item in sub_tasks or []:
        if not isinstance(item, dict):
            continue
        if int(item.get("start_step", -1)) <= step_index <= int(item.get("end_step", -1)):
            return item
    return None
