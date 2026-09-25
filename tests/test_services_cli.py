"""Tests for the service layer and the CLI command center."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from agentos.cli import main as cli_main
from agentos.engine import AgentOSError
from agentos.services import AgentOS


class ServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.app = AgentOS(home=Path(self.tmp.name) / "home")

    def tearDown(self) -> None:
        self.app.close()
        self.tmp.cleanup()


class TestInitAndStatus(ServiceTestCase):
    def test_init_creates_home_and_capabilities(self) -> None:
        result = self.app.init()
        self.assertTrue(Path(result["home"]).exists())
        self.assertTrue(Path(result["db"]).exists())
        self.assertEqual(
            set(result["capabilities"]),
            {
                "shell",
                "filesystem",
                "echo",
                "opencode",
                "github",
                "xai",
                "grok",
                "openclaw",
                "langgraph",
                "grokbot-office",
                "openhands",
                "hermes",
                "browser-harness",
            },
        )

    def test_status_summary(self) -> None:
        self.app.init()
        self.app.create_objective("first", operation="echo")
        status = self.app.status()
        self.assertEqual(status["counts"]["objectives"], 1)
        self.assertEqual(status["objectives_by_state"].get("READY"), 1)
        self.assertEqual(status["capabilities"], 13)


class TestObjectivesService(ServiceTestCase):
    def test_create_list_get_cancel(self) -> None:
        self.app.init()
        objective = self.app.create_objective(
            "do thing",
            description="desc",
            priority="high",
            operation="echo",
            params={"a": 1},
            constraints=["expected:x"],
        )
        self.assertEqual(objective.status.value, "READY")
        self.assertEqual(objective.context["operation"], "echo")
        self.assertEqual(objective.priority.value, "HIGH")
        listed = self.app.list_objectives()
        self.assertEqual(len(listed), 1)
        fetched = self.app.get_objective(objective.id)
        self.assertEqual(fetched.title, "do thing")
        cancelled = self.app.cancel_objective(objective.id)
        self.assertEqual(cancelled.status.value, "CANCELLED")
        events = self.app.events(objective_id=objective.id)
        self.assertTrue(
            any(e["event_type"] == "objective.cancelled" for e in events)
        )

    def test_create_validates_priority_parent_and_status(self) -> None:
        self.app.init()
        with self.assertRaises(AgentOSError):
            self.app.create_objective("bad", priority="urgent")
        with self.assertRaises(AgentOSError):
            self.app.create_objective("bad", parent_id="obj_missing")
        with self.assertRaises(AgentOSError):
            self.app.get_objective("obj_missing")
        with self.assertRaises(AgentOSError):
            self.app.list_objectives(status="BOGUS")


class TestCapabilitiesService(ServiceTestCase):
    def test_add_run_remove_configured_cli(self) -> None:
        self.app.init()
        capability = self.app.add_configured_cli(
            "greet", "Greeter", "echo hello-cli", verify_expected="hello-cli"
        )
        self.assertEqual(capability.operations, ["greet.run"])
        objective = self.app.create_objective("greet me", operation="greet.run")
        report = self.app.run(objective.id)
        self.assertEqual(report.state.value, "COMPLETED")
        self.assertTrue(self.app.remove_capability("greet"))

    def test_refuses_to_remove_native(self) -> None:
        self.app.init()
        with self.assertRaises(AgentOSError):
            self.app.remove_capability("shell")

    def test_agents_lists_detected_agent_model_capabilities(self) -> None:
        self.app.init()
        agents = {a.id for a in self.app.agents()}
        self.assertEqual(
            agents, {"opencode", "xai", "grok", "openclaw", "grokbot-office", "openhands", "hermes"}
        )


class TestInspectDoctor(ServiceTestCase):
    def test_inspect_after_run(self) -> None:
        self.app.init()
        objective = self.app.create_objective("echo test", operation="echo")
        self.app.run(objective.id)
        detail = self.app.inspect(objective.id)
        self.assertEqual(len(detail["executions"]), 1)
        self.assertEqual(len(detail["verifications"]), 1)
        self.assertEqual(detail["failures"], [])
        self.assertTrue(len(detail["events"]) >= 6)

    def test_doctor_all_pass(self) -> None:
        self.app.init()
        result = self.app.doctor()
        self.assertTrue(result["ok"])
        names = {c["name"] for c in result["checks"]}
        self.assertIn("database_integrity", names)
        self.assertIn("capability:shell", names)


class CLITestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.home = str(Path(self.tmp.name) / "home")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli_main(["--home", self.home, *args])
        return code, out.getvalue(), err.getvalue()

    def run_cli_json(self, *args: str) -> tuple[int, dict]:
        code, out, _ = self.run_cli("--json", *args)
        return code, json.loads(out)

    def test_init_status_and_version(self) -> None:
        code, payload = self.run_cli_json("init")
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        code, payload = self.run_cli_json("status")
        self.assertEqual(code, 0)
        self.assertEqual(payload["counts"]["objectives"], 0)

    def test_objective_lifecycle_via_cli(self) -> None:
        self.run_cli("init")
        code, payload = self.run_cli_json(
            "objective", "create", "CLI echo",
            "--operation", "echo",
            "--params", '{"hello": "cli"}',
        )
        self.assertEqual(code, 0)
        objective_id = payload["objective"]["id"]
        code, payload = self.run_cli_json("objectives")
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["objectives"]), 1)
        code, payload = self.run_cli_json("run", objective_id)
        self.assertEqual(code, 0)
        self.assertEqual(payload["state"], "COMPLETED")
        code, payload = self.run_cli_json("verify", objective_id)
        self.assertEqual(code, 0)
        self.assertTrue(payload["verified"])
        code, payload = self.run_cli_json("inspect", objective_id)
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["executions"]), 1)
        code, payload = self.run_cli_json("events", "--objective", objective_id)
        self.assertEqual(code, 0)
        self.assertTrue(len(payload["events"]) >= 6)
        code, _, _ = self.run_cli("doctor")
        self.assertEqual(code, 0)

    def test_run_failure_exit_code_nonzero(self) -> None:
        self.run_cli("init")
        _, payload = self.run_cli_json(
            "objective", "create", "CLI fail",
            "--operation", "shell.run",
            "--params", '{"command": "exit 1"}',
        )
        code, payload = self.run_cli_json(
            "run", payload["objective"]["id"], "--max-attempts", "1"
        )
        self.assertEqual(code, 1)
        self.assertEqual(payload["state"], "FAILED")

    def test_capabilities_add_run_remove_via_cli(self) -> None:
        self.run_cli("init")
        code, payload = self.run_cli_json(
            "capabilities", "add", "shout",
            "--command", "echo loud-noise",
            "--expect", "loud-noise",
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["capability"]["operations"], ["shout.run"])
        _, created = self.run_cli_json(
            "objective", "create", "shout it", "--operation", "shout.run"
        )
        code, ran = self.run_cli_json("run", created["objective"]["id"])
        self.assertEqual(code, 0)
        self.assertEqual(ran["state"], "COMPLETED")
        code, _, _ = self.run_cli("capabilities", "remove", "shout")
        self.assertEqual(code, 0)

    def test_human_output_is_readable(self) -> None:
        self.run_cli("init")
        _, created = self.run_cli_json(
            "objective", "create", "readable", "--operation", "echo"
        )
        code, out, _ = self.run_cli("run", created["objective"]["id"])
        self.assertEqual(code, 0)
        self.assertIn("COMPLETED", out)
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("objectives:", out)

    def test_unknown_objective_errors(self) -> None:
        self.run_cli("init")
        code, payload = self.run_cli_json("run", "obj_missing")
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])


if __name__ == "__main__":
    unittest.main()