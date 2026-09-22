"""Unit tests for v5-B foreground context helpers and export wiring."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ducktrack.foreground_context import (  # noqa: E402
    compute_primary_application,
    pick_context_from_events,
)
from ducktrack.recorder import Recorder  # noqa: E402


def _evt(seq: int, action: str, ts: float, **kwargs) -> dict:
    return {
        "event_id": f"evt_test_{seq:04d}",
        "event_seq": seq,
        "time_stamp": ts,
        "action": action,
        **kwargs,
    }


def _ctx(process_name: str, title: str = "Win") -> dict:
    return {
        "process_name": process_name,
        "window_title": title,
        "window_rect": {"left": 0, "top": 0, "right": 800, "bottom": 600},
        "dpi_scale": 1.0,
    }


def _default_metadata() -> dict:
    return {
        "system": "Windows",
        "screen_width": 1920,
        "screen_height": 1080,
        "monitors": [
            {
                "id": "display-0",
                "x": 0,
                "y": 0,
                "width": 1920,
                "height": 1080,
                "primary": True,
            }
        ],
        "virtual_desktop": {"x": 0, "y": 0, "width": 1920, "height": 1080},
    }


class ForegroundHelperTests(unittest.TestCase):
    def test_pick_prefers_last_nonempty_context(self) -> None:
        events = [
            {"context": _ctx("a.exe", "A")},
            {"action": "drag_move"},
            {"context": _ctx("b.exe", "B")},
        ]
        picked = pick_context_from_events(events)
        self.assertEqual(picked["process_name"], "b.exe")
        self.assertEqual(picked["window_title"], "B")

    def test_pick_falls_back_to_first(self) -> None:
        events = [
            {"context": _ctx("only.exe")},
            {"action": "drag_move"},
        ]
        self.assertEqual(pick_context_from_events(events)["process_name"], "only.exe")

    def test_primary_application_majority_vote(self) -> None:
        traj = [
            {"action": [{"name": "click"}], "context": _ctx("notepad.exe", "N1")},
            {"action": [{"name": "type"}], "context": _ctx("notepad.exe", "N2")},
            {"action": [{"name": "click"}], "context": _ctx("explorer.exe", "E")},
            {"action": [{"name": "terminate"}], "context": _ctx("explorer.exe", "E2")},
        ]
        primary = compute_primary_application(traj)
        self.assertEqual(primary["process_name"], "notepad.exe")
        self.assertEqual(primary["window_title"], "N1")


class ForegroundExportTests(unittest.TestCase):
    def _export(self, events: list[dict]):
        with tempfile.TemporaryDirectory() as tmp:
            recording_path = os.path.join(tmp, "recording-test")
            os.makedirs(recording_path)
            with open(os.path.join(recording_path, "events.jsonl"), "w", encoding="utf-8") as f:
                for event in events:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")

            recorder = Recorder(False, save_screenshots=False, recordings_dir=tmp)
            recorder.recording_path = recording_path
            recorder._event_ids_enabled = True
            recorder._step_ids_enabled = True
            recorder._provenance_enabled = True
            recorder._foreground_context_enabled = True
            recorder.metadata_manager.metadata.update(_default_metadata())
            recorder._export_trajectory_json()
            try:
                recorder.events_file.close()
            except Exception:
                pass

            with open(os.path.join(recording_path, "trajectory.json"), encoding="utf-8") as f:
                return json.load(f)

    def test_step_inherits_event_context(self) -> None:
        events = [
            _evt(
                0,
                "left_click",
                1000.0,
                x=10,
                y=10,
                button="left",
                context=_ctx("notepad.exe", "无标题 - Notepad"),
            ),
        ]
        doc = self._export(events)
        step = doc["trajectory"][0]
        self.assertEqual(step["context"]["process_name"], "notepad.exe")
        self.assertEqual(step["context"]["window_title"], "无标题 - Notepad")
        self.assertEqual(doc["schema_version"], "ducktrack-trajectory-v5")
        self.assertIn("host_name", doc)
        self.assertEqual(doc["primary_application"]["process_name"], "notepad.exe")
        self.assertEqual(doc["application"], "notepad.exe")

    def test_type_merge_uses_first_then_last_context(self) -> None:
        events = [
            _evt(0, "key_input", 1000.0, text="a", context=_ctx("notepad.exe", "A")),
            _evt(1, "key_input", 1000.1, text="b", context=_ctx("notepad.exe", "B")),
        ]
        doc = self._export(events)
        step = [s for s in doc["trajectory"] if s["action"][0]["name"] == "type"][0]
        # Prefer last non-empty context among sources.
        self.assertEqual(step["context"]["window_title"], "B")

    def test_drag_compose_prefers_drag_end_context(self) -> None:
        events = [
            _evt(
                0,
                "drag_start",
                1000.0,
                button="left",
                start_x=1,
                start_y=1,
                x=1,
                y=1,
                context=_ctx("notepad.exe", "start"),
            ),
            _evt(1, "drag_move", 1000.05, button="left", x=2, y=2),
            _evt(
                2,
                "drag_end",
                1000.2,
                button="left",
                x=3,
                y=3,
                context=_ctx("notepad.exe", "end"),
            ),
        ]
        doc = self._export(events)
        step = [s for s in doc["trajectory"] if s["action"][0]["name"] == "dragTo"][0]
        self.assertEqual(step["context"]["window_title"], "end")

    def test_capture_skipped_for_drag_move(self) -> None:
        recorder = Recorder(False, save_screenshots=False)
        try:
            recorder._foreground_context_enabled = True
            with mock.patch(
                "ducktrack.recorder.capture_foreground_context",
                return_value=_ctx("x.exe"),
            ) as mocked:
                move = {"action": "drag_move", "x": 1, "y": 1}
                recorder._maybe_attach_foreground_context(move)
                self.assertIsNone(move.get("context"))
                mocked.assert_not_called()

                click = {"action": "left_click", "x": 1, "y": 1}
                recorder._maybe_attach_foreground_context(click)
                self.assertEqual(click["context"]["process_name"], "x.exe")
                mocked.assert_called_once()
        finally:
            try:
                recorder.events_file.close()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
