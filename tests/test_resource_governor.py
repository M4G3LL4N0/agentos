import tempfile
import unittest
from typing import Any

from datetime import datetime, timedelta, timezone

from agentos.federation import (
    DataClass,
    ExecutorCell,
    JobEnvelope,
    LatencyClass,
    QualityFloor,
    make_executor_cell,
)
from agentos.resource_governor import (
    DEFAULT_GOVERNOR_WEIGHTS,
    ResolvedUsage,
    UsageSnapshot,
    governor_route,
    governor_weights,
    parse_snapshot,
    resolve_usage,
    validate_percentage,
)
from agentos.services import AgentOS
from agentos.supervisor import operating_model, supervisor_status
from agentos.task_verification import (
    DETERMINISTIC_VERIFICATION_TYPES,
    TaskVerificationStatus,
    choose_verifier,
    transition_verification,
    verification_plan,
)
from agentos.usage_ledger import (
    UsageEntry,
    ledger_summary,
    save_entry,
)


def base_job(**overrides):
    data = {
        "requester": "tester",
        "targetCapability": "echo",
        "deliverable": "hello governor",
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
            supported_operations=["shell.run", "fs.read", "fs.write", "echo"],
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


def official_snapshot(
    used_pct: float = 40.0, freshness: str = "LIVE", account: str = "primary"
) -> dict[str, Any]:
    return {
        "provider": "grokbot-office",
        "accountCell": account,
        "usedPct": used_pct,
        "remainingPct": round(100.0 - used_pct, 2),
        "source": "OFFICIAL_UI",
        "resetsAt": "2030-01-01T00:00:00Z",
        "asOf": "2026-09-22T12:00:00Z",
        "freshness": freshness,
    }


FIXED_NOW = "2026-09-22T13:00:00Z"


class GovernorValidationTests(unittest.TestCase):
    def test_invalid_percentages_rejected(self) -> None:
        for bad in (-1, 101, 200, "x", None if False else True, "abc"):
            with self.subTest(value=bad):
                if isinstance(bad, bool):
                    continue
                with self.assertRaises(ValueError):
                    validate_percentage(bad, "usedPct")

    def test_boundary_percentages_accepted(self) -> None:
        self.assertEqual(validate_percentage(0, "usedPct"), 0.0)
        self.assertEqual(validate_percentage(100, "usedPct"), 100.0)
        self.assertEqual(validate_percentage(None, "usedPct"), None)

    def test_no_fabricated_reset(self) -> None:
        snap = parse_snapshot(
            {
                "provider": "grokbot-office",
                "usedPct": 40,
                "source": "OFFICIAL_UI",
                "asOf": "2026-09-22T12:00:00Z",
            },
            now=FIXED_NOW,
        )
        self.assertFalse(snap.reset_known)
        self.assertIsNone(snap.resetsAt)

    def test_unknown_never_zero_or_unlimited(self) -> None:
        resolved = resolve_usage([])
        self.assertIsInstance(resolved, ResolvedUsage)
        self.assertEqual(resolved.source, "UNKNOWN")
        self.assertFalse(resolved.is_live)
        self.assertIsNone(resolved.snapshot)
        self.assertIn("never assumed to be 0 or unlimited", " ".join(resolved.warnings))


class GovernorSourcePrecedenceTests(unittest.TestCase):
    def test_audited_unofficial_marked(self) -> None:
        snap = parse_snapshot(
            {
                "provider": "grokbot-office",
                "usedPct": 33,
                "source": "AUDITED_UNOFFICIAL",
                "asOf": "2026-09-22T12:00:00Z",
            },
            now=FIXED_NOW,
        )
        resolved = resolve_usage([snap.to_dict()], now=FIXED_NOW)
        self.assertTrue(resolved.unofficial)

    def test_precedence_fresh_tiers(self) -> None:
        fresh_official = parse_snapshot(
            official_snapshot(40), now=FIXED_NOW
        )
        fresh_audited = parse_snapshot(
            {
                "provider": "grokbot-office",
                "accountCell": "primary",
                "usedPct": 55,
                "source": "AUDITED_UNOFFICIAL",
                "asOf": "2026-09-22T12:00:00Z",
            },
            now=FIXED_NOW,
        )
        fresh_manual = parse_snapshot(
            {
                "provider": "grokbot-office",
                "accountCell": "primary",
                "usedPct": 60,
                "source": "MANUAL",
                "asOf": "2026-09-22T12:00:00Z",
            },
            now=FIXED_NOW,
        )
        resolved = resolve_usage(
            [fresh_manual.to_dict(), fresh_audited.to_dict(), fresh_official.to_dict()],
            now=FIXED_NOW,
        )
        self.assertEqual(resolved.source, "OFFICIAL_UI")
        self.assertEqual(resolved.snapshot.usedPct, 40.0)
        self.assertTrue(resolved.is_live)

    def test_freshness_dominates_source(self) -> None:
        stale_official = parse_snapshot(
            {
                **official_snapshot(40),
                "asOf": "2026-09-20T12:00:00Z",
            },
            now=FIXED_NOW,
        )
        audit = resolve_usage([stale_official.to_dict()], now=FIXED_NOW)
        self.assertEqual(audit.freshness, "STALE")
        self.assertTrue(audit.stale)
        self.assertEqual(audit.source, "STALE_CACHE")
        self.assertIn("stale", str(audit.stale_warning or "").lower())


class GovernorWeightsTests(unittest.TestCase):
    def test_defaults_used_without_overrides(self) -> None:
        info = governor_weights({})
        self.assertTrue(info["weights_explicit"] is False)
        for key in DEFAULT_GOVERNOR_WEIGHTS:
            self.assertIn(key, info["weights"])

    def test_explicit_overrides_merge(self) -> None:
        info = governor_weights({"governor": {"weights": {"quotaScarcity": 3.0}}})
        self.assertTrue(info["weights_explicit"])
        self.assertEqual(info["weights"]["quotaScarcity"], 3.0)
        self.assertEqual(
            info["weights"]["humanAttention"],
            DEFAULT_GOVERNOR_WEIGHTS["humanAttention"],
        )

    def test_negative_weight_rejected(self) -> None:
        with self.assertRaises(ValueError):
            governor_weights({"governor": {"weights": {"quotaScarcity": -1}}})


class GovernorRoutingTests(unittest.TestCase):
    def test_routing_consumes_scarcity_state(self) -> None:
        job = base_job()
        cells = cells_fixture()
        routed = governor_route(job, cells, config={})
        self.assertIn("candidates", routed)
        self.assertTrue(routed["candidate_count"] >= 1)
        for candidate in routed["candidates"]:
            self.assertIn("cost_factors", candidate)

    def test_backup_when_nothing_eligible(self) -> None:
        job = base_job(dataClass="HIGHLY_SENSITIVE")
        blocked = make_executor_cell(
            id="blocked",
            provider="shell",
            supported_operations=["echo"],
            health="DOWN",
            tier=1,
        )
        routed = governor_route(job, [blocked], config={})
        self.assertIsNone(routed["governed_choice"])
        self.assertEqual(routed["candidate_count"], 0)


class UsageLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.agent = AgentOS(home=str(self.home.name))

    def tearDown(self) -> None:
        self.home.cleanup()

    def test_ledger_save_list_round_trip(self) -> None:
        entry = UsageEntry(
            provider="cursor",
            category="api_budget",
            accountCell="pro",
            amount=250.0,
            unit="USD",
            note="march api budget",
        )
        save_entry(self.agent.store, entry)
        records = self.agent.store.list_usage_entries(provider="cursor")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["entry"]["amount"], 250.0)

    def test_ledger_summary_totals_only_recorded(self) -> None:
        save_entry(
            self.agent.store,
            UsageEntry(provider="cursor", category="api_budget", amount=10, unit="USD"),
        )
        save_entry(
            self.agent.store,
            UsageEntry(provider="cursor", category="api_budget", amount=5, unit="USD"),
        )
        summary = ledger_summary(self.agent.store)
        self.assertEqual(summary["entries"], 2)
        self.assertEqual(summary["amount_total"], 15.0)


