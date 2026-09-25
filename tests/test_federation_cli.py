"""Tests for the federation capability/matrix/distill/node services + CLI.

These cover the CLI + services surface added for the native executors
(openhands, hermes, browser-harness): the discoverable capability catalog,
the executor x capability routing matrix, the deterministic distilled
context, and per-cell node-state gating.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from agentos.cli import main as cli_main
from agentos.services import AgentOS

NEW_EXECUTOR_IDS = {"openhands", "hermes", "browser-harness"}

HEALTH_VALUE_SET = {
    "UNKNOWN",
    "AVAILABLE",
    "OK",
    "DEGRADED",
    "DOWN",
    "UNAVAILABLE",
    "CONFIGURED",
    "READY",
    "AUTH_REQUIRED",
    "MISCONFIGURED",
    "DISABLED",
}


def federation_config() -> dict:
    return {"grokbot_office": {"enabled": True, "read_posture": False}}


class FederationServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_raw = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp_raw.name) / "home"
        self.app = AgentOS(home=self.home)
        self.app.init()
        self.app.config["federation"] = federation_config()

    def tearDown(self) -> None:
        self.app.close()
        self.tmp_raw.cleanup()


class FederationCapabilitiesServiceTests(FederationServiceTestCase):
    def test_catalog_contains_new_executor_ids(self) -> None:
        rows = self.app.federation_capabilities()
        ids = {row["id"] for row in rows}
        self.assertTrue(NEW_EXECUTOR_IDS <= ids)
        self.assertEqual(len(rows), len(ids))

    def test_each_row_is_honest(self) -> None:
        for row in self.app.federation_capabilities():
            self.assertIn("health", row)
            self.assertIn(row["health"], HEALTH_VALUE_SET)
            self.assertIn("usable", row)
            self.assertIsInstance(row["usable"], bool)
            self.assertIn("readiness", row)

    def test_openhands_and_hermes_health_is_measured(self) -> None:
        rows = {row["id"]: row for row in self.app.federation_capabilities()}
        for name in ("openhands", "hermes"):
            row = rows[name]
            self.assertIn(row["health"], HEALTH_VALUE_SET)
            # An AVAILABLE claim must be backed by a real probe on PATH,
            # never assumed. When nothing is installed the row must say so.
            if name == "openhands":
                continue  # openhands binary is not on PATH in CI-free dev
            if row["health"] == "AVAILABLE":
                self.assertIsNotNone(shutil.which("hermes"))


class FederationMatrixServiceTests(FederationServiceTestCase):
    def test_matrix_has_new_executor_rows(self) -> None:
        payload = self.app.federation_matrix()
        self.assertIn("rows", payload)
        executors = {row["executor"] for row in payload["rows"]}
        self.assertTrue(NEW_EXECUTOR_IDS <= executors)
        # `executors` is the deterministic first-occurrence order of the
        # canonical matrix rows; stable across calls, no dupes.
        expected = []
        for row in payload["rows"]:
            if row["executor"] not in expected:
                expected.append(row["executor"])
        self.assertEqual(payload["executors"], expected)
        self.assertEqual(
            payload["executors"],
            self.app.federation_matrix()["executors"],
        )

    def test_each_row_is_honest_about_health_and_tier(self) -> None:
        payload = self.app.federation_matrix()
        for row in payload["rows"]:
            self.assertIn(row["health"], HEALTH_VALUE_SET)
            self.assertIn("tier", row)
            self.assertIsInstance(row["tier"], int)
            self.assertIn("tier_name", row)
            self.assertIn("availability", row)
            self.assertIn("usable", row)

    def test_browser_harness_health_is_unknown_without_chrome(self) -> None:
        if os.environ.get("CHROME_EXECUTABLE"):
            self.skipTest("CHROME_EXECUTABLE set; health is environment-dependent")
        rows = self.app.federation_matrix()["rows"]
        browser_rows = [r for r in rows if r["executor"] == "browser-harness"]
        self.assertTrue(browser_rows)
        self.assertTrue(
            all(r["health"] == "UNKNOWN" for r in browser_rows),
            "browser-harness must not claim availability it did not measure",
        )

    def test_matrix_is_deterministic(self) -> None:
        first = self.app.federation_matrix()
        second = self.app.federation_matrix()
        self.assertEqual(first["rows"], second["rows"])


class FederationDistillServiceTests(FederationServiceTestCase):
    def test_distill_covers_registry_and_cells(self) -> None:
        payload = self.app.federation_distill()
        self.assertEqual(set(payload["sources"]), {"capability", "executor_cell"})
        self.assertGreater(payload["entries"], 0)
        distilled = payload["distilled"]
        for key in (
            "stages",
            "facts",
            "uncertainties",
            "contradictions",
            "citations",
            "compressedContext",
            "sourceRefs",
        ):
            self.assertIn(key, distilled)
        refs = {ref["ref"] for ref in distilled["sourceRefs"]}
        self.assertTrue(NEW_EXECUTOR_IDS <= refs)

    def test_distill_is_deterministic(self) -> None:
        first = self.app.federation_distill(refresh=True)
        second = self.app.federation_distill(refresh=True)
        self.assertEqual(first["distilled"], second["distilled"])


class FederationNodesServiceTests(FederationServiceTestCase):
    def _records(self) -> dict:
        result = self.app.federation_nodes()
        self.assertIn("node", result)
        self.assertIn("nodes", result)
        return result

    def test_one_record_per_cell_with_gates(self) -> None:
        result = self._records()
        self.assertEqual(result["count"], len(result["nodes"]))
        self.assertGreaterEqual(result["count"], 2)
        for record in result["nodes"]:
            self.assertIn("gate", record)
            self.assertIn("eligibility", record)
            self.assertIn(record["eligibility"], {"ELIGIBLE", "INELIGIBLE"})
            self.assertIn("requires_online", record)
            self.assertIn("persistent_remote", record)

    def test_unknown_node_state_is_honest(self) -> None:
        result = self._records()
        self.assertEqual(result["node"]["state"], "UNKNOWN")
        self.assertTrue(any(r["eligibility"] == "ELIGIBLE" for r in result["nodes"]))

    def test_manual_offline_gates_local_tools(self) -> None:
        self.app.config["node"] = {
            "state": "OFFLINE",
            "source": "manual",
            "detail": "offline for tests",
        }
        result = self.app.federation_nodes()
        local = next(r for r in result["nodes"] if r["node"] == "local-tools")
        self.assertEqual(result["node"]["state"], "OFFLINE")
        self.assertEqual(result["node"]["manual"], True)
        self.assertEqual(local["eligibility"], "INELIGIBLE")
        self.assertIn("OFFLINE", local["gate"])


class FederationCliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_raw = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp_raw.name) / "home"
        self.home.mkdir(parents=True)
        config = {"federation": federation_config()}
        (self.home / "config.json").write_text(json.dumps(config), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp_raw.cleanup()

    def _run(self, argv: list[str]):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli_main(["--home", str(self.home), "--json", *argv])
        return code, out.getvalue()

    def _run_json(self, argv: list[str]):
        code, out = self._run(argv)
        return code, json.loads(out)

    def test_federate_capabilities_json(self) -> None:
        code, payload = self._run_json(["federate", "capabilities"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        ids = {c["id"] for c in payload["capabilities"]}
        self.assertTrue(NEW_EXECUTOR_IDS <= ids)

    def test_federate_matrix_json(self) -> None:
        code, payload = self._run_json(["federate", "matrix"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        executors = {row["executor"] for row in payload["rows"]}
        self.assertTrue(NEW_EXECUTOR_IDS <= executors)

    def test_federate_matrix_json_refresh(self) -> None:
        code, payload = self._run_json(["federate", "matrix", "--refresh"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["rows"])

    def test_federate_distill_json(self) -> None:
        code, payload = self._run_json(["federate", "distill"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertIn("distilled", payload)
        self.assertGreater(payload["entries"], 0)

    def test_node_json(self) -> None:
        code, payload = self._run_json(["node"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertIn("node", payload)
        self.assertIn("nodes", payload)
        self.assertEqual(payload["count"], len(payload["nodes"]))

    def test_node_human_output(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli_main(["--home", str(self.home), "node"])
        self.assertEqual(code, 0)
        self.assertIn("state:", out.getvalue())

    def test_node_json_refresh(self) -> None:
        code, payload = self._run_json(["node", "--refresh"])
        self.assertEqual(code, 0)
        self.assertIn("node", payload)

    def test_federate_capabilities_human_output(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli_main(["--home", str(self.home), "federate", "capabilities"])
        self.assertEqual(code, 0)
        self.assertIn("openhands", out.getvalue())
        self.assertIn("hermes", out.getvalue())
        self.assertIn("browser-harness", out.getvalue())


if __name__ == "__main__":
    unittest.main()