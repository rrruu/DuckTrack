"""Unit tests for coarse-chunk overlap + merge (no model calls)."""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ducktrack.subtask_splitter import SubtaskSplitter  # noqa: E402


def _task(start: int, end: int, instruction: str, confidence: float = 0.8) -> dict:
    return {
        "start_step": start,
        "end_step": end,
        "instruction": instruction,
        "confidence": confidence,
    }


class ChunkOverlapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.splitter = SubtaskSplitter()
        self.splitter.chunk_overlap = 8
        self.splitter.chunk_target = 100
        self.splitter.chunk_min = 40
        self.splitter.chunk_max = 180

    def test_apply_overlap_extends_later_chunks_backward(self) -> None:
        chunks = [
            {"start_step": 0, "end_step": 99, "theme": "a"},
            {"start_step": 100, "end_step": 199, "theme": "b"},
            {"start_step": 200, "end_step": 249, "theme": "c"},
        ]
        out = self.splitter._apply_chunk_overlap(chunks, 249)
        self.assertEqual(out[0]["start_step"], 0)
        self.assertEqual(out[0]["end_step"], 99)
        self.assertEqual(out[0]["core_start"], 0)
        self.assertEqual(out[0]["core_end"], 99)
        self.assertEqual(out[1]["start_step"], 92)
        self.assertEqual(out[1]["core_start"], 100)
        self.assertEqual(out[1]["core_end"], 199)
        self.assertEqual(out[2]["start_step"], 192)
        self.assertEqual(out[2]["core_start"], 200)
        self.assertEqual(out[2]["end_step"], 249)
        self.assertEqual(out[2]["core_end"], 249)

    def test_overlap_zero_keeps_abutting_windows(self) -> None:
        self.splitter.chunk_overlap = 0
        chunks = [
            {"start_step": 0, "end_step": 99, "theme": "a"},
            {"start_step": 100, "end_step": 199, "theme": "b"},
        ]
        out = self.splitter._apply_chunk_overlap(chunks, 199)
        self.assertEqual(out[1]["start_step"], 100)
        self.assertEqual(out[1]["core_start"], 100)

    def test_merge_prefers_right_when_it_fully_covers_with_higher_confidence(self) -> None:
        chunks = self.splitter._apply_chunk_overlap(
            [
                {"start_step": 0, "end_step": 99, "theme": "a"},
                {"start_step": 100, "end_step": 199, "theme": "b"},
            ],
            199,
        )
        left = [_task(0, 99, "左块整段", 0.5)]
        right = [_task(92, 199, "跨边界意图", 0.92)]
        merged, decisions = self.splitter._merge_overlapping_chunk_tasks(
            chunks, [left, right], 199,
        )
        self.assertEqual(decisions[0]["winner"], "right")
        self.assertEqual(
            [(t["start_step"], t["end_step"], t["instruction"]) for t in merged],
            [(0, 91, "左块整段"), (92, 199, "跨边界意图")],
        )

    def test_merge_keeps_left_when_right_is_fragmented(self) -> None:
        chunks = self.splitter._apply_chunk_overlap(
            [
                {"start_step": 0, "end_step": 99, "theme": "a"},
                {"start_step": 100, "end_step": 199, "theme": "b"},
            ],
            199,
        )
        left = [_task(0, 99, "完整阶段", 0.88)]
        right = [
            _task(92, 94, "碎段1", 0.4),
            _task(95, 99, "碎段2", 0.4),
            _task(100, 199, "右核", 0.7),
        ]
        merged, decisions = self.splitter._merge_overlapping_chunk_tasks(
            chunks, [left, right], 199,
        )
        self.assertEqual(decisions[0]["winner"], "left")
        self.assertEqual(merged[0]["start_step"], 0)
        self.assertEqual(merged[0]["end_step"], 99)
        self.assertEqual(merged[0]["instruction"], "完整阶段")
        self.assertEqual(merged[1]["start_step"], 100)
        self.assertEqual(merged[1]["end_step"], 199)

    def test_overlap_zero_concat_skips_ownership_merge(self) -> None:
        self.splitter.chunk_overlap = 0
        chunks = [
            {"start_step": 0, "end_step": 99, "core_start": 0, "core_end": 99},
            {"start_step": 100, "end_step": 199, "core_start": 100, "core_end": 199},
        ]
        left = [_task(0, 99, "A")]
        right = [_task(100, 199, "B")]
        merged, decisions = self.splitter._merge_overlapping_chunk_tasks(
            chunks, [left, right], 199,
        )
        self.assertEqual(decisions, [])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["end_step"], 99)
        self.assertEqual(merged[1]["start_step"], 100)

    def test_normalize_then_overlap_covers_last_step(self) -> None:
        chunks = [
            {"start_step": 0, "end_step": 99, "theme": "a"},
            {"start_step": 100, "end_step": 180, "theme": "b"},
        ]
        out = self.splitter._normalize_coarse_chunks(chunks, 199)
        self.assertEqual(out[0]["core_start"], 0)
        self.assertEqual(out[-1]["end_step"], 199)
        self.assertEqual(out[-1]["core_end"], 199)
        self.assertLess(out[1]["start_step"], out[1]["core_start"])


if __name__ == "__main__":
    unittest.main()
