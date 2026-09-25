"""MCP registry discovery + certification lifecycle.

Discovery never equals trust: candidates move DISCOVERED -> INSPECTED ->
TESTING -> TESTED -> APPROVED (or REJECTED/REVOKED) only through explicit
calls. Nothing is installed, started, or registered as executable until a
human approves it — and even then the MCP client transport stays DEFERRED,
so approval registers honest metadata, never a live executor.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentos.services import AgentOS


def _app() -> tuple[AgentOS, tempfile.TemporaryDirectory[str]]:
    tmp = tempfile.TemporaryDirectory()
    app = AgentOS(home=Path(tmp.name) / "home")
    app.init()
    return app, tmp


SERVER = {
    "name": "io.example/calculator",
    "title": "Calculator",
    "description": "Adds numbers. No network, no filesystem.",
    "version": "1.2.0",
    "repository": {"url": "https://example.invalid/calc", "source": "example"},
    "packages": [
        {
            "registryType": "npm",
            "identifier": "@example/calc",
            "version": "1.2.0",
            "transport": {"type": "stdio"},
            "runtimeHint": "npx",
        }
    ],
}


class MCPLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app, self.tmp = _app()

    def tearDown(self) -> None:
        self.app.close()
        self.tmp.cleanup()

    def test_discover_without_registry_is_unconfigured(self) -> None:
        result = self.app.mcp_discover("calculator")
        self.assertEqual(result["status"], "UNCONFIGURED")
        self.assertEqual(result["candidates"], [])

    def test_full_lifecycle_discover_inspect_test_approve(self) -> None:
        from agentos import mcp_discovery

        candidate = mcp_discovery.ingest_server(SERVER, source="fixture")
        self.app.store.save_mcp_candidate(candidate)
        self.assertEqual(
            self.app.mcp_inspect(candidate["id"])["candidate"]["state"], "INSPECTED"
        )
        tested = self.app.mcp_test(candidate["id"])
        self.assertEqual(tested["candidate"]["state"], "TESTED")
        self.assertTrue(tested["candidate"]["test_result"]["passed"])
        approved = self.app.mcp_approve(
            candidate["id"], evidence=[{"kind": "human", "grant": "approve"}]
        )
        self.assertEqual(approved["candidate"]["state"], "APPROVED")
        # Approved metadata is registered but NOT executable: no transport,
        # so the cell is honestly unavailable for routing.
        cells = {c["id"]: c for c in self.app.federation_executors(refresh=True)}
        cell = cells.get(f"mcp:{candidate['id']}")
        self.assertIsNotNone(cell)
        self.assertFalse(cell["usable"])

    def test_approve_requires_tested_state(self) -> None:
        from agentos import mcp_discovery

        candidate = mcp_discovery.ingest_server(SERVER, source="fixture")
        self.app.store.save_mcp_candidate(candidate)
        with self.assertRaises(Exception):
            self.app.mcp_approve(candidate["id"], evidence=[{"kind": "human"}])

    def test_approve_requires_evidence(self) -> None:
        from agentos import mcp_discovery

        candidate = mcp_discovery.ingest_server(SERVER, source="fixture")
        self.app.store.save_mcp_candidate(candidate)
        self.app.mcp_inspect(candidate["id"])
        self.app.mcp_test(candidate["id"])
        with self.assertRaises(Exception):
            self.app.mcp_approve(candidate["id"], evidence=[])

    def test_reject_and_revoke(self) -> None:
        from agentos import mcp_discovery

        candidate = mcp_discovery.ingest_server(SERVER, source="fixture")
        self.app.store.save_mcp_candidate(candidate)
        self.assertEqual(
            self.app.mcp_reject(candidate["id"], reason="too broad")["candidate"][
                "state"
            ],
            "REJECTED",
        )
        candidate2 = mcp_discovery.ingest_server(
            {**SERVER, "name": "io.example/calc2"}, source="fixture"
        )
        self.app.store.save_mcp_candidate(candidate2)
        self.app.mcp_inspect(candidate2["id"])
        self.app.mcp_test(candidate2["id"])
        self.app.mcp_approve(candidate2["id"], evidence=[{"kind": "human"}])
        self.assertEqual(
            self.app.mcp_revoke(candidate2["id"], reason="rotated")["candidate"][
                "state"
            ],
            "REVOKED",
        )

    def test_certification_flags_excessive_surface(self) -> None:
        from agentos import mcp_discovery

        bad = dict(SERVER)
        bad["name"] = "io.example/kitchen-sink"
        bad["packages"] = [
            {
                "registryType": "npm",
                "identifier": "@example/kitchen-sink",
                "version": "9.9.9",
                "transport": {"type": "streamable-http"},
                "environmentVariables": [
                    {"name": "API_KEY", "isSecret": True, "isRequired": True}
                ],
            }
            for _ in range(12)
        ]
        candidate = mcp_discovery.ingest_server(bad, source="fixture")
        self.app.store.save_mcp_candidate(candidate)
        self.app.mcp_inspect(candidate["id"])
        tested = self.app.mcp_test(candidate["id"])
        self.assertFalse(tested["candidate"]["test_result"]["passed"])
        self.assertTrue(tested["candidate"]["test_result"]["findings"])

    def test_restart_persistence(self) -> None:
        from agentos import mcp_discovery

        candidate = mcp_discovery.ingest_server(SERVER, source="fixture")
        self.app.store.save_mcp_candidate(candidate)
        db_path = self.app.db_path
        self.app.close()
        app2 = AgentOS(home=self.app.home)
        try:
            listed = {c["id"]: c for c in app2.mcp_candidates()}
            self.assertIn(candidate["id"], listed)
            self.assertEqual(listed[candidate["id"]]["state"], "DISCOVERED")
        finally:
            app2.close()
        self.assertTrue(db_path.exists())


if __name__ == "__main__":
    unittest.main()
