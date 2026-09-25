"""ORCHESTRATION tests: routing strategies and plan execution."""

import tempfile
import unittest
from pathlib import Path

from agentos.engine import Engine
from agentos.events import EventBus
from agentos.models import Objective, ObjectiveState, Strategy, new_id
from agentos.registry import CapabilityRegistry
from agentos.router import Router, detect_write_conflicts
from agentos.store import Store


class RouterTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "agentos.db")
        self.bus = EventBus(self.store)
        self.registry = CapabilityRegistry(self.store, self.bus)
        self.capabilities = self.registry.discover()
        self.router = Router(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def objective(self, title: str = "t", **kwargs) -> Objective:
        objective = Objective(
            id=new_id("obj"), title=title, description=kwargs.pop("description", "d"), **kwargs
        )
        self.store.save_objective(objective)
        return objective


class TestStrategySelection(RouterTestCase):
    def test_direct_default(self) -> None:
        plan = self.router.plan(self.objective(), "echo", self.capabilities)
        self.assertEqual(plan.strategy, Strategy.DIRECT)
        self.assertEqual(len(plan.steps), 1)
        self.assertIn("rationale", plan.to_dict())
        self.assertTrue(plan.rationale)

    def test_delegated_for_agent_operations(self) -> None:
        plan = self.router.plan(self.objective(), "opencode.run", self.capabilities)
        self.assertEqual(plan.strategy, Strategy.DELEGATED)
        self.assertEqual(plan.steps[0].capability_id, "opencode")

    def test_parallel_items_without_conflicts(self) -> None:
        objective = self.objective(
            context={
                "operation": "echo",
                "items": [{"params": {"a": 1}}, {"params": {"b": 2}}],
            }
        )
        plan = self.router.plan(objective, "echo", self.capabilities)
        self.assertEqual(plan.strategy, Strategy.PARALLEL)
        self.assertEqual(len(plan.steps), 2)

    def test_parallel_conflicts_serialize(self) -> None:
        objective = self.objective(
            context={
                "operation": "fs.write",
                "items": [
                    {"params": {"path": "/tmp/same.txt", "content": "1"}},
                    {"params": {"path": "/tmp/same.txt", "content": "2"}},
                ],
            }
        )
        plan = self.router.plan(objective, "fs.write", self.capabilities)
        self.assertEqual(plan.strategy, Strategy.SEQUENTIAL)
        self.assertIn("conflict", plan.rationale.lower())

    def test_explicit_steps_plan(self) -> None:
        objective = self.objective(
            context={
                "steps": [
                    {"kind": "operation", "operation": "echo", "params": {"a": 1}},
                    {"kind": "operation", "operation": "echo", "params": {"b": 2}},
                ]
            }
        )
        plan = self.router.plan(objective, "echo", self.capabilities)
        self.assertEqual(plan.strategy, Strategy.SEQUENTIAL)
        self.assertEqual(len(plan.steps), 2)

    def test_research_then_execute(self) -> None:
        objective = self.objective(context={"research": True, "operation": "echo"})
        plan = self.router.plan(objective, "echo", self.capabilities)
        self.assertEqual(plan.strategy, Strategy.RESEARCH_THEN_EXECUTE)
        self.assertEqual(len(plan.steps), 2)
        roles = {r.value for r in plan.roles}
        self.assertIn("researcher", roles)

    def test_build_then_verify_uses_independent_capability(self) -> None:
        objective = self.objective(
            "implement and test the widget",
            context={
                "operation": "fs.write",
                "params": {"path": "/tmp/btv.txt", "content": "x"},
            },
        )
        plan = self.router.plan(objective, "fs.write", self.capabilities)
        self.assertEqual(plan.strategy, Strategy.BUILD_THEN_VERIFY)
        build, verify = plan.steps
        self.assertNotEqual(build.capability_id, verify.capability_id)
        self.assertEqual(verify.kind, "verify")

    def test_review_then_revise(self) -> None:
        objective = self.objective(context={"review": True, "operation": "echo"})
        plan = self.router.plan(objective, "echo", self.capabilities)
        self.assertEqual(plan.strategy, Strategy.REVIEW_THEN_REVISE)
        self.assertEqual(plan.steps[-1].kind, "evaluate")

    def test_iterative_bounded(self) -> None:
        objective = self.objective(
            context={"iterate": True, "max_iterations": 10, "operation": "echo"}
        )
        plan = self.router.plan(objective, "echo", self.capabilities)
        self.assertEqual(plan.strategy, Strategy.ITERATIVE)
        self.assertLessEqual(len(plan.steps), 5)

    def test_graph_falls_back_without_langgraph(self) -> None:
        objective = self.objective(
            context={"strategy": "GRAPH", "operation": "echo"}
        )
        plan = self.router.plan(objective, "echo", self.capabilities)
        self.assertEqual(plan.strategy, Strategy.SEQUENTIAL)
        self.assertIn("GRAPH", plan.rationale)

    def test_history_influences_selection(self) -> None:
        from agentos.models import Pattern

        for _ in range(4):
            self.store.save_pattern(
                Pattern(
                    id=new_id("pat"),
                    objective_class="echo:DIRECT",
                    operation="echo",
                    strategy="DIRECT",
                    capability_id="echo",
                    verified=True,
                    lesson="echo works",
                )
            )
        plan = self.router.plan(self.objective(), "echo", self.capabilities)
        self.assertIn("history=", plan.rationale)
        self.assertEqual(plan.steps[0].capability_id, "echo")

    def test_unknown_operation_plans_without_capability(self) -> None:
        plan = self.router.plan(self.objective(), "teleport.run", self.capabilities)
        self.assertIsNone(plan.steps[0].capability_id)


class TestConflictDetection(unittest.TestCase):
    def test_detects_shared_write_paths(self) -> None:
        conflicts = detect_write_conflicts(
            [
                {"params": {"path": "/tmp/a.txt"}},
                {"params": {"path": "/tmp/a.txt"}},
                {"params": {"path": "/tmp/b.txt"}},
            ]
        )
        self.assertEqual(conflicts, ["/tmp/a.txt"])

    def test_no_conflict_for_distinct_paths(self) -> None:
        conflicts = detect_write_conflicts(
            [{"params": {"path": "/tmp/a.txt"}}, {"params": {"path": "/tmp/b.txt"}}]
        )
        self.assertEqual(conflicts, [])


class EngineOrchestrationCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "agentos.db")
        self.bus = EventBus(self.store)
        self.registry = CapabilityRegistry(self.store, self.bus)
        self.engine = Engine(self.store, self.bus, self.registry)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def objective(self, title: str = "t", **kwargs) -> Objective:
        objective = Objective(
            id=new_id("obj"), title=title, description=kwargs.pop("description", "d"), **kwargs
        )
        self.store.save_objective(objective)
        return objective


