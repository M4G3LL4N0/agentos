"""Tests for the federated agent fabric (envelopes, routing, cells, A2A)."""

import contextlib
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path

from agentos.cli import main as cli_main
from agentos.federation import (
    A2AClient,
    DataClass,
    ExecutionClass,
    ExecutorCell,
    JobEnvelope,
    LatencyClass,
    QualityFloor,
    ResultEnvelope,
    ResultStatus,
    USAGE_BAND_PENALTY,
    create_a2a_server,
    default_tier,
    export_telemetry,
    job_from_task_packet,
    make_executor_cell,
    parse_result_packet,
    parse_task_packet,
    plan_before_premium,
    resolve_route,
    result_from_packet,
    score_executor_cell,
    serialize_result_packet,
    serialize_task_packet,
    tier_name,
    usage_scarcity_penalty,
)
from agentos.services import AgentOS


def make_home() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory()


def base_job(**overrides):
    data = {
        "requester": "tester",
        "targetCapability": "echo",
        "deliverable": "hello federation",
        "dataClass": "INTERNAL",
    }
    data.update(overrides)
    return JobEnvelope.from_dict(data)


def cells_fixture() -> list[ExecutorCell]:
    return [
        make_executor_cell(
            id="local-tools",
            provider="shell",
            capability_ids=["shell", "filesystem", "echo"],
            supported_operations=[
                "shell.run",
                "fs.read",
                "fs.write",
                "fs.exists",
                "fs.list",
                "echo",
            ],
            adapter="shell",
            health="OK",
            tier=1,
            data_class_max=DataClass.HIGHLY_SENSITIVE.value,
            latency_class=LatencyClass.INTERACTIVE.value,
            persistence=True,
            cost_hint={"per_run": 0},
        ),
        make_executor_cell(
            id="grokbot-office",
            provider="grokbot-office",
            capability_ids=["grokbot-office.route"],
            supported_operations=["grokbot-office.route"],
            adapter="grokbot-office",
            health="AVAILABLE",
            data_class_max=DataClass.CONFIDENTIAL.value,
            quality_ladder=QualityFloor.VERIFIED.value,
            latency_class=LatencyClass.BATCH.value,
            persistence=False,
            cost_hint={"per_run": 0},
            usage={"band": "CONSERVE", "used_pct": 79},
            is_external=True,
        ),
    ]


class EnvelopeTests(unittest.TestCase):
    def test_envelope_round_trip(self) -> None:
        job = base_job(
            compactContext="keep it short",
            contextRefs=["ref:wave-2.md"],
            priority="HIGH",
        )
        rebuilt = JobEnvelope.from_dict(job.to_dict())
        self.assertEqual(rebuilt.to_dict(), job.to_dict())

    def test_envelope_requires_deliverable(self) -> None:
        with self.assertRaises(ValueError):
            base_job(deliverable="")

    def test_envelope_rejects_credentials(self) -> None:
        with self.assertRaises(ValueError):
            base_job(compactContext="use the sk-1234567890abcdefghijkl key")
        with self.assertRaises(ValueError):
            base_job(contextRefs=["api_key=super-secret-value"])

    def test_result_confidence_validation(self) -> None:
        with self.assertRaises(ValueError):
            ResultEnvelope(
                jobId="j1", executor="x", confidence=1.5
            )
        with self.assertRaises(ValueError):
            ResultEnvelope(jobId="j1", executor="x", confidence=-0.1)
        ok = ResultEnvelope(jobId="j1", executor="x", confidence=0.5)
        self.assertEqual(ok.confidence, 0.5)

    def test_invalid_data_class(self) -> None:
        with self.assertRaises(ValueError):
            base_job(dataClass="NO_SUCH_CLASS")


