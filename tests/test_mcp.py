"""INTEGRATION tests: MCP stdio server routes into the same core."""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentos import mcp_server
from agentos.services import AgentOS


class MCPTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.app = AgentOS(home=Path(self.tmp.name) / "home")
        self.app.init()

    def tearDown(self) -> None:
        self.app.close()
        self.tmp.cleanup()

    def handle(self, method: str, params: dict | None = None, msg_id: int = 1):
        return mcp_server.handle_message(
            self.app, {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
        )


class TestMCPProtocol(MCPTestCase):
    def test_initialize(self) -> None:
        response = self.handle("initialize", {"protocolVersion": "2024-11-05"})
        self.assertEqual(response["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(response["result"]["serverInfo"]["name"], "agentos")

    def test_ping_and_notifications(self) -> None:
        self.assertEqual(self.handle("ping")["result"], {})
        self.assertIsNone(
            mcp_server.handle_message(
                self.app,
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
        )

    def test_unknown_method_errors(self) -> None:
        response = self.handle("nope/method")
        self.assertIn("error", response)

    def test_tools_list_covers_required_concepts(self) -> None:
        response = self.handle("tools/list")
        names = {t["name"] for t in response["result"]["tools"]}
        for required in (
            "inspect",
            "status",
            "capabilities",
            "objectives",
            "create_objective",
            "run",
            "delegate",
            "verify",
            "recover",
            "events",
            "state",
            "plans",
            "patterns",
            "gaps",
        ):
            self.assertIn(required, names)


class TestMCPTools(MCPTestCase):
    def call(self, name: str, args: dict):
        response = self.handle(
            "tools/call", {"name": name, "arguments": args}
        )
        self.assertNotIn("error", response, name)
        return json.loads(response["result"]["content"][0]["text"])

    def test_status_state_capabilities_agents(self) -> None:
        self.assertIn("counts", self.call("status", {}))
        self.assertIn("active_objectives", self.call("state", {}))
        self.assertTrue(len(self.call("capabilities", {})) >= 9)
        agent_ids = {a["id"] for a in self.call("agents", {})}
        self.assertTrue({"opencode", "grok", "openclaw", "xai"} <= agent_ids)

    def test_full_objective_flow_over_mcp(self) -> None:
        created = self.call(
            "create_objective",
            {"title": "MCP echo", "operation": "echo", "params": {"a": 1}},
        )
        objective_id = created["id"]
        objectives = self.call("objectives", {})
        self.assertEqual(len(objectives), 1)
        report = self.call("run", {"objective_id": objective_id})
        self.assertEqual(report["state"], "COMPLETED")
        verification = self.call("verify", {"objective_id": objective_id})
        self.assertTrue(verification["verified"])
        detail = self.call("inspect", {"objective_id": objective_id})
        self.assertEqual(len(detail["executions"]), 1)
        events = self.call("events", {"objective_id": objective_id})
        self.assertTrue(len(events) >= 6)
        plans = self.call("plans", {"objective_id": objective_id})
        self.assertEqual(len(plans), 1)
        self.assertIn("rationale", plans[0])
        patterns = self.call("patterns", {})
        self.assertTrue(len(patterns) >= 1)

    def test_delegate_tool(self) -> None:
        report = self.call("delegate", {"title": "MCP delegate", "operation": "echo"})
        self.assertEqual(report["state"], "COMPLETED")

    def test_recover_and_gaps(self) -> None:
        created = self.call(
            "create_objective",
            {
                "title": "MCP fail",
                "operation": "shell.run",
                "params": {"command": "exit 1"},
            },
        )
        objective_id = created["id"]
        report = self.call("run", {"objective_id": objective_id, "max_attempts": 1})
        self.assertEqual(report["state"], "FAILED")
        recovered = self.call("recover", {"objective_id": objective_id, "max_attempts": 1})
        self.assertEqual(recovered["state"], "FAILED")

    def test_gap_tool_after_blocked(self) -> None:
        created = self.call(
            "create_objective", {"title": "MCP gap", "operation": "teleport.run"}
        )
        report = self.call("run", {"objective_id": created["id"]})
        self.assertEqual(report["state"], "BLOCKED")
        gaps = self.call("gaps", {"objective_id": created["id"]})
        self.assertEqual(len(gaps), 1)
        self.assertIn("teleport.run", gaps[0]["operation"])

    def test_unknown_tool_errors(self) -> None:
        response = self.handle(
            "tools/call", {"name": "teleport", "arguments": {}}
        )
        self.assertIn("error", response)

    def test_unknown_objective_errors(self) -> None:
        response = self.handle(
            "tools/call",
            {"name": "run", "arguments": {"objective_id": "obj_missing"}},
        )
        self.assertIn("error", response)


class TestMCPStdioLoop(MCPTestCase):
    def test_serve_stdio_end_to_end(self) -> None:
        script = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "status", "arguments": {}},
                    }
                ),
                "not json at all",
            ]
        )
        with mock.patch("sys.stdin", io.StringIO(script)):
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                code = mcp_server.serve_stdio(self.app)
        self.assertEqual(code, 0)
        lines = [json.loads(line) for line in out.getvalue().strip().splitlines()]
        self.assertEqual([r["id"] for r in lines], [1, 2, 3])
        self.assertIn("counts", json.loads(lines[2]["result"]["content"][0]["text"]))


if __name__ == "__main__":
    unittest.main()