"""Task 7: staged verification pipeline (+ Task 5 engine-wiring debt).

Pipeline: VerifyStage enum + VERIFY_STAGES order + DISALLOWED_VERIFY_COMMANDS
policy gate + stage_commands + Verifier.run_verify_pipeline aggregating
per-stage evidence into one VerificationResult. Truthfulness: un-run stages
are never marked passed; disallowed commands never execute.

Part B: the engine populates the recorded Task 5 Execution fields
(mode/authorization/result_summary/files_changed/git_diff_summary plus the
normalize_result pair in output) after every adapter run.
"""

import tempfile
import unittest
from pathlib import Path

from agentos.models import Objective, new_id
from agentos.task_builder import ProjectContext
from agentos.verify import (
    DISALLOWED_VERIFY_COMMANDS,
    NOT_APPLICABLE,
    VERIFY_STAGES,
    VerifyStage,
    Verifier,
    stage_commands,
)


def _objective(**over):
    base = {
        "id": new_id("obj"),
        "title": "Verify the thing",
        "description": "Pipeline verification target.",
    }
    base.update(over)
    return Objective(**base)


def _stage_status(result, stage):
    """Status prefix of a stage's evidence detail (text before the first ':')."""
    kind = f"stage:{stage.value if isinstance(stage, VerifyStage) else stage}"
    for item in result.evidence:
        if item.kind == kind:
            return item.detail.split(":", 1)[0]
    return None


def _stage_statuses(result, stage):
    kind = f"stage:{stage.value if isinstance(stage, VerifyStage) else stage}"
    return [
        item.detail.split(":", 1)[0]
        for item in result.evidence
        if item.kind == kind
    ]


class TestVerifyStages(unittest.TestCase):
    def test_stage_set_and_order(self):
        self.assertEqual(
            [s for s in VERIFY_STAGES],
            [
                VerifyStage.STATIC,
                VerifyStage.TEST,
                VerifyStage.TYPECHECK,
                VerifyStage.LINT,
                VerifyStage.BUILD,
                VerifyStage.RUNTIME,
                VerifyStage.DIFF_REVIEW,
                VerifyStage.CUSTOM,
            ],
        )

    def test_disallow_list_exact(self):
        self.assertEqual(
            DISALLOWED_VERIFY_COMMANDS,
            ("git commit", "git push", "rm -rf", "git reset --hard"),
        )

    def test_stage_commands_test_mentions_pytest(self):
        commands = stage_commands(VerifyStage.TEST, "pyproject")
        blob = " ".join(commands)
        self.assertTrue(
            "pytest" in blob or "unittest" in blob,
            f"TEST commands on pyproject must mention pytest/unittest: {commands!r}",
        )


class TestVerifyPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.verifier = Verifier()

    def tearDown(self):
        self.tmp.cleanup()

    def test_echo_custom_stage_returns_verified_with_evidence(self):
        objective = _objective(
            context={"verify_commands": ["echo pipeline-ok"]},
        )
        result = self.verifier.run_verify_pipeline(
            objective, str(self.root), stages=[VerifyStage.CUSTOM]
        )
        self.assertTrue(result.verified)
        self.assertTrue(len(result.evidence) > 0)
        self.assertEqual(_stage_status(result, VerifyStage.CUSTOM), "passed")

    def test_disallowed_commands_rejected_and_never_executed(self):
        import agentos.verify as verify_module

        calls = []
        real_runner = verify_module._run_command

        def spy(cmd, cwd, timeout):
            calls.append(cmd)
            return real_runner(cmd, cwd, timeout)

        objective = _objective(
            context={"verify_commands": ["git push origin main", "git commit -m x"]},
        )
        verify_module._run_command = spy
        try:
            result = self.verifier.run_verify_pipeline(
                objective, str(self.root), stages=[VerifyStage.CUSTOM]
            )
        finally:
            verify_module._run_command = real_runner
        self.assertEqual(calls, [])
        self.assertFalse(result.verified)
        blob = " ".join(e.detail for e in result.evidence)
        self.assertIn("policy_block", blob)
        self.assertEqual(
            _stage_status(result, VerifyStage.CUSTOM), "policy_block"
        )

    def test_empty_project_static_reported_test_not_applicable(self):
        objective = _objective()
        result = self.verifier.run_verify_pipeline(
            objective,
            str(self.root),
            stages=[VerifyStage.STATIC, VerifyStage.TEST],
        )
        # STATIC ran and reported an outcome.
        self.assertIsNotNone(_stage_status(result, VerifyStage.STATIC))
        # TEST never ran: exactly NOT_APPLICABLE, never passed.
        self.assertEqual(_stage_status(result, VerifyStage.TEST), NOT_APPLICABLE)
        self.assertEqual(NOT_APPLICABLE, "NOT_APPLICABLE")
        self.assertNotIn("passed", _stage_statuses(result, VerifyStage.TEST))

    def test_unrun_stages_never_marked_passed(self):
        objective = _objective()
        result = self.verifier.run_verify_pipeline(
            objective, str(self.root)
        )
        self.assertFalse(result.verified)
        kinds = {e.kind for e in result.evidence}
        self.assertTrue(any(k.startswith("stage:") for k in kinds))
        for stage in VERIFY_STAGES:
            statuses = _stage_statuses(result, stage)
            for status in statuses:
                self.assertIn(
                    status, ("passed", "failed", NOT_APPLICABLE, "policy_block")
                )

    def test_project_context_accepted(self):
        context = ProjectContext(
            paths=[], git_root=str(self.root), baseline_docs=[], locked_dirs=[]
        )
        objective = _objective(
            context={"verify_commands": ["echo ctx-ok"]},
        )
        result = self.verifier.run_verify_pipeline(
            objective, context, stages=[VerifyStage.CUSTOM]
        )
        self.assertTrue(result.verified)

    def test_stdout_secrets_redacted_before_persistence(self):
        import agentos.verify as verify_module

        # Secret lives in a file so the command itself carries no secret;
        # only the command's stdout carries it (mirrors stderr redaction).
        secret_value = "hunter2-stdout-marker"
        (self.root / "secret.txt").write_text(
            f"deploy with api_key={secret_value}\n", encoding="utf-8"
        )
        completed = verify_module._run_command(
            "cat secret.txt", self.root, 30.0
        )
        self.assertNotIn(
            secret_value, completed.get("stdout") or "",
            "_run_command stdout must be redacted before persistence",
        )
        objective = _objective(
            context={"verify_commands": ["cat secret.txt"]},
        )
        result = self.verifier.run_verify_pipeline(
            objective, str(self.root), stages=[VerifyStage.CUSTOM]
        )
        self.assertTrue(result.verified)
        blob = " ".join(e.detail for e in result.evidence)
        self.assertNotIn(secret_value, blob)
        self.assertIn("***redacted***", blob)


class TestEnginePopulatesExecutionFields(unittest.TestCase):
    """Part B (Task 5 debt): engine fills the new Execution fields per attempt."""

    def setUp(self):
        from agentos.engine import Engine
        from agentos.events import EventBus
        from agentos.registry import CapabilityRegistry
        from agentos.store import Store

        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "agentos.db")
        self.bus = EventBus(self.store)
        self.registry = CapabilityRegistry(self.store, self.bus)
        self.engine = Engine(self.store, self.bus, self.registry)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _run_echo(self, **context):
        objective = Objective(
            id=new_id("obj"), title="echo test", description="d",
            context={"operation": "echo", **context},
        )
        self.store.save_objective(objective)
        report = self.engine.run(objective.id)
        self.assertEqual(report.state.value, "COMPLETED")
        executions = self.store.list_executions(objective_id=objective.id)
        self.assertTrue(executions)
        return executions[0]

    def test_mode_authorization_summary_and_normalized_pair(self):
        execution = self._run_echo(params={"hello": "world"})
        self.assertEqual(execution.mode, "simulated")
        self.assertEqual(execution.authorization, [])
        self.assertTrue(isinstance(execution.result_summary, str))
        self.assertTrue(execution.result_summary)
        self.assertIn("result_state", execution.output)
        self.assertIn("verification_state", execution.output)
        self.assertEqual(execution.output["result_state"], "PARTIAL")
        self.assertIsInstance(execution.files_changed, list)

    def test_files_changed_wired_from_change_keys(self):
        project = Path(self.tmp.name) / "proj"
        project.mkdir()
        target = project / "note.txt"
        objective = Objective(
            id=new_id("obj"), title="write a file", description="d",
            context={
                "operation": "fs.write",
                "params": {
                    "path": str(target),
                    "content": "proof",
                    "project_path": str(project),
                },
            },
        )
        self.store.save_objective(objective)
        report = self.engine.run(objective.id)
        self.assertEqual(report.state.value, "COMPLETED")
        executions = self.store.list_executions(objective_id=objective.id)
        self.assertTrue(executions)
        execution = executions[0]
        self.assertTrue(execution.files_changed)
        self.assertTrue(
            any("note.txt" in f for f in execution.files_changed),
            f"files_changed should name the produced file: {execution.files_changed!r}",
        )
        self.assertIsInstance(execution.git_diff_summary, str)


if __name__ == "__main__":
    unittest.main()
