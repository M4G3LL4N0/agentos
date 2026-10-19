"""Tests for hive intelligence worker assignment."""

from __future__ import annotations

import unittest

from agentos.hive_intelligence import (
    Assignment,
    TaskProfile,
    choose_assignment,
    coalesce_tasks,
    score_candidate,
    should_parallelize,
)


def ready_candidate(cid: str = "opencode", cost: int = 1, free: bool = True) -> dict:
    return {
        "id": cid,
        "state": "READY",
        "free": free,
        "cost_rank": cost,
        "capabilities": ["coding", "general"],
    }


class TestScoreCandidate(unittest.TestCase):
    def test_not_ready_scores_zero(self) -> None:
        cand = ready_candidate()
        cand["state"] = "AUTH_REQUIRED"
        s = score_candidate(TaskProfile(), cand)
        self.assertEqual(s.score, 0.0)
        self.assertIn("AUTH_REQUIRED", s.reason)

    def test_usage_blocked_scores_zero(self) -> None:
        s = score_candidate(TaskProfile(), ready_candidate(), usage={"blocked": True})
        self.assertEqual(s.score, 0.0)

    def test_quota_exhausted_scores_zero(self) -> None:
        s = score_candidate(TaskProfile(), ready_candidate(), usage={"quota_left": 0.0})
        self.assertEqual(s.score, 0.0)

    def test_ready_scores_positive(self) -> None:
        s = score_candidate(TaskProfile(), ready_candidate())
        self.assertGreater(s.score, 0.0)

    def test_high_success_beats_low(self) -> None:
        high = score_candidate(
            TaskProfile(), ready_candidate(), history={"success_rate": 0.95}
        )
        low = score_candidate(
            TaskProfile(), ready_candidate(), history={"success_rate": 0.10}
        )
        self.assertGreater(high.score, low.score)

    def test_free_beats_expensive_on_tie(self) -> None:
        free = score_candidate(TaskProfile(), ready_candidate("opencode", 1, True))
        paid = score_candidate(TaskProfile(), ready_candidate("claude", 4, False))
        self.assertGreater(free.score, paid.score)

    def test_capability_mismatch_penalized(self) -> None:
        match = score_candidate(TaskProfile(capability="coding"), ready_candidate())
        mismatch = score_candidate(TaskProfile(capability="rendering"), ready_candidate())
        self.assertGreater(match.score, mismatch.score)


class TestChooseAssignment(unittest.TestCase):
    def test_picks_best(self) -> None:
        a = choose_assignment(
            TaskProfile(),
            [ready_candidate("claude", 4, False), ready_candidate("opencode", 1, True)],
        )
        self.assertIsInstance(a, Assignment)
        self.assertEqual(a.provider_id, "opencode")

    def test_empty_returns_none(self) -> None:
        self.assertIsNone(choose_assignment(TaskProfile(), []))

    def test_all_blocked_returns_none(self) -> None:
        cand = ready_candidate()
        cand["state"] = "UNAVAILABLE"
        self.assertIsNone(choose_assignment(TaskProfile(), [cand]))

    def test_history_overrides_cost(self) -> None:
        # expensive but proven provider wins when reliability differs a lot
        a = choose_assignment(
            TaskProfile(complexity=5, project_priority=90),
            [ready_candidate("opencode", 1, True), ready_candidate("claude", 4, False)],
            histories={
                "opencode": {"success_rate": 0.10, "avg_latency_ms": 5000},
                "claude": {"success_rate": 0.99, "avg_latency_ms": 2000},
            },
        )
        self.assertEqual(a.provider_id, "claude")


class TestParallelize(unittest.TestCase):
    def test_single_task_one_worker(self) -> None:
        self.assertEqual(should_parallelize(1, 60.0, 5.0, 4), 1)

    def test_no_workers_one(self) -> None:
        self.assertEqual(should_parallelize(8, 60.0, 5.0, 1), 1)

    def test_many_fast_tasks_parallel_wins(self) -> None:
        w = should_parallelize(8, 60.0, 2.0, 4)
        self.assertGreaterEqual(w, 2)

    def test_high_overhead_no_parallel(self) -> None:
        # overhead so large parallelism never wins by 20%
        w = should_parallelize(4, 10.0, 100.0, 4)
        self.assertEqual(w, 1)


class TestCoalesce(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(coalesce_tasks([], 4), [])

    def test_fewer_than_workers(self) -> None:
        self.assertEqual(coalesce_tasks(["a", "b"], 4), [["a"], ["b"]])

    def test_no_excessive_split(self) -> None:
        tasks = [f"t{i}" for i in range(10)]
        groups = coalesce_tasks(tasks, 3)
        self.assertLessEqual(len(groups), 3)
        self.assertTrue(all(len(g) >= 3 for g in groups))
        flat = [t for g in groups for t in g]
        self.assertEqual(flat, tasks)


if __name__ == "__main__":
    unittest.main()