class PacketTests(unittest.TestCase):
    def test_task_packet_round_trip(self) -> None:
        job = base_job(
            requester="ChiefOfStaff",
            targetCapability="grokbot-office.route",
            compactContext="offline continuation",
            deliverable="handoff plan",
            priority="HIGH",
            dataClass="CONFIDENTIAL",
            latencyClass="BATCH",
        )
        line = serialize_task_packet(job)
        self.assertTrue(line.startswith("TASK|"))
        parsed = parse_task_packet(line)
        self.assertEqual(parsed["from"], "ChiefOfStaff")
        self.assertEqual(parsed["to"], "grokbot-office.route")
        self.assertIn("offline continuation", parsed["context"])
        rebuilt = job_from_task_packet(line)
        self.assertEqual(rebuilt.deliverable, "handoff plan")
        self.assertEqual(rebuilt.targetCapability, "grokbot-office.route")

    def test_result_packet_sentinel(self) -> None:
        result = ResultEnvelope(
            jobId="j1",
            executor="grokbot-office",
            verifiedFacts=["fact one", "fact two"],
            action="none",
            confidence=None,
            completedAt="2026-01-01T00:00:00Z",
        )
        line = serialize_result_packet(result)
        self.assertTrue("|-1" in line)
        parsed = parse_result_packet(line)
        self.assertEqual(parsed["facts"], ["fact one", "fact two"])
        self.assertEqual(parsed["confidence"], "-1")
        rebuilt = result_from_packet("j1", line)
        self.assertIsNone(rebuilt.confidence)
        self.assertEqual(rebuilt.blocker, None)

    def test_result_packet_blocker(self) -> None:
        result = ResultEnvelope(
            jobId="j1",
            executor="a2a-loopback",
            verifiedFacts=["accepted"],
            action="none",
            confidence=0.9,
            blocker="review only",
            completedAt="2026-01-01T00:00:00Z",
        )
        line = serialize_result_packet(result)
        self.assertTrue(line.endswith("|review only"))
        self.assertEqual(parse_result_packet(line)["blocker"], "review only")

    def test_invalid_packet_kind(self) -> None:
        with self.assertRaises(ValueError):
            parse_task_packet("ESCAPE|from->to|goal|ctx|constraints|deliverable")


