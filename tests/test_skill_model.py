"""Tests for the Skill Model - compact structured procedures."""

from __future__ import annotations

import os
import tempfile
import unittest

from agentos.models import SkillDefinition
from agentos.store import Store
from agentos.skill_model import SkillModel, SKILL_TEMPLATES


class TestSkillTemplates(unittest.TestCase):
    def test_builtin_skills_exist(self):
        expected_skills = [
            "repo-audit",
            "usage-refresh",
            "ecosystem-delta",
            "location-arrival",
            "tonight",
            "roadtrip",
            "fuel-stop-stack",
            "weather-departure",
            "sunset",
            "weekly-city-plan",
            "workforce-health",
            "project-reality",
        ]
        for skill_id in expected_skills:
            self.assertIn(skill_id, SKILL_TEMPLATES)
            skill = SKILL_TEMPLATES[skill_id]
            self.assertIsInstance(skill, SkillDefinition)
            # The skill ID uses underscores
            self.assertEqual(skill.id, f"skill_{skill_id.replace('-', '_')}")

    def test_skill_definition_structure(self):
        skill = SKILL_TEMPLATES["repo-audit"]
        self.assertEqual(skill.objective, "Audit repository for issues, patterns, and improvements")
        self.assertIn("repo_path", skill.inputs)
        self.assertIn("read-only access", skill.constraints)
        self.assertGreater(len(skill.decision_rules), 0)
        self.assertIn("filesystem", skill.tools)
        self.assertIn("report", skill.outputs)
        self.assertEqual(skill.verification, "diff + test")
        self.assertEqual(skill.approval_boundary, "read-only")


class TestSkillModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "test.db"))
        self.model = SkillModel(self.store)

    def tearDown(self):
        self.tmp.cleanup()

    def test_get_skill(self):
        skill = self.model.get_skill("skill_repo_audit")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.objective, "Audit repository for issues, patterns, and improvements")

    def test_get_nonexistent_skill(self):
        skill = self.model.get_skill("nonexistent")
        self.assertIsNone(skill)

    def test_get_skill_by_objective(self):
        skill = self.model.get_skill_by_objective("Audit repository for issues, patterns, and improvements")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.id, "skill_repo_audit")

    def test_list_skills(self):
        skills = self.model.list_skills()
        self.assertGreaterEqual(len(skills), 12)

    def test_execute_skill(self):
        result = self.model.execute_skill("skill_repo_audit", {"repo_path": "/tmp/test"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["skill_id"], "skill_repo_audit")
        self.assertEqual(result["objective"], "Audit repository for issues, patterns, and improvements")
        self.assertIn("decision_rules_applied", result)
        self.assertIn("tools_used", result)

    def test_execute_nonexistent_skill(self):
        result = self.model.execute_skill("nonexistent", {})
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"])

    def test_create_skill(self):
        new_skill = SkillDefinition(
            id="skill_custom",
            objective="Custom skill for testing",
            inputs={"param1": "str"},
            constraints=["test constraint"],
            decision_rules=["rule1", "rule2"],
            tools=["tool1"],
            outputs={"result": "str"},
            verification="test",
            approval_boundary="none",
        )
        self.model.create_skill(new_skill)
        stored = self.store.get_skill("skill_custom")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.objective, "Custom skill for testing")

    def test_store_update_preserves_all_skill_fields(self):
        skill = SkillDefinition(
            id="skill_update",
            objective="original",
            inputs={"old": "str"},
            constraints=["old"],
            decision_rules=["old"],
            tools=["old"],
            outputs={"old": "str"},
            verification="old",
            approval_boundary="old",
            failure_handling={"old": True},
            context_refs=["old"],
        )
        self.store.save_skill(skill)
        skill.objective = "updated"
        skill.inputs = {"new": "str"}
        skill.constraints = ["new"]
        skill.decision_rules = ["new"]
        skill.tools = ["new"]
        skill.outputs = {"new": "str"}
        skill.verification = "new"
        skill.approval_boundary = "new"
        skill.failure_handling = {"new": True}
        skill.context_refs = ["new"]
        self.store.save_skill(skill)

        stored = self.store.get_skill("skill_update")
        self.assertEqual(stored.objective, "updated")
        self.assertEqual(stored.inputs, {"new": "str"})
        self.assertEqual(stored.constraints, ["new"])
        self.assertEqual(stored.decision_rules, ["new"])
        self.assertEqual(stored.tools, ["new"])
        self.assertEqual(stored.outputs, {"new": "str"})
        self.assertEqual(stored.verification, "new")
        self.assertEqual(stored.approval_boundary, "new")
        self.assertEqual(stored.failure_handling, {"new": True})
        self.assertEqual(stored.context_refs, ["new"])

    def test_update_skill(self):
        updated = self.model.update_skill("skill_repo_audit", {"version": 2, "objective": "Updated objective"})
        self.assertIsNotNone(updated)
        self.assertEqual(updated.version, 3)  # version 1 -> set to 2 -> incremented to 3
        self.assertEqual(updated.objective, "Updated objective")

    def test_recommend_skill(self):
        skill = self.model.recommend_skill("repo_audit", {})
        self.assertIsNotNone(skill)
        # The skill with "audit" in objective that was most recently updated is returned
        # Due to updated_at DESC ordering, project_reality (last in SKILL_TEMPLATES) comes first
        self.assertIn(skill.id, ["skill_repo_audit", "skill_project_reality"])

    def test_recommend_skill_no_match(self):
        skill = self.model.recommend_skill("completely_unrelated_task", {})
        self.assertIsNone(skill)


class TestSkillDefinitionModel(unittest.TestCase):
    def test_skill_definition_to_from_dict(self):
        skill = SkillDefinition(
            id="skill_test",
            objective="Test objective",
            inputs={"param": "str"},
            constraints=["constraint1"],
            decision_rules=["rule1", "rule2"],
            tools=["tool1", "tool2"],
            outputs={"result": "str"},
            verification="test",
            approval_boundary="read-only",
            failure_handling={"retry": 1},
            context_refs=["ref1"],
            version=3,
        )
        d = skill.to_dict()
        self.assertEqual(d["id"], "skill_test")
        self.assertEqual(d["objective"], "Test objective")
        self.assertEqual(d["version"], 3)
        self.assertEqual(d["decision_rules"], ["rule1", "rule2"])

        restored = SkillDefinition.from_dict(d)
        self.assertEqual(restored.id, "skill_test")
        self.assertEqual(restored.version, 3)
        self.assertEqual(restored.decision_rules, ["rule1", "rule2"])
        self.assertEqual(restored.context_refs, ["ref1"])


if __name__ == "__main__":
    unittest.main()