class TaskVerificationTests(unittest.TestCase):
    def test_illegal_transition_refused(self) -> None:
        state, allowed, reason = transition_verification(
            TaskVerificationStatus.PENDING.value, "REQUIRE"
        )
        self.assertFalse(allowed)
        self.assertEqual(state, TaskVerificationStatus.PENDING)

    def test_legal_chain(self) -> None:
        state, allowed, _ = transition_verification("UNVERIFIED", "REQUIRE")
        self.assertTrue(allowed)
        state, allowed, _ = transition_verification(state.value, "START")
        self.assertTrue(allowed)
        state, allowed, _ = transition_verification(state.value, "PASS")
        self.assertTrue(allowed)
        self.assertEqual(state, TaskVerificationStatus.VERIFIED)

    def test_deterministic_avoids_second_model_call(self) -> None:
        job = base_job(verificationType="file_diff")
        worker = cells_fixture()[0]
        verifier, separated, reason = choose_verifier(job, worker, cells_fixture())
        self.assertFalse(separated)
        self.assertIn("deterministic", reason)

    def test_verifier_separation_when_available(self) -> None:
        job = base_job(verificationType="tests")
        cells = cells_fixture()
        for cell in cells:
            cell.verificationPolicy = "run-tests-verify"
        verifier, separated, reason = choose_verifier(job, cells[0], cells)
        self.assertTrue(separated)
        self.assertEqual(verifier, cells[1].id)

    def test_plan_not_required(self) -> None:
        job = base_job()
        plan = verification_plan(job)
        self.assertFalse(plan["verificationRequired"])
        self.assertEqual(plan["status"], TaskVerificationStatus.NOT_REQUIRED.value)

    def test_plan_requires_and_applies_default(self) -> None:
        job = base_job(verificationRequired=True)
        cells = cells_fixture()
        plan = verification_plan(job, cells[0], cells)
        self.assertTrue(plan["verificationRequired"])
        self.assertGreaterEqual(plan["verificationType"], "source_verification")


class SupervisorModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.agent = AgentOS(home=str(self.home.name))

    def tearDown(self) -> None:
        self.home.cleanup()

    def test_operating_model_honest(self) -> None:
        model = operating_model()
        self.assertEqual(model["status"], "IMPLEMENTED")
        self.assertIn("GrokBot", model["grokbot_scope"])
        self.assertIn("DOES NOT", model["grokbot_scope"].upper())

    def test_supervisor_status_honest(self) -> None:
        status = supervisor_status(self.agent)
        self.assertFalse(status["verified_live"])
        self.assertIsInstance(status["cells"], list)
        self.assertIn("ChiefOfStaff", status["chain_layer_status"])


class GovernorStoreIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.home = tempfile.TemporaryDirectory()
        self.agent = AgentOS(home=str(self.home.name))
        # Pin the usage reference clock so fixture asOf 2026-09-22T12:00Z is
        # live (ages computed against FIXED_NOW, not the real wall clock).
        self.agent.config["usage"] = {"now": FIXED_NOW}

    def tearDown(self) -> None:
        self.home.cleanup()

    def test_service_usage_import_and_resolve(self) -> None:
        imported = self.agent.usage_import(official_snapshot(40))
        self.assertEqual(imported["imported"]["source"], "OFFICIAL_UI")
        resolved = self.agent.usage_resolve(provider="grokbot-office")
        self.assertEqual(
            resolved["usage_resolution"]["snapshot"]["usedPct"], 40.0
        )
        self.assertTrue(resolved["usage_resolution"]["is_live"])

    def test_service_usage_ledger_record(self) -> None:
        result = self.agent.usage_add(
            provider="cursor", category="api_budget", amount=99.0, unit="USD"
        )
        self.assertEqual(result["entry"]["amount"], 99.0)
        listings = self.agent.usage_ledger(provider="cursor")
        self.assertEqual(len(listings["entries"]), 1)

    def test_service_governor_outcome_lifecycle(self) -> None:
        routed = self.agent.governor_route(
            {
                "requester": "tester",
                "targetCapability": "echo",
                "deliverable": "verify me",
                "dataClass": "INTERNAL",
            }
        )
        self.assertEqual(routed["outcome"]["outcome"], "PENDING")
        job_id = routed["outcome"]["job_id"]
        verified = self.agent.governor_verify(
            job_id, "VERIFIED", evidence=["sandbox-run + reporter ping-ok"]
        )
        self.assertEqual(verified["outcome"]["outcome"], "VERIFIED")
        summary = self.agent.governor_outcome_summary()
        self.assertGreaterEqual(
            sum(e["runs"] for e in summary["executors"].values()), 1
        )

    def test_governor_rejects_bad_verify_status(self) -> None:
        routed = self.agent.governor_route(
            {
                "requester": "tester",
                "targetCapability": "echo",
                "deliverable": "verify me",
                "dataClass": "INTERNAL",
            }
        )
        job_id = routed["outcome"]["job_id"]
        with self.assertRaises(Exception):
            self.agent.governor_verify(job_id, "questionable")
        latest = self.agent.store.latest_routing_outcome(job_id)
        self.assertEqual(latest["outcome"], "PENDING")


if __name__ == "__main__":
    unittest.main()