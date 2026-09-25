"""Tests for real objective decomposition: parent/child persistence,
dependency blocking, and individually runnable children.

Default execution mode is SIMULATED (no authorization needed); no test
here passes mode=LIVE.
"""

import tempfile
import unittest
from pathlib import Path

from agentos.decompose import (
    DecomposedChild,
    Decomposition,
    create_child_objectives,
    objective_is_unblocked,
)
from agentos.engine import AgentOSError, Engine
from agentos.events import EventBus
from agentos.models import Objective, ObjectiveState, new_id
from agentos.registry import CapabilityRegistry
from agentos.services import AgentOS
from agentos.store import Store


def _echo_context() -> dict:
    return {"operation": "echo", "params": {"hello": "world"}}


class DecomposeExecutionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "agentos.db")
        self.bus = EventBus(self.store)
        self.registry = CapabilityRegistry(self.store, self.bus)
        self.engine = Engine(self.store, self.bus, self.registry)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def _parent(self) -> Objective:
        parent = Objective(
            id=new_id("obj"),
            title="parent work",
            description="decompose me",
            context=dict(_echo_context()),
        )
        self.store.save_objective(parent)
        return parent

    def _three_child_decomposition(self, parent_id: str) -> Decomposition:
        child_a = DecomposedChild(id="child_a", title="A", operation="echo")
        child_b = DecomposedChild(
            id="child_b", title="B", operation="echo", depends_on=["child_a"]
        )
        child_c = DecomposedChild(
            id="child_c",
            title="C",
            operation="echo",
            depends_on=["child_a", "child_b"],
        )
        return Decomposition(
            id=new_id("decomp"),
            objective_id=parent_id,
            children=[child_a, child_b, child_c],
            order=[["child_a"], ["child_b"], ["child_c"]],
            gaps=[],
            rationale="test graph A -> B -> C",
        )


class TestObjectiveIsUnblocked(DecomposeExecutionTestCase):
    def test_blocked_until_deps_complete(self) -> None:
        parent = self._parent()
        decomposition = self._three_child_decomposition(parent.id)
        children = create_child_objectives(decomposition, parent.id, self.store)
        by_title = {c.title: c for c in children}

        # B depends on A: blocked with nothing done, unblocked once A is done.
        blocked, reason = objective_is_unblocked(by_title["B"], set())
        self.assertFalse(blocked)
        self.assertIsNotNone(reason)
        ok, reason = objective_is_unblocked(by_title["B"], {by_title["A"].id})
        self.assertTrue(ok)
        self.assertIsNone(reason)

        # C depends on A and B: A alone is not enough.
        blocked, reason = objective_is_unblocked(
            by_title["C"], {by_title["A"].id}
        )
        self.assertFalse(blocked)
        self.assertIsNotNone(reason)
        ok, _ = objective_is_unblocked(
            by_title["C"], {by_title["A"].id, by_title["B"].id}
        )
        self.assertTrue(ok)

        # A has no deps: never blocked.
        ok, reason = objective_is_unblocked(by_title["A"], set())
        self.assertTrue(ok)
        self.assertIsNone(reason)


class TestChildPersistence(DecomposeExecutionTestCase):
    def test_children_persist_with_parent_id(self) -> None:
        parent = self._parent()
        decomposition = self._three_child_decomposition(parent.id)
        children = create_child_objectives(decomposition, parent.id, self.store)
        self.assertEqual(len(children), 3)
        for child in children:
            self.assertEqual(child.parent_id, parent.id)
            stored = self.store.get_objective(child.id)
            assert stored is not None
            self.assertEqual(stored.parent_id, parent.id)

    def test_children_carry_dependency_metadata(self) -> None:
        parent = self._parent()
        decomposition = self._three_child_decomposition(parent.id)
        children = create_child_objectives(decomposition, parent.id, self.store)
        by_title = {c.title: c for c in children}
        self.assertEqual(by_title["A"].parents_data.get("depends_on"), [])
        self.assertEqual(
            by_title["B"].parents_data.get("depends_on"), [by_title["A"].id]
        )
        self.assertEqual(
            sorted(by_title["C"].parents_data.get("depends_on") or []),
            sorted([by_title["A"].id, by_title["B"].id]),
        )


class TestEngineRunsChildren(DecomposeExecutionTestCase):
    def test_run_child_a_then_child_b(self) -> None:
        parent = self._parent()
        decomposition = self._three_child_decomposition(parent.id)
        children = create_child_objectives(decomposition, parent.id, self.store)
        by_title = {c.title: c for c in children}
        report_a = self.engine.run(by_title["A"].id)
        self.assertEqual(report_a.state, ObjectiveState.COMPLETED)
        report_b = self.engine.run(by_title["B"].id)
        self.assertEqual(report_b.state, ObjectiveState.COMPLETED)

    def test_run_parent_while_children_unstarted_blocks(self) -> None:
        parent = self._parent()
        decomposition = self._three_child_decomposition(parent.id)
        create_child_objectives(decomposition, parent.id, self.store)
        report = self.engine.run(parent.id)
        self.assertEqual(report.state, ObjectiveState.BLOCKED)
        self.assertTrue(report.next_action)
        self.assertIn("blocked", report.next_action.lower())

    def test_run_child_with_incomplete_dep_blocks(self) -> None:
        parent = self._parent()
        decomposition = self._three_child_decomposition(parent.id)
        children = create_child_objectives(decomposition, parent.id, self.store)
        by_title = {c.title: c for c in children}
        report = self.engine.run(by_title["B"].id)
        self.assertEqual(report.state, ObjectiveState.BLOCKED)


class TestUnknownDependsOn(DecomposeExecutionTestCase):
    def test_unknown_depends_on_raises(self) -> None:
        parent = self._parent()
        bad = DecomposedChild(
            id="child_x", title="X", operation="echo", depends_on=["no_such_child"]
        )
        decomposition = Decomposition(
            id=new_id("decomp"),
            objective_id=parent.id,
            children=[bad],
            order=[["child_x"]],
            gaps=[],
            rationale="dangling dep must fail loud",
        )
        with self.assertRaises(AgentOSError):
            create_child_objectives(decomposition, parent.id, self.store)


class TestServiceParentWiring(unittest.TestCase):
    def test_create_objective_with_parent_and_children(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        try:
            home = Path(tmp.name) / "home"
            app = AgentOS(home=home)
            try:
                app.init()
                parent = app.create_objective("parent work", "d")
                child = app.create_objective(
                    "child work", "d", parent=parent.id
                )
                self.assertEqual(child.parent_id, parent.id)
                kids = app.children(parent.id)
                self.assertEqual([k.id for k in kids], [child.id])
            finally:
                app.close()
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
