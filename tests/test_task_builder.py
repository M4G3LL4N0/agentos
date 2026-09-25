"""Tests for the provider-neutral bounded task builder (Task 6)."""

import unittest

from agentos.models import Capability, CapabilityType, Objective, PlanStep
from agentos.task_builder import ProjectContext, build_task


def _objective(**over):
    base = {
        "id": "obj_1",
        "title": "Wire checkout totals",
        "description": "Compute cart totals with tax.",
        "strategy": "DIRECT",
        "constraints": ["stdlib only"],
        "context": {"current_state": "cart sums subtotal only"},
    }
    base.update(over)
    return Objective(**base)


def _context(**over):
    base = {
        "paths": ["src/shop/cart.py"],
        "git_root": "/repo",
        "baseline_docs": ["README.md"],
        "locked_dirs": ["src/shop/legacy"],
    }
    base.update(over)
    return ProjectContext(**base)


HEADERS = [
    "PROJECT",
    "OBJECTIVE",
    "CURRENT STATE",
    "CONSTRAINTS",
    "FILES & SYSTEMS TO PRESERVE",
    "EXPECTED BEHAVIOR",
    "VERIFICATION",
    "DEFINITION OF DONE",
]


class TestTaskBuilder(unittest.TestCase):
    def test_all_eight_sections_present(self):
        out = build_task(
            _objective(),
            _context(),
            ["stdlib only"],
            "Totals include tax.",
            ["src/shop/legacy"],
        )
        for header in HEADERS:
            self.assertIn(header, out)

    def test_instruction_line(self):
        out = build_task(
            _objective(), _context(), [], "Totals include tax.", []
        )
        self.assertIn("INSPECT FIRST", out)
        self.assertIn("NO SUCCESS WITHOUT EVIDENCE", out)

    def test_bounded_with_long_inputs(self):
        long_desc = "x" * 5000
        long_behavior = "y" * 5000
        long_constraint = "z" * 3000
        long_donot = "w" * 3000
        long_doc = "d" * 3000
        out = build_task(
            _objective(description=long_desc),
            _context(baseline_docs=[long_doc]),
            [long_constraint],
            long_behavior,
            [long_donot],
        )
        self.assertLess(len(out), 8000)
        self.assertNotIn(long_desc, out)
        self.assertNotIn(long_behavior, out)
        self.assertNotIn(long_constraint, out)
        self.assertNotIn(long_donot, out)
        self.assertNotIn(long_doc, out)
        self.assertIn("[truncated]", out)

    def test_pure_deterministic(self):
        args = (
            _objective(),
            _context(),
            ["stdlib only"],
            "Totals include tax.",
            ["src/shop/legacy"],
        )
        first = build_task(*args)
        second = build_task(*args)
        self.assertEqual(first, second)

    def test_uses_real_model_fields(self):
        step = PlanStep(id="s1", kind="operation", operation="edit")
        cap = Capability(
            id="c1",
            name="shell",
            type=CapabilityType.SHELL if hasattr(CapabilityType, "SHELL") else list(CapabilityType)[0],
            description="Run shell cmds",
        )
        out = build_task(
            _objective(),
            _context(),
            ["stdlib only"],
            "Totals include tax.",
            ["src/shop/legacy"],
            steps=[step],
            capabilities=[cap],
            strategy="DIRECT",
        )
        self.assertIn("Wire checkout totals", out)
        self.assertIn("DIRECT", out)


if __name__ == "__main__":
    unittest.main()
