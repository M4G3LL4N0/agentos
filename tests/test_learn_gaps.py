"""CORE tests: learning layer, gap analysis, quality evaluation."""

import tempfile
import unittest
from pathlib import Path

from agentos.engine import Engine
from agentos.events import EventBus
from agentos.models import Execution, ExecutionStatus, Objective, new_id
from agentos.registry import CapabilityRegistry
from agentos.services import AgentOS
from agentos.store import Store
from agentos.verify import evaluate_quality


class LearnGapCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.app = AgentOS(home=Path(self.tmp.name) / "home")
        self.app.init()

    def tearDown(self) -> None:
        self.app.close()
        self.tmp.cleanup()


class TestLearningLayer(LearnGapCase):
    def test_success_and_failure_patterns_recorded(self) -> None:
        good = self.app.create_objective("learn good", operation="echo")
        self.app.run(good.id)
        bad = self.app.create_objective(
            "learn bad", operation="shell.run", params={"command": "exit 1"}
        )
        self.app.run(bad.id, max_attempts=1)
        patterns = self.app.patterns()
        self.assertTrue(any(p["verified"] for p in patterns))
        self.assertTrue(any(not p["verified"] for p in patterns))
        failed = [p for p in patterns if not p["verified"]][0]
        self.assertEqual(failed["failure_type"], "exit_code")
        self.assertTrue(failed["lesson"])

    def test_history_prefers_proven_capability(self) -> None:
        from agentos.models import Pattern

        for _ in range(5):
            self.app.store.save_pattern(
                Pattern(
                    id=new_id("pat"),
                    objective_class="echo:DIRECT",
                    operation="echo",
                    strategy="DIRECT",
                    capability_id="echo",
                    verified=True,
                    lesson="echo proven",
                )
            )
        plan = self.app.engine.router.plan(
            self.app.create_objective("x", operation="echo"),
            "echo",
            self.app.capabilities(),
        )
        self.assertIn("history=5/5", plan.rationale)

    def test_patterns_are_statistics_not_facts(self) -> None:
        # A single failure does not disable a capability; routing still
        # considers it (score penalty, not removal).
        bad = self.app.create_objective(
            "one failure", operation="echo", params={"command": "exit 1"}
        )
        self.app.run(bad.id, max_attempts=1)
        capability = self.app.store.get_capability("echo")
        assert capability is not None
        self.assertTrue(capability.is_usable())


class TestGapAnalysis(LearnGapCase):
    def test_blocked_objective_produces_structured_gap(self) -> None:
        objective = self.app.create_objective("need teleport", operation="teleport.run")
        report = self.app.run(objective.id)
        self.assertEqual(report.state.value, "BLOCKED")
        gaps = self.app.gaps(objective.id)
        self.assertEqual(len(gaps), 1)
        gap = gaps[0]
        self.assertEqual(gap["operation"], "teleport.run")
        self.assertTrue(gap["required_capability"])
        self.assertTrue(gap["why_required"])
        self.assertTrue(gap["missing_interface"])
        self.assertTrue(len(gap["implementation_paths"]) >= 2)
        self.assertTrue(gap["risk"])
        self.assertTrue(gap["verification_plan"])
        self.assertIn("gap", (report.next_action or "").lower())


class TestQualityEvaluation(unittest.TestCase):
    def _execution(self, output: dict, error=None) -> Execution:
        return Execution(
            id=new_id("exec"),
            objective_id="obj_1",
            capability_id="shell",
            operation="shell.run",
            status=ExecutionStatus.EXECUTED,
            output=output,
            error=error,
        )

    def _objective(self) -> Objective:
        return Objective(id=new_id("obj"), title="t", description="d")

    def test_strong_execution_passes(self) -> None:
        from agentos.models import Evidence, VerificationResult

        execution = self._execution(
            {"stdout": "a" * 100, "stderr": "", "exit_code": 0, "duration_ms": 50}
        )
        execution.evidence = [Evidence(kind="exit_code", detail="exit 0")]
        verification = VerificationResult(
            id=new_id("vrf"),
            objective_id="obj_1",
            execution_id=execution.id,
            verified=True,
            method="exit_code",
            evidence=[
                Evidence(kind="exit_code", detail="exit 0"),
                Evidence(kind="x", detail="y"),
            ],
        )
        evaluation = evaluate_quality(self._objective(), execution, verification)
        self.assertEqual(evaluation.grade, "pass")
        self.assertGreaterEqual(evaluation.score, 80)

    def test_unverified_execution_fails_quality(self) -> None:
        from agentos.models import VerificationResult

        execution = self._execution({"stdout": "x" * 100})
        verification = VerificationResult(
            id=new_id("vrf"),
            objective_id="obj_1",
            execution_id=execution.id,
            verified=False,
            method="exit_code",
            evidence=[],
        )
        evaluation = evaluate_quality(self._objective(), execution, verification)
        self.assertEqual(evaluation.grade, "fail")
        self.assertEqual(evaluation.score, 0)

    def test_thin_output_needs_review(self) -> None:
        from agentos.models import VerificationResult

        execution = self._execution({"stdout": "ok"})
        verification = VerificationResult(
            id=new_id("vrf"),
            objective_id="obj_1",
            execution_id=execution.id,
            verified=True,
            method="structural",
            evidence=[],
        )
        evaluation = evaluate_quality(self._objective(), execution, verification)
        self.assertIn(evaluation.grade, ("needs_review", "fail"))
        self.assertTrue(evaluation.findings)


class TestDirectStoreTables(unittest.TestCase):
    def test_plans_patterns_gaps_quality_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "agentos.db")
            try:
                from agentos.models import (
                    ExecutionPlan,
                    GapAnalysis,
                    Pattern,
                    QualityEvaluation,
                    Strategy,
                )

                plan = ExecutionPlan(
                    id=new_id("plan"),
                    objective_id="obj_1",
                    strategy=Strategy.DIRECT,
                    rationale="test",
                )
                store.save_plan(plan)
                self.assertEqual(len(store.list_plans("obj_1")), 1)
                pattern = Pattern(
                    id=new_id("pat"),
                    objective_class="c",
                    operation="echo",
                    strategy="DIRECT",
                    capability_id="echo",
                    verified=True,
                    lesson="ok",
                )
                store.save_pattern(pattern)
                self.assertEqual(len(store.list_patterns("c")), 1)
                self.assertEqual(
                    store.capability_stats("echo")["echo"]["verified"], 1
                )
                gap = GapAnalysis(
                    id=new_id("gap"),
                    objective_id="obj_1",
                    operation="teleport.run",
                    required_capability="teleporter",
                    why_required="because",
                )
                store.save_gap(gap)
                self.assertEqual(len(store.list_gaps("obj_1")), 1)
                quality = QualityEvaluation(
                    id=new_id("qual"),
                    objective_id="obj_1",
                    execution_id="exec_1",
                    score=90,
                    grade="pass",
                )
                store.save_quality(quality)
                self.assertEqual(len(store.list_quality("obj_1")), 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()