class TestSequentialExecution(EngineOrchestrationCase):
    def test_two_step_plan_runs_in_order(self) -> None:
        first = Path(self.tmp.name) / "first.txt"
        second = Path(self.tmp.name) / "second.txt"
        objective = self.objective(
            context={
                "steps": [
                    {
                        "kind": "operation",
                        "operation": "fs.write",
                        "params": {"path": str(first), "content": "one"},
                    },
                    {
                        "kind": "operation",
                        "operation": "fs.write",
                        "params": {"path": str(second), "content": "two"},
                    },
                ]
            }
        )
        report = self.engine.run(objective.id)
        self.assertEqual(report.state, ObjectiveState.COMPLETED)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual(len(self.store.list_executions(objective.id)), 2)
        plans = self.store.list_plans(objective.id)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].strategy.value, "SEQUENTIAL")

    def test_failed_first_step_stops_plan(self) -> None:
        objective = self.objective(
            context={
                "steps": [
                    {
                        "kind": "operation",
                        "operation": "shell.run",
                        "params": {"command": "exit 1"},
                    },
                    {"kind": "operation", "operation": "echo", "params": {}},
                ]
            }
        )
        from agentos.engine import EngineOptions

        report = self.engine.run(objective.id, EngineOptions(max_attempts=1))
        self.assertEqual(report.state, ObjectiveState.FAILED)
        self.assertEqual(len(self.store.list_executions(objective.id)), 1)

    def test_build_then_verify_independent(self) -> None:
        target = Path(self.tmp.name) / "btv.txt"
        objective = self.objective(
            "implement and test the widget",
            context={
                "operation": "fs.write",
                "params": {"path": str(target), "content": "widget"},
            },
        )
        report = self.engine.run(objective.id)
        self.assertEqual(report.state, ObjectiveState.COMPLETED)
        executions = self.store.list_executions(objective.id)
        capability_ids = {e.capability_id for e in executions}
        self.assertTrue({"filesystem", "shell"} <= capability_ids)


