"""Tests for ServerOS four-question worker admission (Phase 4B)."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from agentos.resource_contract import MAX_ADDITIONAL_HEAVY_CODING, UNKNOWN, compose_snapshot
from agentos.worker_admission import (
    RETENTION_POLICIES,
    admit_worker,
    complete_scratch,
    list_leases,
    register_scratch,
)
from tests.test_resource_contract import NOW, full_sources


def healthy_snapshot(**overrides) -> dict:
    band = overrides.pop("band", "HEALTHY")
    return compose_snapshot(full_sources(**overrides), now=NOW, band=band)


class TestFourQuestionAdmission(unittest.TestCase):
    def test_healthy_heavyworker_admitted(self) -> None:
        decision = admit_worker(
            healthy_snapshot(),
            profile="heavyweight_coding",
            bytes_needed=1024,
            now=NOW + 5,
        )
        self.assertTrue(decision["can_start"])
        self.assertEqual(decision["constraining_resource"], "none")
        self.assertEqual(decision["retry_after_s"], 0)
        budget = decision["budget"]
        self.assertEqual(
            budget["additional_heavy_coding"], MAX_ADDITIONAL_HEAVY_CODING
        )
        self.assertEqual(budget["bytes_cap"], 1024)
        self.assertEqual(budget["band"], "HEALTHY")
        self.assertIn("admitted:", decision["reason"])

    def test_light_and_verification_profiles_admitted_when_healthy(self) -> None:
        for profile in ("light", "verification"):
            with self.subTest(profile=profile):
                decision = admit_worker(
                    healthy_snapshot(), profile=profile, now=NOW + 5
                )
                self.assertTrue(decision["can_start"])

    def test_missing_snapshot_fails_closed(self) -> None:
        decision = admit_worker(None, now=NOW)
        self.assertFalse(decision["can_start"])
        self.assertEqual(decision["constraining_resource"], "unknown_source")
        self.assertEqual(decision["retry_after_s"], 15)

    def test_stale_snapshot_fails_closed_with_age(self) -> None:
        snap = healthy_snapshot()
        decision = admit_worker(snap, now=NOW + 500)
        self.assertFalse(decision["can_start"])
        self.assertEqual(decision["constraining_resource"], "stale_snapshot")
        self.assertEqual(decision["retry_after_s"], 15)
        self.assertGreaterEqual(decision["state_age_s"], 499)

    def test_snapshot_without_timestamp_fails_closed(self) -> None:
        snap = healthy_snapshot()
        snap["_meta"].pop("generated_epoch")
        decision = admit_worker(snap, now=NOW + 1)
        self.assertFalse(decision["can_start"])
        self.assertEqual(decision["constraining_resource"], "stale_snapshot")

    def test_unknown_profile_raises(self) -> None:
        with self.assertRaises(ValueError):
            admit_worker(healthy_snapshot(), profile="nonsense", now=NOW + 1)


class TestProviderCooldownIndependence(unittest.TestCase):
    def test_cooldown_refuses_on_healthy_host(self) -> None:
        decision = admit_worker(
            healthy_snapshot(),
            provider_cooldown_until=NOW + 42,
            now=NOW + 5,
        )
        self.assertFalse(decision["can_start"])
        self.assertEqual(decision["constraining_resource"], "provider_cooldown")
        self.assertEqual(decision["retry_after_s"], 38)  # int(NOW+42 - (NOW+5)) + 1

    def test_cooldown_reported_before_critical_band(self) -> None:
        # independent dimension: cooldown wins as the reported constraint,
        # proving it is evaluated regardless of host state
        decision = admit_worker(
            healthy_snapshot(band="CRITICAL"),
            provider_cooldown_until=NOW + 10,
            now=NOW + 5,
        )
        self.assertFalse(decision["can_start"])
        self.assertEqual(decision["constraining_resource"], "provider_cooldown")

    def test_critical_band_without_cooldown_reports_band(self) -> None:
        decision = admit_worker(healthy_snapshot(band="CRITICAL"), now=NOW + 5)
        self.assertEqual(decision["constraining_resource"], "band_critical")
        self.assertEqual(decision["retry_after_s"], 300)

    def test_expired_cooldown_is_not_a_factor(self) -> None:
        decision = admit_worker(
            healthy_snapshot(),
            provider_cooldown_until=NOW - 1,
            now=NOW + 5,
        )
        self.assertTrue(decision["can_start"])


class TestConstraintOrder(unittest.TestCase):
    def test_autonomousos_hold_blocks(self) -> None:
        sources = full_sources()
        sources["aos"] = dict(sources["aos"], allow_new_work=False, mode="SEVERE")
        snap = compose_snapshot(sources, now=NOW, band="HEALTHY")
        decision = admit_worker(snap, now=NOW + 5)
        self.assertFalse(decision["can_start"])
        self.assertEqual(decision["constraining_resource"], "autonomousos_hold")
        self.assertEqual(decision["retry_after_s"], 60)

    def test_storage_denied_blocks(self) -> None:
        sources = full_sources()
        sources["janitor"] = dict(
            sources["janitor"], admission_default={"admit": False, "reason": "floor"}
        )
        snap = compose_snapshot(sources, now=NOW, band="HEALTHY")
        decision = admit_worker(snap, now=NOW + 5)
        self.assertEqual(decision["constraining_resource"], "storage_denied")
        self.assertEqual(decision["retry_after_s"], 60)

    def test_storage_unknown_fails_closed(self) -> None:
        snap = compose_snapshot(full_sources(janitor=None), now=NOW, band="HEALTHY")
        decision = admit_worker(snap, now=NOW + 5)
        self.assertFalse(decision["can_start"])
        self.assertEqual(decision["constraining_resource"], "storage_unknown")

    def test_stacking_blocks(self) -> None:
        sources = full_sources()
        sources["proc"] = dict(sources["proc"], janitor_processes=3)
        snap = compose_snapshot(sources, now=NOW, band="HEALTHY")
        decision = admit_worker(snap, now=NOW + 5)
        self.assertEqual(decision["constraining_resource"], "process_stacking")
        self.assertEqual(decision["retry_after_s"], 120)

    def test_stuck_scan_blocks(self) -> None:
        sources = full_sources()
        sources["proc"] = dict(sources["proc"], stuck_finds=1)
        snap = compose_snapshot(sources, now=NOW, band="HEALTHY")
        decision = admit_worker(snap, now=NOW + 5)
        self.assertEqual(decision["constraining_resource"], "stuck_filesystem_scan")

    def test_verification_overload_blocks_only_verification_profile(self) -> None:
        sources = full_sources()
        sources["scheduler"] = dict(
            sources["scheduler"],
            active_leases=3,
            active_leases_by_class={"test": 3},
        )
        snap = compose_snapshot(sources, now=NOW, band="HEALTHY")
        blocked = admit_worker(snap, profile="verification", now=NOW + 5)
        self.assertFalse(blocked["can_start"])
        self.assertEqual(blocked["constraining_resource"], "verification_overload")
        heavy = admit_worker(snap, profile="heavyweight_coding", now=NOW + 5)
        self.assertTrue(heavy["can_start"])

    def test_unknown_band_fails_closed(self) -> None:
        snap = healthy_snapshot(band=UNKNOWN)
        decision = admit_worker(snap, now=NOW + 5)
        self.assertFalse(decision["can_start"])
        self.assertEqual(decision["constraining_resource"], "unknown_source")


class TestPressureBehaviour(unittest.TestCase):
    def test_pressed_defers_low_priority(self) -> None:
        decision = admit_worker(
            healthy_snapshot(band="PRESSED"), level="LOW", now=NOW + 5
        )
        self.assertFalse(decision["can_start"])
        self.assertEqual(decision["constraining_resource"], "low_priority_deferred")
        self.assertEqual(decision["retry_after_s"], 60)
        self.assertIs(decision["defer_low_priority"], True)

    def test_pressed_defers_heavyweight(self) -> None:
        decision = admit_worker(
            healthy_snapshot(band="PRESSED"),
            profile="heavyweight_coding",
            now=NOW + 5,
        )
        self.assertFalse(decision["can_start"])
        self.assertEqual(decision["constraining_resource"], "heavy_under_pressure")
        self.assertEqual(decision["retry_after_s"], 120)

    def test_pressed_admits_medium_light_with_clamped_bytes(self) -> None:
        decision = admit_worker(
            healthy_snapshot(band="PRESSED"),
            profile="light",
            bytes_needed=400 * 1024 * 1024,
            now=NOW + 5,
        )
        self.assertTrue(decision["can_start"])
        self.assertTrue(decision["defer_low_priority"])
        self.assertEqual(decision["budget"]["bytes_cap"], 100 * 1024 * 1024)

    def test_recovering_behaves_like_pressed_for_heavy(self) -> None:
        decision = admit_worker(
            healthy_snapshot(band="RECOVERING"), now=NOW + 5
        )
        self.assertEqual(decision["constraining_resource"], "heavy_under_pressure")

    def test_capacity_exhaustion_by_cpu_budget(self) -> None:
        sources = full_sources()
        sources["aos"] = dict(sources["aos"])
        sources["aos"]["cpu_budget"] = {"cpu_heavy_cap": 2, "cpu_heavy_remaining": 0}
        snap = compose_snapshot(sources, now=NOW, band="HEALTHY")
        decision = admit_worker(snap, now=NOW + 5)
        self.assertFalse(decision["can_start"])
        self.assertEqual(decision["constraining_resource"], "worker_capacity_exhausted")
        self.assertEqual(decision["retry_after_s"], 60)

    def test_low_priority_allowed_on_healthy_host(self) -> None:
        decision = admit_worker(
            healthy_snapshot(), profile="light", level="LOW", now=NOW + 5
        )
        self.assertTrue(decision["can_start"])
        self.assertIs(decision["defer_low_priority"], False)


class TestAuditAndScratchLeases(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.snapshot_path = self.dir / "resource-snapshot.json"
        self.registry = self.dir / "scratch-leases.json"
        # recent, published contract at the env-overridden path
        snap = compose_snapshot(full_sources(), now=time.time(), band="HEALTHY")
        self.snapshot_path.write_text(json.dumps(snap), encoding="utf-8")
        self.stub_janitor = self.dir / "janitor"
        self.stub_janitor.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.stub_janitor.chmod(0o755)
        for key, value in {
            "AGENTOS_RES_SNAPSHOT": str(self.snapshot_path),
            "AGENTOS_RES_JANITOR": str(self.stub_janitor),
        }.items():
            old = os.environ.get(key)
            os.environ[key] = value
            self.addCleanup(self._restore, key, old)

    @staticmethod
    def _restore(key: str, old: str | None) -> None:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old

    def test_services_admission_records_audit_event(self) -> None:
        from agentos.models import EventType
        from agentos.services import AgentOS

        app = AgentOS(home=self.dir / "home")
        self.addCleanup(app.close)
        decision = app.worker_admission(
            profile="heavyweight_coding", bytes_needed=2048, level="HIGH"
        )
        self.assertTrue(decision["can_start"])
        events = app.events(limit=50)
        audit = [e for e in events if e["event_type"] == str(EventType.RESOURCE_ADMISSION_DECIDED)]
        self.assertEqual(len(audit), 1)
        payload = audit[0]["payload"]
        self.assertIs(payload["can_start"], True)
        self.assertEqual(payload["constraining_resource"], "none")
        self.assertEqual(payload["resource_band"], "HEALTHY")

    def test_services_admission_audits_refusal_too(self) -> None:
        from agentos.models import EventType
        from agentos.services import AgentOS

        self.snapshot_path.unlink()  # no published contract -> fail closed
        app = AgentOS(home=self.dir / "home")
        self.addCleanup(app.close)
        decision = app.worker_admission(profile="heavyweight_coding")
        events = app.events(limit=50)
        audit = [e for e in events if e["event_type"] == str(EventType.RESOURCE_ADMISSION_DECIDED)]
        self.assertEqual(len(audit), 1)  # snapshot missing on fresh home -> refusal
        self.assertIs(decision["can_start"], False)

    def test_services_resource_snapshot_read_mode(self) -> None:
        from agentos.services import AgentOS

        app = AgentOS(home=self.dir / "home")
        self.addCleanup(app.close)
        result = app.resource_snapshot()
        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "read")
        self.assertEqual(result["snapshot"]["resource_band"], "HEALTHY")
        live = app.resource_snapshot(live=True)
        self.assertEqual(live["mode"], "live")

    def test_read_of_absent_snapshot_is_honest_error(self) -> None:
        from agentos.services import AgentOS

        self.snapshot_path.unlink()
        app = AgentOS(home=self.dir / "home")
        self.addCleanup(app.close)
        result = app.resource_snapshot()
        self.assertFalse(result["ok"])
        self.assertIsNone(result["snapshot"])
        self.assertIn("governor snapshot --publish", result["error"])

    def test_register_and_complete_disposable_lease(self) -> None:
        record = register_scratch(
            task_id="t1",
            venture_id="v1",
            workspace="/srv/noaerth/w/t1",
            scratch_root="/srv/noaerth/scratch/t1",
            storage_budget=1024,
            owner="test",
            retention_policy="disposable",
            registry=self.registry,
        )
        self.assertEqual(record["lease_state"], "ACTIVE")
        for field in (
            "task_id",
            "venture_id",
            "workspace",
            "scratch_root",
            "storage_budget",
            "creation_time",
            "owner",
            "lease_state",
            "retention_policy",
        ):
            self.assertIn(field, record)
        result = complete_scratch(task_id="t1", registry=self.registry)
        self.assertTrue(result["ok"])
        lease = result["lease"]
        self.assertEqual(lease["lease_state"], "RETIRED")
        self.assertTrue(lease["cleanup"]["requested"])

    def test_rollback_critical_lease_is_retained(self) -> None:
        register_scratch(
            task_id="t2",
            venture_id="v1",
            workspace="/srv/noaerth/w/t2",
            scratch_root="/srv/noaerth/scratch/t2",
            storage_budget=1024,
            owner="test",
            retention_policy="rollback_critical",
            registry=self.registry,
        )
        result = complete_scratch(task_id="t2", registry=self.registry)
        lease = result["lease"]
        self.assertEqual(lease["lease_state"], "COMPLETED_PENDING_REAP")
        self.assertFalse(lease["cleanup"]["requested"])
        self.assertIn("retained", lease["cleanup"]["detail"])
        self.assertEqual(list_leases(self.registry)[0]["retention_policy"],
                         "rollback_critical")

    def test_complete_unknown_task_is_error(self) -> None:
        result = complete_scratch(task_id="ghost", registry=self.registry)
        self.assertFalse(result["ok"])
        self.assertIn("ghost", result["error"])

    def test_invalid_retention_policy_raises(self) -> None:
        with self.assertRaises(ValueError):
            register_scratch(
                task_id="t3",
                venture_id="v",
                workspace="w",
                scratch_root="s",
                storage_budget=1,
                owner="o",
                retention_policy="yolo",
                registry=self.registry,
            )
        self.assertIn("disposable", RETENTION_POLICIES)


if __name__ == "__main__":
    unittest.main()
