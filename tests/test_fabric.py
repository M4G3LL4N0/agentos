"""Federated fabric tests: cells, scarcity, explain, failover, pipelines,
approvals/trust, capability graph, benchmarks, durable, bridges, doctor.

No paid models, no live Grok requests, no network: every remote/untrusted
surface is exercised against loopback fixtures or pure functions.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from agentos.cli import main as cli_main
from agentos.federation import (
    DataClass,
    ExecutorCell,
    JobEnvelope,
    LatencyClass,
    make_executor_cell,
    resolve_route,
    trust_gate,
)
from agentos.services import AgentOS


def _app() -> tuple[AgentOS, tempfile.TemporaryDirectory[str]]:
    tmp = tempfile.TemporaryDirectory()
    app = AgentOS(home=Path(tmp.name) / "home")
    app.init()
    return app, tmp


def _job(**overrides: object) -> JobEnvelope:
    data = {
        "requester": "tester",
        "targetCapability": "echo",
        "deliverable": "fabric probe",
        "dataClass": "INTERNAL",
    }
    data.update(overrides)
    return JobEnvelope.from_dict(data)


class CellIsolationTests(unittest.TestCase):
    def test_credential_values_rejected_on_cells(self) -> None:
        with self.assertRaises(ValueError):
            ExecutorCell.from_dict(
                {
                    "id": "evil",
                    "provider": "x",
                    "config": {"api_key": "sk-abcdefghij1234567890"},
                }
            )

    def test_configured_cells_load_without_contact(self) -> None:
        app, tmp = _app()
        try:
            app.config["federation"] = {
                "cells": [
                    {
                        "id": "grok-research",
                        "provider": "grokbot-office",
                        "authorized": True,
                        "health": "UNKNOWN",
                        "live_roles": ["IntelligenceChief"],
                        "virtual_role_count": 129,
                        "transport": "a2a",
                    }
                ]
            }
            cells = {c.id: c for c in app._federation_cells(refresh=True)}
            cell = cells["grok-research"]
            self.assertTrue(cell.authorized)
            self.assertEqual(cell.health, "UNKNOWN")
            self.assertEqual(cell.trust, "UNTRUSTED")
        finally:
            app.close()
            tmp.cleanup()

    def test_session_preference_for_authorized_cell(self) -> None:
        generic = make_executor_cell(
            id="generic",
            provider="shell",
            health="OK",
            tier=5,
            supported_operations=["shell.run"],
        )
        specialized = make_executor_cell(
            id="grok-eng",
            provider="grokbot-office",
            health="OK",
            tier=5,
            supported_operations=["shell.run"],
            authorized=True,
            config={"sessions": ["github"]},
        )
        job = _job(
            targetCapability="shell.run",
            deliverable="rotate session:github deploy key",
        )
        decision = resolve_route(job, [generic, specialized])
        self.assertEqual(decision.chosen.id, "grok-eng")
        self.assertIn("session-match", decision.rationale)

    def test_no_cross_cell_credential_assumptions(self) -> None:
        first = make_executor_cell(id="a", provider="x", config={"label": "one"})
        second = make_executor_cell(id="b", provider="x", config={"label": "two"})
        self.assertIsNot(first.config, second.config)
        self.assertNotEqual(first.to_dict()["config"], second.to_dict()["config"])


class ScarcityTests(unittest.TestCase):
    def test_grok_primary_scarcity_high_conserve(self) -> None:
        from agentos import scarcity as scarcity_mod

        cell = make_executor_cell(
            id="grok-primary",
            provider="grokbot-office",
            health="AVAILABLE",
            tier=5,
            usage={"band": "CONSERVE", "used_pct": 79},
        )
        profile = scarcity_mod.scarcity_profile(cell)
        self.assertEqual(profile["quotaScarcity"], "HIGH")
        self.assertEqual(profile["usedPct"], 79)
        self.assertEqual(profile["remainingPct"], 21.0)
        penalty, _ = scarcity_mod.reset_adjustment(cell, mode="PRESERVE")
        self.assertGreaterEqual(penalty, 20.0)

    def test_harvest_disabled_by_default(self) -> None:
        from agentos import scarcity as scarcity_mod

        allowed, reason = scarcity_mod.harvest_allowed({})
        self.assertFalse(allowed)
        self.assertIn("not explicitly enabled", reason)
        cell = make_executor_cell(id="g", provider="grokbot-office", tier=5)
        penalty, note = scarcity_mod.reset_adjustment(cell, mode="HARVEST", config={})
        self.assertIn("refused", note)
        self.assertGreater(penalty, 0)

    def test_harvest_requires_all_guards(self) -> None:
        from agentos import scarcity as scarcity_mod

        full = {
            "harvest": {
                "enabled": True,
                "reset_known": True,
                "allowance_expiring": True,
                "queued_work": ["CapabilityScout"],
            }
        }
        allowed, _ = scarcity_mod.harvest_allowed(full)
        self.assertTrue(allowed)
        partial = {"harvest": {**full["harvest"], "queued_work": []}}
        self.assertFalse(scarcity_mod.harvest_allowed(partial)[0])

    def test_never_fabricates_money(self) -> None:
        from agentos import scarcity as scarcity_mod

        cell = make_executor_cell(id="x", provider="shell", tier=1)
        profile = scarcity_mod.scarcity_profile(cell)
        blob = json.dumps(profile)
        self.assertNotIn("$", blob)
        self.assertNotIn("token", blob.lower())


class ExplainTests(unittest.TestCase):
    def test_route_explain_shape(self) -> None:
        app, tmp = _app()
        try:
            result = app.federate_route_explain(
                {
                    "requester": "tester",
                    "targetCapability": "echo",
                    "deliverable": "inspect this repo",
                }
            )
            for key in (
                "task_class",
                "required_capabilities",
                "quality_floor",
                "data_class",
                "node_state",
                "candidates",
                "chosen",
                "why",
                "fallback",
                "premium_scarce_required",
                "approval_required",
            ):
                self.assertIn(key, result)
            self.assertEqual(result["chosen"], "local-tools")
            self.assertFalse(result["premium_scarce_required"])
        finally:
            app.close()
            tmp.cleanup()


class FailoverTests(unittest.TestCase):
    def test_falls_back_with_reasons(self) -> None:
        from agentos.failover import FailoverPolicy, run_with_failover

        calls: list[str] = []

        def attempt(candidate: dict) -> tuple[bool, object, str]:
            calls.append(candidate["executor"])
            if candidate["executor"] == "first":
                return False, None, "simulated outage"
            return True, {"ok": True}, "recovered"

        outcome = run_with_failover(
            [
                {"executor": "first", "tier": 1},
                {"executor": "second", "tier": 1},
            ],
            attempt,
            FailoverPolicy(max_attempts=3, max_executor_switches=2),
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.executor, "second")
        self.assertEqual(calls, ["first", "second"])
        self.assertTrue(any("simulated outage" in r for r in outcome.fallback_reasons))

    def test_bounds_terminate(self) -> None:
        from agentos.failover import FailoverPolicy, run_with_failover

        def attempt(candidate: dict) -> tuple[bool, object, str]:
            return False, None, "always down"

        outcome = run_with_failover(
            [{"executor": f"e{i}", "tier": 1} for i in range(9)],
            attempt,
            FailoverPolicy(max_attempts=2, max_executor_switches=1),
        )
        self.assertFalse(outcome.ok)
        self.assertLessEqual(len(outcome.attempts), 2)

    def test_pipeline_executes_bounded_stages(self) -> None:
        from agentos.failover import run_pipeline

        app, tmp = _app()
        try:
            result = run_pipeline(
                [
                    {
                        "requester": "t",
                        "targetCapability": "echo",
                        "deliverable": "stage one",
                    },
                    {
                        "requester": "t",
                        "targetCapability": "echo",
                        "deliverable": "stage two",
                    },
                ],
                app.federate_submit,
            )
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["stages"]), 2)
            self.assertEqual(len(result["artifacts"]), 2)
        finally:
            app.close()
            tmp.cleanup()

    def test_pipeline_rejects_oversize(self) -> None:
        from agentos.failover import run_pipeline

        with self.assertRaises(ValueError):
            run_pipeline(
                [
                    {
                        "requester": "t",
                        "targetCapability": "echo",
                        "deliverable": f"s{i}",
                    }
                    for i in range(9)
                ],
                lambda job: {"executed": True, "result": {"verifiedFacts": ["x"]}},
            )


class ApprovalTrustTests(unittest.TestCase):
    def test_canonical_classes_ordered(self) -> None:
        from agentos.policies import APPROVAL_CLASS_RANK, ApprovalClass

        self.assertLess(
            APPROVAL_CLASS_RANK[ApprovalClass.READ_ONLY.value],
            APPROVAL_CLASS_RANK[ApprovalClass.IRREVERSIBLE.value],
        )

    def test_executors_cannot_reduce_class(self) -> None:
        job = _job(targetCapability="shell.run", dataClass="CONFIDENTIAL")
        cell = make_executor_cell(
            id="remote",
            provider="a2a-remote",
            health="AVAILABLE",
            tier=6,
            is_external=True,
            local=False,
            supported_operations=["shell.run"],
        )
        allowed, reason = trust_gate(cell, job)
        self.assertFalse(allowed)
        self.assertTrue(reason)

    def test_hs_never_to_untrusted_external(self) -> None:
        job = _job(targetCapability="echo", dataClass="HIGHLY_SENSITIVE")
        cell = make_executor_cell(
            id="remote",
            provider="a2a-remote",
            health="AVAILABLE",
            tier=6,
            is_external=True,
            local=False,
            supported_operations=["echo"],
        )
        allowed, _ = trust_gate(cell, job)
        self.assertFalse(allowed)

    def test_local_read_only_passes(self) -> None:
        job = _job(targetCapability="echo", dataClass="INTERNAL")
        cell = make_executor_cell(
            id="local-tools",
            provider="shell",
            health="OK",
            tier=1,
            supported_operations=["echo"],
        )
        allowed, _ = trust_gate(cell, job)
        self.assertTrue(allowed)


class CapabilityGraphTests(unittest.TestCase):
    def test_find_ranks_echo(self) -> None:
        app, tmp = _app()
        try:
            result = app.capability_find("echo")
            self.assertGreater(result["count"], 0)
            self.assertEqual(result["candidates"][0]["executor"], "echo")
            candidate = result["candidates"][0]
            for key in (
                "availability",
                "health",
                "quality_class",
                "cost_scarcity",
                "local_remote",
                "persistence",
                "data_class_max",
                "approval_required",
            ):
                self.assertIn(key, candidate)
        finally:
            app.close()
            tmp.cleanup()

    def test_find_empty_for_unknown_need(self) -> None:
        app, tmp = _app()
        try:
            result = app.capability_find("zz-no-such-capability-zz")
            self.assertEqual(result["count"], 0)
        finally:
            app.close()
            tmp.cleanup()

    def test_explain_unknown_raises(self) -> None:
        app, tmp = _app()
        try:
            with self.assertRaises(Exception):
                app.capability_explain("no-such-capability")
        finally:
            app.close()
            tmp.cleanup()


class BenchmarkTests(unittest.TestCase):
    def test_deterministic_run_persists(self) -> None:
        app, tmp = _app()
        try:
            result = app.benchmark_run("local-worker", "A")
            record = result["record"]
            self.assertEqual(record["status"], "COMPLETED")
            self.assertTrue(record["success"])
            self.assertGreaterEqual(record["latency_ms"], 0.0)
            report = app.benchmark_report(executor="local-worker", workload="A")
            self.assertGreaterEqual(report["runs"], 1)
            stats = report["by_executor"]["local-worker"]
            self.assertEqual(stats["success_rate"], 1.0)
        finally:
            app.close()
            tmp.cleanup()

    def test_other_executors_skipped_honestly(self) -> None:
        app, tmp = _app()
        try:
            result = app.benchmark_run("opencode", "H")
            self.assertEqual(result["record"]["status"], "SKIPPED")
            self.assertIsNone(result["record"]["success"])
        finally:
            app.close()
            tmp.cleanup()

    def test_unknown_workload_rejected(self) -> None:
        app, tmp = _app()
        try:
            with self.assertRaises(Exception):
                app.benchmark_run("local-worker", "Z")
        finally:
            app.close()
            tmp.cleanup()

    def test_restart_persistence(self) -> None:
        app, tmp = _app()
        try:
            app.benchmark_run("local-worker", "B")
            home = app.home
            app.close()
            app2 = AgentOS(home=home)
            try:
                self.assertGreaterEqual(
                    app2.benchmark_report(executor="local-worker")["runs"], 1
                )
            finally:
                app2.close()
        finally:
            tmp.cleanup()


class DurableTests(unittest.TestCase):
    def test_native_backend_checkpoint_resume(self) -> None:
        from agentos import durable as durable_mod

        app, tmp = _app()
        try:
            backend = durable_mod.DurableExecutionBackend(app.store)
            started = backend.start(
                {
                    "requester": "t",
                    "targetCapability": "echo",
                    "deliverable": "durable work",
                }
            )
            backend.checkpoint(started["id"], {"stage": 1})
            resumed = backend.resume(started["id"])
            self.assertEqual(len(resumed["checkpoints"]), 1)
            home = app.home
            app.close()
            app2 = AgentOS(home=home)
            try:
                resumed2 = durable_mod.DurableExecutionBackend(app2.store).resume(
                    started["id"]
                )
                self.assertEqual(len(resumed2["checkpoints"]), 1)
            finally:
                app2.close()
        finally:
            tmp.cleanup()

    def test_temporal_is_deferred_with_reason(self) -> None:
        from agentos import durable as durable_mod

        status = durable_mod.temporal_status()
        self.assertEqual(status["status"], "DEFERRED")
        self.assertTrue(status["detail"])
        self.assertFalse(status["service_running"])

    def test_durable_smoke(self) -> None:
        from agentos import durable as durable_mod

        app, tmp = _app()
        try:
            smoke = durable_mod.durable_smoke(app.store)
            self.assertTrue(smoke["ok"])
        finally:
            app.close()
            tmp.cleanup()


class BridgeDoctorTests(unittest.TestCase):
    def test_orgos_unconfigured_without_snapshot(self) -> None:
        app, tmp = _app()
        try:
            status = app.orgos_status()
            self.assertEqual(status["status"], "UNCONFIGURED")
        finally:
            app.close()
            tmp.cleanup()

    def test_doctor_categories_cover_fabric(self) -> None:
        app, tmp = _app()
        try:
            result = app.federate_doctor()
            names = {c["name"] for c in result["checks"]}
            self.assertTrue(
                {"cells", "mcp-registry", "temporal", "federation-store"} <= names
            )
            for check in result["checks"]:
                self.assertIn(
                    check["category"],
                    ("PASS", "WARN", "UNCONFIGURED", "UNAVAILABLE", "FAIL"),
                )
            self.assertNotIn("FAIL", {c["category"] for c in result["checks"]})
        finally:
            app.close()
            tmp.cleanup()

    def test_paios_snapshot_shape(self) -> None:
        app, tmp = _app()
        try:
            snapshot = app.paios_snapshot()
            for key in (
                "executors",
                "cells",
                "capabilities",
                "jobs",
                "health",
                "usage_scarcity",
                "routing_decisions",
                "a2a_peers",
                "mcp_approved_tools",
                "durable_backend",
            ):
                self.assertIn(key, snapshot)
        finally:
            app.close()
            tmp.cleanup()


class FabricCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_raw = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp_raw.name) / "home"
        self.home.mkdir(parents=True)

    def tearDown(self) -> None:
        self.tmp_raw.cleanup()

    def _run_json(self, argv: list[str]) -> tuple[int, dict]:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli_main(["--home", str(self.home), "--json", *argv])
        return code, json.loads(out.getvalue())

    def test_a2a_card(self) -> None:
        code, payload = self._run_json(["a2a", "card"])
        self.assertEqual(code, 0)
        skills = {s["id"] for s in payload["card"]["skills"]}
        self.assertNotIn("shell.run", skills)
        self.assertNotIn("fs.write", skills)

    def test_capability_find(self) -> None:
        code, payload = self._run_json(["capability", "find", "echo"])
        self.assertEqual(code, 0)
        self.assertGreater(payload["count"], 0)

    def test_federate_doctor(self) -> None:
        code, payload = self._run_json(["federate", "doctor"])
        self.assertEqual(code, 0)
        self.assertIn("checks", payload)

    def test_route_explain(self) -> None:
        job = json.dumps(
            {
                "requester": "tester",
                "targetCapability": "echo",
                "deliverable": "inspect this repo",
            }
        )
        code, payload = self._run_json(["federate", "route", "--job", job, "--explain"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["chosen"], "local-tools")
        self.assertIn("candidates", payload)

    def test_benchmark_run_and_report(self) -> None:
        code, payload = self._run_json(
            ["benchmark", "run", "--executor", "local-worker", "--workload", "A"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["record"]["status"], "COMPLETED")
        code, payload = self._run_json(["benchmark", "report"])
        self.assertEqual(code, 0)
        self.assertGreaterEqual(payload["runs"], 1)

    def test_usage_and_durable_status(self) -> None:
        code, payload = self._run_json(["usage", "status"])
        self.assertEqual(code, 0)
        self.assertIn("reset_mode", payload)
        code, payload = self._run_json(["durable", "status"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["backend"], "native")
        self.assertEqual(payload["temporal"]["status"], "DEFERRED")

    def test_mcp_candidates_cli(self) -> None:
        code, payload = self._run_json(["mcp", "candidates"])
        self.assertEqual(code, 0)
        self.assertIn("candidates", payload)

    def test_top_level_route(self) -> None:
        job = json.dumps(
            {
                "requester": "tester",
                "targetCapability": "echo",
                "deliverable": "x",
            }
        )
        code, payload = self._run_json(["route", "--job", job])
        self.assertEqual(code, 0)
        self.assertIn("chosen", payload)


if __name__ == "__main__":
    unittest.main()