class TestParallelExecution(EngineOrchestrationCase):
    def test_parallel_items_all_execute(self) -> None:
        paths = [str(Path(self.tmp.name) / f"p{i}.txt") for i in range(3)]
        objective = self.objective(
            context={
                "operation": "fs.write",
                "items": [
                    {"params": {"path": p, "content": f"content-{i}"}}
                    for i, p in enumerate(paths)
                ],
            }
        )
        report = self.engine.run(objective.id)
        self.assertEqual(report.state, ObjectiveState.COMPLETED)
        for path in paths:
            self.assertTrue(Path(path).exists())
        stored = self.store.get_objective(objective.id)
        assert stored is not None
        self.assertIn("PARALLEL", stored.strategy)

    def test_parallel_failure_fails_objective(self) -> None:
        objective = self.objective(
            context={
                "operation": "shell.run",
                "items": [
                    {"params": {"command": "echo ok"}},
                    {"params": {"command": "exit 1"}},
                ],
            }
        )
        from agentos.engine import EngineOptions

        report = self.engine.run(objective.id, EngineOptions(max_attempts=1))
        self.assertEqual(report.state, ObjectiveState.FAILED)


class TestIterativeAndReview(EngineOrchestrationCase):
    def test_iterative_stops_at_first_verify(self) -> None:
        objective = self.objective(
            context={"iterate": True, "max_iterations": 3, "operation": "echo"}
        )
        report = self.engine.run(objective.id)
        self.assertEqual(report.state, ObjectiveState.COMPLETED)
        self.assertEqual(len(self.store.list_executions(objective.id)), 1)

    def test_review_records_quality(self) -> None:
        objective = self.objective(context={"review": True, "operation": "echo"})
        report = self.engine.run(objective.id)
        self.assertEqual(report.state, ObjectiveState.COMPLETED)
        quality = self.store.list_quality(objective.id)
        self.assertTrue(len(quality) >= 1)
        self.assertIn(quality[0].grade, ("pass", "needs_review", "fail"))


class TestTerminationAndGaps(EngineOrchestrationCase):
    def test_unknown_operation_blocks_and_records_gap(self) -> None:
        objective = self.objective(context={"operation": "teleport.run"})
        report = self.engine.run(objective.id)
        self.assertEqual(report.state, ObjectiveState.BLOCKED)
        gaps = self.store.list_gaps(objective.id)
        self.assertEqual(len(gaps), 1)
        gap = gaps[0]
        self.assertIn("teleport.run", gap.operation)
        self.assertTrue(gap.why_required)
        self.assertTrue(gap.missing_interface)
        self.assertTrue(gap.implementation_paths)
        self.assertTrue(gap.verification_plan)

    def test_unsafe_destructive_blocks_for_approval(self) -> None:
        objective = self.objective(
            context={
                "operation": "shell.run",
                "operation_class": "destructive",
                "params": {"command": "rm -rf ./build-cache"},
            }
        )
        from agentos.engine import EngineOptions

        report = self.engine.run(objective.id, EngineOptions(max_attempts=1))
        self.assertEqual(report.state, ObjectiveState.BLOCKED)
        assert report.next_action is not None
        self.assertIn("approval", report.next_action.lower())

    def test_patterns_recorded_for_success_and_failure(self) -> None:
        good = self.objective(context={"operation": "echo"})
        self.engine.run(good.id)
        bad = self.objective(
            context={"operation": "shell.run", "params": {"command": "exit 1"}}
        )
        from agentos.engine import EngineOptions

        self.engine.run(bad.id, EngineOptions(max_attempts=1))
        patterns = self.store.list_patterns()
        by_objective = {}
        for pattern in patterns:
            by_objective.setdefault(pattern.objective_class, []).append(pattern)
        self.assertTrue(any(p.verified for p in patterns))
        self.assertTrue(any(not p.verified for p in patterns))
        lessons = [p.lesson for p in patterns]
        self.assertTrue(all(lessons))


if __name__ == "__main__":
    unittest.main()