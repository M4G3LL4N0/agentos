"""Cheap-triage short-circuit tests (rules-first; no model I/O)."""

from __future__ import annotations

import unittest

from agentos.efficiency import Resolution, plan_team, triage


class TestEfficiencyTriageShortCircuit(unittest.TestCase):
    def test_exact_cache_hit_short_circuits_before_hints(self) -> None:
        d = triage("refactor the coding unit test suite", exact_cache_hit=True)
        self.assertEqual(d.resolution, Resolution.CACHE_RETURN)
        self.assertIn("cache", d.reason.lower())

    def test_known_workflow_beats_premium_path(self) -> None:
        d = triage("architecture planning synthesis", known_workflow=True)
        self.assertEqual(d.resolution, Resolution.KNOWN_WORKFLOW)

    def test_deterministic_sum_without_model(self) -> None:
        d = triage("sum and count these totals")
        self.assertEqual(d.resolution, Resolution.DETERMINISTIC)

    def test_cheap_model_for_classify(self) -> None:
        d = triage("classify and categorize these labels")
        self.assertEqual(d.resolution, Resolution.CHEAP_MODEL)

    def test_grokbot_only_when_unique_value(self) -> None:
        d = triage(
            "need persistent cloud computer and authenticated browser for durable supervisor identity"
        )
        self.assertEqual(d.resolution, Resolution.GROKBOT)

    def test_computer_does_not_false_trigger_compute(self) -> None:
        d = triage("use the office computer inventory spreadsheet")
        self.assertNotEqual(d.resolution, Resolution.DETERMINISTIC)

    def test_coding_prefers_non_grokbot_premium(self) -> None:
        d = triage("large repo inspection and coding refactor unit test")
        self.assertEqual(d.resolution, Resolution.PREMIUM)
        self.assertEqual(d.grok_bias, "DISFAVORED")


class TestSmallestTeam(unittest.TestCase):
    def test_default_single_worker(self) -> None:
        t = plan_team()
        self.assertEqual(t.size, 1)
        self.assertEqual(t.roles, ["worker"])
        self.assertEqual(t.parallel, 1)

    def test_verifier_only_when_justified(self) -> None:
        t = plan_team(
            decomposable=True,
            independent_subtasks=3,
            quality_floor="VERIFIED",
        )
        self.assertIn("verifier", t.roles)
        self.assertGreaterEqual(t.size, 3)


if __name__ == "__main__":
    unittest.main()
