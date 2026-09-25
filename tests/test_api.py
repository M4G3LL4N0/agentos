"""INTEGRATION tests: HTTP API over the same core (stdlib server + urllib)."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agentos.api import create_server
from agentos.services import AgentOS


class APITestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.app = AgentOS(home=Path(self.tmp.name) / "home")
        self.app.init()
        self.server = create_server(self.app, host="127.0.0.1", port=0)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.app.close()
        self.tmp.cleanup()

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body or {}).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_health_and_status(self) -> None:
        status, payload = self.call("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["healthy"])
        status, payload = self.call("GET", "/status")
        self.assertEqual(status, 200)
        self.assertIn("counts", payload)

    def test_objective_lifecycle_over_http(self) -> None:
        status, created = self.call(
            "POST",
            "/objectives",
            {"title": "API echo", "operation": "echo", "params": {"a": 1}},
        )
        self.assertEqual(status, 201)
        objective_id = created["objective"]["id"]
        status, listed = self.call("GET", "/objectives")
        self.assertEqual(status, 200)
        self.assertEqual(len(listed["objectives"]), 1)
        status, ran = self.call("POST", f"/objectives/{objective_id}/run", {})
        self.assertEqual(status, 200)
        self.assertEqual(ran["report"]["state"], "COMPLETED")
        status, verified = self.call("POST", f"/objectives/{objective_id}/verify", {})
        self.assertEqual(status, 200)
        self.assertTrue(verified["verification"]["verified"])
        status, inspected = self.call("GET", f"/objectives/{objective_id}")
        self.assertEqual(status, 200)
        self.assertEqual(len(inspected["executions"]), 1)

    def test_delegate_endpoint(self) -> None:
        status, payload = self.call(
            "POST", "/delegate", {"title": "API delegate", "operation": "echo"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["report"]["state"], "COMPLETED")

    def test_failed_run_returns_422_with_report(self) -> None:
        _, created = self.call(
            "POST",
            "/objectives",
            {
                "title": "API fail",
                "operation": "shell.run",
                "params": {"command": "exit 1"},
            },
        )
        objective_id = created["objective"]["id"]
        status, payload = self.call(
            "POST", f"/objectives/{objective_id}/run", {"max_attempts": 1}
        )
        self.assertEqual(status, 422)
        self.assertEqual(payload["report"]["state"], "FAILED")
        status, recovered = self.call(
            "POST", f"/objectives/{objective_id}/recover", {"max_attempts": 1}
        )
        self.assertEqual(status, 422)

    def test_events_plans_patterns_gaps_quality(self) -> None:
        _, created = self.call(
            "POST", "/objectives", {"title": "API probe", "operation": "echo"}
        )
        objective_id = created["objective"]["id"]
        self.call("POST", f"/objectives/{objective_id}/run", {})
        for path in (
            "/events",
            "/plans",
            "/patterns",
            "/gaps",
            f"/objectives/{objective_id}/quality",
            f"/objectives/{objective_id}/gaps",
            "/dashboard",
            "/capabilities",
            "/agents",
        ):
            status, payload = self.call("GET", path)
            self.assertEqual(status, 200, path)
            self.assertTrue(payload["ok"], path)

    def test_unknown_routes_and_objects_404(self) -> None:
        status, payload = self.call("GET", "/nope")
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])
        status, _ = self.call("GET", "/objectives/obj_missing")
        self.assertEqual(status, 404)
        status, payload = self.call("POST", "/objectives", {})
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_cancel_endpoint(self) -> None:
        _, created = self.call(
            "POST", "/objectives", {"title": "API cancel", "operation": "echo"}
        )
        objective_id = created["objective"]["id"]
        status, payload = self.call("POST", f"/objectives/{objective_id}/cancel", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["objective"]["status"], "CANCELLED")


if __name__ == "__main__":
    unittest.main()