class RoutingTests(unittest.TestCase):
    def test_default_tiers(self) -> None:
        self.assertEqual(default_tier("shell", ["shell"]), 1)
        self.assertEqual(default_tier("opencode", []), 3)
        self.assertEqual(default_tier("xai", []), 4)
        self.assertEqual(default_tier("grokbot-office", []), 5)
        self.assertEqual(default_tier("a2a-peer", []), 6)

    def test_cheapest_verified_tier_wins(self) -> None:
        job = base_job(targetCapability="echo")
        cells = cells_fixture()
        decision = resolve_route(job, cells)
        self.assertEqual(decision.chosen.id, "local-tools")
        self.assertEqual(decision.tier, 1)
        self.assertFalse(decision.premiumGated)
        self.assertIn("health=OK", decision.rationale)

    def test_external_cell_excluded_for_hs(self) -> None:
        cells = cells_fixture()
        hs_echo = base_job(targetCapability="echo", dataClass="HIGHLY_SENSITIVE")
        self.assertEqual(resolve_route(hs_echo, cells).chosen.id, "local-tools")
        hs_grok = base_job(
            targetCapability="grokbot-office.route",
            dataClass="HIGHLY_SENSITIVE",
            latencyClass="BATCH",
        )
        decision = resolve_route(hs_grok, cells)
        self.assertTrue(
            decision.chosen is None or decision.chosen.id != "grokbot-office"
        )

    def test_scoring_tiebreak_deterministic(self) -> None:
        cells = [
            make_executor_cell(
                id="b", provider="shell", health="OK", tier=1, cost_hint={"per_run": 0}
            ),
            make_executor_cell(
                id="a", provider="shell", health="OK", tier=1, cost_hint={"per_run": 0}
            ),
        ]
        job = base_job(targetCapability="any")
        first = resolve_route(job, cells[:])
        second = resolve_route(job, list(reversed(cells)))
        self.assertEqual(first.chosen.id, second.chosen.id)
        self.assertEqual(score_executor_cell(job, cells[0])[0], score_executor_cell(job, cells[1])[0])

    def test_usage_scarcity_penalty(self) -> None:
        scarce = make_executor_cell(
            id="g", provider="grokbot-office", usage={"band": "CONSERVE"}, health="OK"
        )
        job = base_job(targetCapability="any")
        self.assertEqual(
            usage_scarcity_penalty(scarce, job, approved=False),
            USAGE_BAND_PENALTY["CONSERVE"],
        )
        self.assertEqual(
            usage_scarcity_penalty(scarce, job, approved=True), 0.0
        )
        override_job = base_job(
            targetCapability="any",
            compactContext="offline continuation of a long task",
        )
        self.assertLess(
            usage_scarcity_penalty(scarce, override_job, approved=False),
            USAGE_BAND_PENALTY["CONSERVE"],
        )

    def test_premium_gated_plan(self) -> None:
        job = base_job(
            targetCapability="grokbot-office.route",
            dataClass="CONFIDENTIAL",
            latencyClass="BATCH",
            executionClass="PREMIUM",
        )
        cells = cells_fixture()
        decision = resolve_route(job, cells, approval_evidence=[])
        self.assertEqual(decision.chosen.id, "grokbot-office")
        self.assertTrue(decision.premiumGated)
        plan = plan_before_premium(job, decision, len(cells))
        self.assertIn("plan_before_premium", plan["plan"])
        self.assertTrue(plan["premiumGated"])
        self.assertFalse(plan["approved"])

    def test_approved_premium_not_gated(self) -> None:
        job = base_job(
            targetCapability="grokbot-office.route",
            dataClass="CONFIDENTIAL",
            latencyClass="BATCH",
            executionClass="PREMIUM",
        )
        decision = resolve_route(
            job, cells_fixture(), approval_evidence=[{"kind": "human_approved", "grant": "live"}]
        )
        self.assertFalse(decision.premiumGated)
        self.assertTrue(decision.approved)

    def test_forbidden_executor_excluded(self) -> None:
        job = base_job(targetCapability="echo", forbiddenExecutors=["local-tools"])
        decision = resolve_route(job, cells_fixture())
        self.assertIn("forbidden=local-tools", decision.gates)

    def test_no_executor_passes(self) -> None:
        job = base_job(targetCapability="mystery.run", executionClass="PREMIUM")
        decision = resolve_route(job, cells_fixture())
        self.assertIsNone(decision.chosen)
        self.assertEqual(decision.rationale, "no executor passes routing gates")


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_raw = make_home()
        self.home = Path(self.tmp_raw.name) / "home"
        self.app = AgentOS(home=self.home)

    def tearDown(self) -> None:
        self.app.close()
        self.tmp_raw.cleanup()

    def test_job_persists_across_restart(self) -> None:
        job = base_job(targetCapability="echo", deliverable="persist me")
        self.app.store.save_federation_job(job, "ROUTED", decision={"chosen": "local-tools"})
        self.app.close()
        reopened = AgentOS(home=self.home)
        try:
            record = reopened.store.get_federation_job(job.id)
            self.assertIsNotNone(record)
            self.assertEqual(record["status"], "ROUTED")
            self.assertEqual(record["decision"]["chosen"], "local-tools")
        finally:
            reopened.close()

    def test_cell_persists(self) -> None:
        cell = make_executor_cell(id="peer-a", provider="a2a", health="OK")
        self.app.store.save_federation_cell(cell)
        reloaded = self.app.store.list_federation_cells()
        self.assertEqual(reloaded[0].id, "peer-a")
        self.assertEqual(reloaded[0].provider, "a2a")

    def test_result_stored_by_job(self) -> None:
        result = ResultEnvelope(
            jobId="job_x", executor="local-tools", status="COMPLETED", confidence=0.8
        )
        self.app.store.save_federation_result(result)
        rows = self.app.store.list_federation_results(job_id="job_x")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result"]["executor"], "local-tools")


