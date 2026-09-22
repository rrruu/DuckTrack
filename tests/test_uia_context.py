"""Unit tests for v5-C UI Automation context."""

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

from ducktrack.recorder import Recorder  # noqa: E402
from ducktrack.uia_context import (  # noqa: E402
    UIA_SAMPLE_ACTIONS,
    capture_uia_at_point,
    control_type_name,
    maybe_attach_uia_to_event,
)


def _evt(seq: int, action: str, ts: float, **kwargs) -> dict:
    return {
        "event_id": f"evt_test_{seq:04d}",
        "event_seq": seq,
        "time_stamp": ts,
        "action": action,
        **kwargs,
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


class UiaHelperTests(unittest.TestCase):
    def test_control_type_name_known(self) -> None:
        self.assertEqual(control_type_name(50000), "Button")
        self.assertEqual(control_type_name(50004), "Edit")

    def test_control_type_name_unknown(self) -> None:
        self.assertEqual(control_type_name(99999), "ControlType_99999")

    def test_maybe_attach_skips_when_disabled(self) -> None:
        event = {"action": "left_click", "x": 10, "y": 20}
        with mock.patch("ducktrack.uia_context.capture_uia_at_point") as mocked:
            maybe_attach_uia_to_event(event, enabled=False)
            mocked.assert_not_called()
            self.assertNotIn("context", event)

    def test_maybe_attach_only_on_click_actions(self) -> None:
        uia = {
            "control_type": "Button",
            "automation_id": "saveBtn",
            "name": "保存",
            "bounding_rectangle": {"left": 1, "top": 2, "right": 3, "bottom": 4},
        }
        with mock.patch("ducktrack.uia_context.capture_uia_at_point", return_value=uia):
            click = {"action": "left_click", "x": 10, "y": 20, "context": {"process_name": "x.exe"}}
            maybe_attach_uia_to_event(click, enabled=True)
            self.assertEqual(click["context"]["uia"], uia)

            scroll = {"action": "scroll", "x": 10, "y": 20}
            maybe_attach_uia_to_event(scroll, enabled=True)
            self.assertNotIn("context", scroll)

    def test_maybe_attach_creates_context_when_missing(self) -> None:
        uia = {"control_type": "Pane", "bounding_rectangle": {"left": 0, "top": 0, "right": 1, "bottom": 1}}
        event = {"action": "right_click", "x": 5, "y": 6}
        with mock.patch("ducktrack.uia_context.capture_uia_at_point", return_value=uia):
            maybe_attach_uia_to_event(event, enabled=True)
        self.assertEqual(event["context"]["uia"], uia)

    def test_sample_actions_cover_clicks_only(self) -> None:
        self.assertIn("left_click", UIA_SAMPLE_ACTIONS)
        self.assertIn("double_left_click", UIA_SAMPLE_ACTIONS)
        self.assertNotIn("scroll", UIA_SAMPLE_ACTIONS)
        self.assertNotIn("drag_end", UIA_SAMPLE_ACTIONS)


class UiaExportTests(unittest.TestCase):
    def _export(self, events: list[dict]):
        with tempfile.TemporaryDirectory() as tmp:
            recording_path = os.path.join(tmp, "recording-test")
            os.makedirs(recording_path)
            with open(os.path.join(recording_path, "events.jsonl"), "w", encoding="utf-8") as f:
                for event in events:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")

            recorder = Recorder(False, save_screenshots=False, recordings_dir=tmp)
            recorder.recording_path = recording_path
            recorder._step_ids_enabled = True
            recorder._foreground_context_enabled = False
            recorder._uia_enabled = True
            recorder.metadata_manager.metadata.update(_default_metadata())
            recorder._export_trajectory_json()
            try:
                recorder.events_file.close()
            except Exception:
                pass

            with open(os.path.join(recording_path, "trajectory.json"), encoding="utf-8") as f:
                return json.load(f)

    def test_trajectory_step_inherits_uia_from_click_event(self) -> None:
        uia = {
            "control_type": "Button",
            "automation_id": "ok",
            "name": "确定",
            "bounding_rectangle": {"left": 10, "top": 20, "right": 110, "bottom": 60},
        }
        events = [
            _evt(
                0,
                "left_click",
                1000.0,
                x=50,
                y=40,
                button="left",
                context={"process_name": "app.exe", "uia": uia},
            ),
        ]
        doc = self._export(events)
        step = doc["trajectory"][0]
        self.assertEqual(step["context"]["uia"], uia)
        self.assertEqual(step["context"]["process_name"], "app.exe")

    def test_recorder_wires_uia_on_capture(self) -> None:
        uia = {
            "control_type": "Edit",
            "name": "Search",
            "bounding_rectangle": {"left": 0, "top": 0, "right": 100, "bottom": 30},
        }
        recorder = Recorder(False, save_screenshots=False)
        try:
            recorder._foreground_context_enabled = False
            recorder._uia_enabled = True
            with mock.patch("ducktrack.uia_context.capture_uia_at_point", return_value=uia):
                event = {"action": "left_click", "x": 12, "y": 34, "time_stamp": 1.0}
                recorder._maybe_attach_uia(event)
                self.assertEqual(event["context"]["uia"], uia)
        finally:
            try:
                recorder.events_file.close()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
