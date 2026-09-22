"""Unit tests for v5-A event/step provenance (no live recording)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ducktrack.recorder import Recorder  # noqa: E402


def _evt(seq: int, action: str, ts: float, **kwargs) -> dict:
    eid = f"evt_test_{seq:04d}"
    return {
        "event_id": eid,
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


class ProvenanceExportTests(unittest.TestCase):
    def _export(self, events: list[dict], metadata: dict | None = None):
        with tempfile.TemporaryDirectory() as tmp:
            recording_path = os.path.join(tmp, "recording-test")
            os.makedirs(recording_path)
            events_path = os.path.join(recording_path, "events.jsonl")
            with open(events_path, "w", encoding="utf-8") as f:
                for event in events:
                    f.write(json.dumps(event, ensure_ascii=False) + "\n")

            recorder = Recorder(False, save_screenshots=False, recordings_dir=tmp)
            recorder.recording_path = recording_path
            recorder._event_ids_enabled = True
            recorder._step_ids_enabled = True
            recorder._provenance_enabled = True
            recorder.metadata_manager.metadata.update(metadata or _default_metadata())
            recorder._export_trajectory_json()
            try:
                recorder.events_file.close()
            except Exception:
                pass

            with open(os.path.join(recording_path, "trajectory.json"), encoding="utf-8") as f:
                trajectory_doc = json.load(f)
            provenance_path = os.path.join(recording_path, "provenance.json")
            provenance_doc = None
            if os.path.exists(provenance_path):
                with open(provenance_path, encoding="utf-8") as f:
                    provenance_doc = json.load(f)
            return trajectory_doc, provenance_doc

    def _action_steps(self, trajectory_doc: dict) -> list[dict]:
        steps = trajectory_doc["trajectory"]
        return [s for s in steps if (s.get("action") or [{}])[0].get("name") != "terminate"]

    def test_direct_click_has_single_source(self) -> None:
        events = [
            _evt(0, "left_click", 1000.0, x=100, y=200, button="left"),
        ]
        doc, prov = self._export(events)
        steps = self._action_steps(doc)
        self.assertEqual(len(steps), 1)
        step = steps[0]
        self.assertEqual(step["action"][0]["name"], "click")
        self.assertEqual(step["merge_rule"], "direct")
        self.assertEqual(step["source_event_ids"], ["evt_test_0000"])
        self.assertEqual(step["source_event_seq"], [0])
        self.assertEqual(step["step_seq"], 0)
        self.assertTrue(step["step_id"].startswith("stp_"))
        self.assertIsNotNone(prov)
        self.assertEqual(prov["steps"][0]["source_event_ids"], ["evt_test_0000"])
        self.assertEqual(prov["steps"][0]["step_seq"], 0)

    def test_type_buffer_merges_key_inputs(self) -> None:
        events = [
            _evt(0, "key_input", 1000.0, text="f"),
            _evt(1, "key_input", 1000.1, text="i"),
            _evt(2, "key_input", 1000.2, text="r"),
            _evt(3, "key_input", 1000.3, text="s"),
            _evt(4, "key_input", 1000.4, text="t"),
        ]
        doc, _ = self._export(events)
        steps = self._action_steps(doc)
        self.assertEqual(len(steps), 1)
        step = steps[0]
        self.assertEqual(step["action"][0]["name"], "type")
        self.assertEqual(step["action"][0]["parameters"]["text"], "first")
        self.assertEqual(step["merge_rule"], "type_buffer")
        self.assertEqual(
            step["source_event_ids"],
            [f"evt_test_{i:04d}" for i in range(5)],
        )
        self.assertEqual(step["source_event_seq"], [0, 1, 2, 3, 4])

    def test_scroll_group_merges_continuous_scroll(self) -> None:
        events = [
            _evt(0, "scroll", 1000.0, x=500, y=500, dx=0, dy=-1),
            _evt(1, "scroll", 1000.05, x=500, y=500, dx=0, dy=-1),
            _evt(2, "scroll", 1000.12, x=500, y=500, dx=0, dy=-1),
        ]
        doc, _ = self._export(events)
        steps = self._action_steps(doc)
        self.assertEqual(len(steps), 1)
        step = steps[0]
        self.assertEqual(step["action"][0]["name"], "scroll")
        self.assertEqual(step["action"][0]["parameters"]["clicks"], -3)
        self.assertEqual(step["merge_rule"], "scroll_group")
        self.assertEqual(len(step["source_event_ids"]), 3)

    def test_drag_compose_includes_all_drag_events(self) -> None:
        events = [
            _evt(0, "drag_start", 1000.0, button="left", start_x=100, start_y=100, x=100, y=100),
            _evt(1, "drag_move", 1000.05, button="left", x=120, y=80),
            _evt(2, "drag_move", 1000.15, button="left", x=140, y=60),
            _evt(3, "drag_end", 1000.38, button="left", x=160, y=40),
        ]
        doc, _ = self._export(events)
        steps = self._action_steps(doc)
        self.assertEqual(len(steps), 1)
        step = steps[0]
        self.assertEqual(step["action"][0]["name"], "dragTo")
        self.assertEqual(step["merge_rule"], "drag_compose")
        self.assertEqual(len(step["source_event_ids"]), 4)
        self.assertEqual(step["source_event_seq"], [0, 1, 2, 3])

    def test_hotkey_chord_includes_press_input_release(self) -> None:
        events = [
            _evt(0, "press", 1000.0, name="ctrl_l"),
            _evt(1, "key_input", 1000.08, text="\x01"),
            _evt(2, "release", 1000.15, name="ctrl_l"),
        ]
        doc, _ = self._export(events)
        steps = self._action_steps(doc)
        self.assertEqual(len(steps), 1)
        step = steps[0]
        self.assertEqual(step["action"][0]["name"], "hotkey")
        self.assertEqual(step["action"][0]["parameters"]["keys"], ["ctrl", "a"])
        self.assertEqual(step["merge_rule"], "hotkey_chord")
        self.assertEqual(
            step["source_event_ids"],
            ["evt_test_0000", "evt_test_0001", "evt_test_0002"],
        )

    def test_terminate_step_has_append_rule(self) -> None:
        events = [_evt(0, "left_click", 1000.0, x=10, y=10, button="left")]
        doc, prov = self._export(events)
        terminate = doc["trajectory"][-1]
        self.assertEqual(terminate["action"][0]["name"], "terminate")
        self.assertEqual(terminate["merge_rule"], "terminate_append")
        self.assertEqual(terminate.get("source_event_ids"), [])
        self.assertEqual(prov["steps"][-1]["merge_rule"], "terminate_append")

    def test_step_seq_is_contiguous_including_terminate(self) -> None:
        events = [
            _evt(0, "left_click", 1000.0, x=10, y=10, button="left"),
            _evt(1, "key_input", 1001.0, text="a"),
            _evt(2, "scroll", 1002.0, dx=0, dy=-1, x=10, y=10),
        ]
        doc, _ = self._export(events)
        traj = doc["trajectory"]
        self.assertEqual([s.get("step_seq") for s in traj], list(range(len(traj))))
        self.assertEqual(traj[-1]["action"][0]["name"], "terminate")
        self.assertEqual(traj[-1]["step_seq"], len(traj) - 1)

    def test_provenance_matches_trajectory(self) -> None:
        events = [
            _evt(0, "left_click", 1000.0, x=10, y=10, button="left"),
            _evt(1, "key_input", 1001.0, text="a"),
        ]
        doc, prov = self._export(events)
        self.assertIsNotNone(prov)
        self.assertEqual(len(prov["steps"]), len(doc["trajectory"]))
        for idx, (step, pstep) in enumerate(zip(doc["trajectory"], prov["steps"])):
            self.assertEqual(pstep["step_id"], step.get("step_id"))
            self.assertEqual(step.get("step_seq"), idx)
            self.assertEqual(pstep["step_seq"], idx)
            self.assertEqual(pstep["step_index"], idx)
            self.assertEqual(pstep["source_event_ids"], step.get("source_event_ids", []))


if __name__ == "__main__":
    unittest.main()