class A2ATests(unittest.TestCase):
    def _serve(self):
        server = create_a2a_server(cells_fixture(), port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def test_a2a_client_loopback(self) -> None:
        server, thread = self._serve()
        try:
            port = server.server_address[1]
            client = A2AClient(f"http://127.0.0.1:{port}")
            result = client.send_task(base_job(targetCapability="echo"))
            self.assertEqual(result.status, ResultStatus.COMPLETED.value)
            self.assertIn("accepted", " ".join(result.verifiedFacts))
        finally:
            server.shutdown()
            server.server_close()

    def test_a2a_rejects_live_remote_jobs(self) -> None:
        server, thread = self._serve()
        try:
            port = server.server_address[1]
            client = A2AClient(f"http://127.0.0.1:{port}")
            job = base_job(targetCapability="echo", executionClass="PREMIUM")
            result = client.send_task(job)
            self.assertEqual(result.status, ResultStatus.BLOCKED.value)
        finally:
            server.shutdown()
            server.server_close()

    def test_a2a_agents_hide_forbidden_ops(self) -> None:
        server, thread = self._serve()
        try:
            port = server.server_address[1]
            body = json.dumps({"agents": self._fetch_agents(port)})
            self.assertNotIn("shell.run", body)
            self.assertNotIn("grokbot-office.activate", body)
        finally:
            server.shutdown()
            server.server_close()

    def _fetch_agents(self, port: int) -> list:
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/agents", timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload["agents"]


class GrokBotAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        from agentos.adapters.grokbot_office import (
            FORBIDDEN_GROKBOT_COMMANDS,
            READ_ONLY_COMMANDS,
            GrokBotOfficeAdapter,
        )

        self.adapter = GrokBotOfficeAdapter
        self.READ_ONLY = READ_ONLY_COMMANDS
        self.FORBIDDEN = FORBIDDEN_GROKBOT_COMMANDS
        self.tmp_raw = make_home()

    def tearDown(self) -> None:
        self.tmp_raw.cleanup()

    def _fixture(self) -> Path:
        root = Path(self.tmp_raw.name) / "office"
        (root / "src").mkdir(parents=True)
        (root / "registry").mkdir()
        (root / "node_modules" / ".bin").mkdir(parents=True)
        (root / "package.json").write_text("{}", encoding="utf-8")
        (root / "src" / "cli.ts").write_text("", encoding="utf-8")
        (root / "registry" / "roles.yaml").write_text("", encoding="utf-8")
        (root / "node_modules" / ".bin" / "tsx").write_text("#!/bin/sh\n", encoding="utf-8")
        return root

    def test_probe_unavailable_when_repo_missing(self) -> None:
        adapter = self.adapter({"dir": str(Path(self.tmp_raw.name) / "absent")})
        self.assertEqual(adapter.probe().value, "UNAVAILABLE")

    def test_simulated_dry_run_uses_allowlist(self) -> None:
        from agentos.adapters.base import ExecutionMode, ExecutionRequest

        adapter = self.adapter({"dir": str(self._fixture())})
        for operation in self.READ_ONLY:
            result = adapter.execute(
                operation,
                ExecutionRequest(operation=operation, mode=ExecutionMode.SIMULATED),
            )
            self.assertTrue(result.ok, f"{operation} should dry-run ok")
            command = result.output["command"]
            self.assertFalse(any(f in command for f in self.FORBIDDEN))
        result = adapter.execute(
            "grokbot-office.usage:set",
            ExecutionRequest(
                operation="grokbot-office.usage:set", mode=ExecutionMode.SIMULATED
            ),
        )
        self.assertFalse(result.ok)
        self.assertIn("does not support", result.error)

    def test_live_requires_authorization(self) -> None:
        from agentos.adapters.base import ExecutionMode, ExecutionRequest

        root = self._fixture()
        adapter = self.adapter({"dir": str(root)})
        result = adapter.execute(
            "grokbot-office.mode",
            ExecutionRequest(operation="grokbot-office.mode", mode=ExecutionMode.LIVE),
        )
        self.assertFalse(result.ok)
        self.assertIn("authorization", result.error)

    def test_live_read_only_argv_never_forbidden(self) -> None:
        from unittest import mock

        from agentos.adapters.base import ExecutionMode, ExecutionRequest
        from agentos.adapters.grokbot_office import FORBIDDEN_GROKBOT_COMMANDS

        root = self._fixture()
        adapter = self.adapter({"dir": str(root)})
        seen: list[list[str]] = []

        def fake_run(argv, timeout=60, cwd=None):
            seen.append(argv)
            return {"stdout": "mode: CONSERVE  (79% used / 21% remaining)\n", "stderr": "", "exit_code": 0, "duration_ms": 1, "timed_out": False}

        with mock.patch("agentos.adapters.grokbot_office.run_command", side_effect=fake_run):
            for operation in self.READ_ONLY:
                result = adapter.execute(
                    operation,
                    ExecutionRequest(
                        operation=operation,
                        mode=ExecutionMode.LIVE,
                        authorization=[{"kind": "live", "grant": "approved"}],
                        params={"subject": "research wave-2"},
                    ),
                )
                self.assertTrue(result.ok, f"{operation} live should be allowed")
        flat = " ".join(" ".join(a) for a in seen)
        for forbidden in FORBIDDEN_GROKBOT_COMMANDS:
            self.assertNotIn(forbidden, flat)


class FederationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_raw = make_home()
        self.home = Path(self.tmp_raw.name) / "home"
        self.app = AgentOS(home=self.home)
        self.app.init()
        # Grok availability is environment-dependent (honest probe), so premium
        # routing/hold logic is covered by a deterministic synthetic premium
        # cell. Nothing here executes Grok or fabricates its availability.
        self.app.config["federation"] = {
            "grokbot_office": {"enabled": True, "read_posture": False},
            "peers": [
                {
                    "id": "test-premium",
                    "provider": "test-premium",
                    "description": "deterministic synthetic premium cell for tests",
                    "capability_ids": ["test.premium"],
                    "supported_operations": ["test.premium"],
                    "adapter": "echo",
                    "health": "AVAILABLE",
                    "tier": 5,
                    "data_class_max": "CONFIDENTIAL",
                    "quality_ladder": "VERIFIED",
                    "latency_class": "BATCH",
                    "persistence": False,
                    "requires_online": True,
                    "persistent_remote": False,
                    "local": False,
                    "usage": {"band": "NORMAL"},
                    "is_external": False,
                }
            ],
        }

    def tearDown(self) -> None:
        self.app.close()
        self.tmp_raw.cleanup()

    def test_executors_present(self) -> None:
        cells = self.app.federation_executors(refresh=True)
        ids = {c["id"] for c in cells}
        self.assertTrue({"local-tools", "grokbot-office"} <= ids)
        grok = next(c for c in cells if c["id"] == "grokbot-office")
        self.assertTrue(grok["premium"])
        self.assertEqual(grok["tier_name"], "T5_SCARCE_PREMIUM")

    def test_route_local_vs_grokbot(self) -> None:
        local = self.app.federate_route(
            {"requester": "tester", "targetCapability": "echo", "deliverable": "x"}
        )
        self.assertEqual(local["decision"]["chosen"], "local-tools")
        routed = self.app.federate_route(
            {
                "requester": "tester",
                "targetCapability": "test.premium",
                "deliverable": "route this",
                "dataClass": "CONFIDENTIAL",
                "latencyClass": "BATCH",
                "executionClass": "PREMIUM",
            }
        )
        self.assertEqual(routed["decision"]["chosen"], "test-premium")
        self.assertTrue(routed["decision"]["premiumGated"])

    def test_unavailable_grokbot_is_never_chosen(self) -> None:
        cells = {c["id"]: c for c in self.app.federation_executors(refresh=True)}
        routed = self.app.federate_route(
            {
                "requester": "tester",
                "targetCapability": "grokbot-office.route",
                "deliverable": "route",
                "dataClass": "CONFIDENTIAL",
                "latencyClass": "BATCH",
            }
        )
        if cells.get("grokbot-office", {}).get("health") in (
            "DOWN",
            "UNAVAILABLE",
            "DISABLED",
        ):
            self.assertNotEqual(routed["decision"]["chosen"], "grokbot-office")
        else:
            self.assertEqual(routed["decision"]["chosen"], "grokbot-office")

    def test_hs_never_external(self) -> None:
        routed = self.app.federate_route(
            {
                "requester": "tester",
                "targetCapability": "grokbot-office.route",
                "deliverable": "route",
                "dataClass": "HIGHLY_SENSITIVE",
                "latencyClass": "BATCH",
            }
        )
        self.assertNotEqual(routed["decision"]["chosen"], "grokbot-office")

    def test_submit_persists_and_returns_result(self) -> None:
        submitted = self.app.federate_submit(
            {"requester": "tester", "targetCapability": "echo", "deliverable": "hi"}
        )
        self.assertTrue(submitted["executed"])
        self.assertEqual(submitted["result"]["status"], "COMPLETED")
        jobs = self.app.federation_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertIn(jobs[0]["status"], {"COMPLETED", "BLOCKED"})
        self.assertEqual(jobs[0]["envelope"]["deliverable"], "hi")

    def test_premium_not_auto_executed(self) -> None:
        submitted = self.app.federate_submit(
            {
                "requester": "tester",
                "targetCapability": "test.premium",
                "deliverable": "route: offline continuation",
                "dataClass": "CONFIDENTIAL",
                "latencyClass": "BATCH",
                "executionClass": "PREMIUM",
            }
        )
        self.assertFalse(submitted["executed"])
        self.assertIn("premium tier requires", submitted["note"])

    def test_packet_service(self) -> None:
        parsed = self.app.federation_packet(
            "RESULT|grokbot-office->ChiefOfStaff|done|none|0.9"
        )
        self.assertEqual(parsed["kind"], "RESULT")
        self.assertEqual(parsed["confidence"], "0.9")
        with self.assertRaises(Exception):
            self.app.federation_packet("NOTAPACKET|x")

    def test_telemetry_measured_only(self) -> None:
        telemetry = self.app.federation_telemetry()
        self.assertIn("measured_cost_samples", telemetry)
        self.assertIn("note", telemetry)


class CliFederateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_raw = make_home()
        self.home = Path(self.tmp_raw.name) / "home"
        self.home.mkdir(parents=True)
        config = {
            "federation": {"grokbot_office": {"enabled": True, "read_posture": False}}
        }
        (self.home / "config.json").write_text(json.dumps(config), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp_raw.cleanup()

    def _run(self, argv):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli_main(["--home", str(self.home), "--json", *argv])
        return code, json.loads(out.getvalue())

    def test_federate_status(self) -> None:
        code, payload = self._run(["federate", "status"])
        self.assertEqual(code, 0)
        self.assertIn("executors", payload)
        self.assertIn("grokbot-office", payload["executors"])

    def test_federate_route_smoke(self) -> None:
        code, payload = self._run(
            ["federate", "route", "--job", json.dumps({"requester": "cli", "targetCapability": "echo", "deliverable": "x"})]
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["decision"]["chosen"], "local-tools")

    def test_federate_packet_smoke(self) -> None:
        code, payload = self._run(
            ["federate", "packet", "TASK|A->B|goal|ctx|constraints|deliverable"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["kind"], "TASK")

    def test_federate_bad_job_rejected(self) -> None:
        code, payload = self._run(
            ["federate", "route", "--job", json.dumps({"deliverable": "x"})]
        )
        self.assertNotEqual(code, 0)
        self.assertIn("error", payload)


class RegistryTests(unittest.TestCase):
    def test_grokbot_capability_registered(self) -> None:
        from agentos.registry import native_capabilities

        capability = next(c for c in native_capabilities() if c.id == "grokbot-office")
        self.assertEqual(capability.adapter, "grokbot-office")
        self.assertIn("grokbot-office.validate", capability.operations)
        self.assertIn("grokbot-office.bridge-check", capability.operations)


if __name__ == "__main__":
    unittest.main()