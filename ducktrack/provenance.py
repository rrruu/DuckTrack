"""v5-A provenance helpers: stable event/step IDs and env toggles."""

from __future__ import annotations

import os
import uuid


def env_enabled(name: str, default: bool = True) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.lower() in {"1", "true", "yes", "on"}


def new_event_id() -> str:
    return f"evt_{uuid.uuid4().hex}"


def new_step_id() -> str:
    return f"stp_{uuid.uuid4().hex}"


def provenance_from_events(events: list[dict]) -> dict:
    """Build source_event_ids / source_event_seq from raw events."""
    ids = [e["event_id"] for e in events if e.get("event_id")]
    seqs = [e["event_seq"] for e in events if e.get("event_seq") is not None]
    out: dict = {}
    if ids:
        out["source_event_ids"] = ids
    if seqs and len(seqs) == len(ids):
        out["source_event_seq"] = seqs
    return out


def attach_step_provenance(
    step: dict,
    *,
    merge_rule: str,
    source_events: list[dict] | None = None,
    enabled: bool = True,
) -> dict:
    if not enabled:
        return step
    step["step_id"] = new_step_id()
    step["merge_rule"] = merge_rule
    if source_events:
        step.update(provenance_from_events(source_events))
    else:
        step.setdefault("source_event_ids", [])
    return step


def assign_step_seqs(trajectory: list[dict] | None, *, enabled: bool = True) -> None:
    """
    Write step_seq = 0..N-1 in export order (same as trajectory array index).

    Call once after the full trajectory list is built. Mirrors event_seq for steps.
    """
    if not enabled:
        return
    for idx, step in enumerate(trajectory or []):
        step["step_seq"] = idx
