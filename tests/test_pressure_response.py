"""Phase 4B pressure scenarios: host saturation must yield predictable
admission decisions and a zero/limited worker recommendation.

Each scenario builds a contract snapshot from explicit sources (deterministic,
no live sampling) and asserts BOTH the recommended capacity and the four
answers from admit_worker. No worker process is ever started or killed by
these code paths — admission is pure — which the scenarios verify by
constructing every decision from inputs alone.
"""

import unittest

from agentos.resource_contract import UNKNOWN, compose_snapshot
from agentos.worker_admission import admit_worker
from tests.test_resource_contract import NOW, full_sources


def snap(band, **overrides):
    return compose_snapshot(full_sources(**overrides), now=NOW, band=band)


class TestPressureScenarios(unittest.TestCase):
    def test_01_cpu_saturation_slows_then_stops_admission(self) -> None:
        # cpu at 100% with the governor holding PRESSED (hysteresis-held):
        # capacity slows to 1, heavy deferred, medium light still possible.
        sources = full_sources()
        sources["aos"] = dict(sources["aos"])
        sources["aos"]["host"]["cpu"]["usage"] = 100.0
        sources["aos"]["mode"] = "PRESSURE"
        snap_obj = compose_snapshot(sources, now=NOW, band="PRESSED")
        self.assertEqual(snap_obj["cpu_usage"], 100.0)
        self.assertEqual(
            snap_obj["recommended_worker_capacity"]["additional_heavy_coding"], 1
        )
        heavy = admit_worker(snap_obj, now=NOW + 5)
        self.assertEqual(heavy["constraining_resource"], "heavy_under_pressure")
        self.assertEqual(heavy["retry_after_s"], 120)
        light = admit_worker(snap_obj, profile="light", now=NOW + 5)
        self.assertTrue(light["can_start"])
        self.assertTrue(light["defer_low_priority"])

    def test_02_memory_pressure_critical_band_refuses(self) -> None:
        sources = full_sources()
        sources["aos"] = dict(sources["aos"])
        sources["aos"]["pressure"]["memory"]["some_avg10"] = 85.0
        sources["aos"]["mode"] = "CRITICAL"
        sources["aos"]["allow_new_work"] = False
        snap_obj = compose_snapshot(sources, now=NOW, band="CRITICAL")
        self.assertEqual(snap_obj["memory_pressure"], 85.0)
        self.assertEqual(
            snap_obj["recommended_worker_capacity"]["additional_heavy_coding"], 0
        )
        decision = admit_worker(snap_obj, profile="light", now=NOW + 5)
        self.assertFalse(decision["can_start"])
        self.assertEqual(decision["constraining_resource"], "band_critical")
        self.assertEqual(decision["retry_after_s"], 300)

    def test_03_disk_denied_by_janitoros(self) -> None:
        sources = full_sources()
        sources["janitor"] = dict(
            sources["janitor"],
            disk={"avail_bytes": 1024, "band": "ok"},
            admission_default={"admit": False, "reason": "free space floor"},
        )
        snap_obj = compose_snapshot(sources, now=NOW, band="HEALTHY")
        self.assertEqual(snap_obj["storage_admission"], "DENY")
        self.assertEqual(
            snap_obj["recommended_worker_capacity"]["additional_heavy_coding"], 0
        )
        decision = admit_worker(snap_obj, now=NOW + 5)
        self.assertEqual(decision["constraining_resource"], "storage_denied")
        self.assertEqual(decision["retry_after_s"], 60)

    def test_04_worker_stacking_detected(self) -> None:
        # janitor/reactor duplication (the Phase-4A-class incident): more
        # janitors than the stacking limit zeroes capacity and refuses.
        sources = full_sources()
        sources["proc"] = dict(sources["proc"], janitor_processes=4, active_processes=320)
        snap_obj = compose_snapshot(sources, now=NOW, band="HEALTHY")
        infra = snap_obj["_meta"]["infrastructure"]
        self.assertIs(infra["stacking_detected"], True)
        self.assertEqual(infra["janitor_processes"], 4)
        self.assertEqual(
            snap_obj["recommended_worker_capacity"]["additional_heavy_coding"], 0
        )
        decision = admit_worker(snap_obj, now=NOW + 5)
        self.assertEqual(decision["constraining_resource"], "process_stacking")
        self.assertEqual(decision["retry_after_s"], 120)

    def test_05_failed_cron_environment_fails_closed(self) -> None:
        # janitor service unreadable (broken environment) -> UNKNOWN storage,
        # never an optimistic ALLOW.
        snap_obj = compose_snapshot(full_sources(janitor=None), now=NOW, band="HEALTHY")
        self.assertEqual(snap_obj["storage_admission"], UNKNOWN)
        self.assertEqual(
            snap_obj["recommended_worker_capacity"]["additional_heavy_coding"], 0
        )
        decision = admit_worker(snap_obj, now=NOW + 5)
        self.assertFalse(decision["can_start"])
        self.assertEqual(decision["constraining_resource"], "storage_unknown")
        self.assertEqual(decision["retry_after_s"], 60)

    def test_06_stuck_filesystem_scan_blocks_new_workers(self) -> None:
        sources = full_sources()
        sources["proc"] = dict(sources["proc"], stuck_finds=3, active_processes=400)
        snap_obj = compose_snapshot(sources, now=NOW, band="HEALTHY")
        self.assertEqual(snap_obj["_meta"]["infrastructure"]["stuck_finds"], 3)
        self.assertEqual(
            snap_obj["recommended_worker_capacity"]["additional_heavy_coding"], 0
        )
        decision = admit_worker(snap_obj, now=NOW + 5)
        self.assertEqual(decision["constraining_resource"], "stuck_filesystem_scan")
        self.assertEqual(decision["retry_after_s"], 120)

    def test_07_provider_rate_limit_is_independent_of_cpu(self) -> None:
        # healthy host, cooling provider -> refused (provider first).
        sources = full_sources()
        sources["studio"] = {"studio_active": 1, "provider_waits": {"anthropic": int(NOW) + 90}}
        healthy = compose_snapshot(sources, now=NOW, band="HEALTHY")
        self.assertEqual(healthy["_meta"]["provider_cooldown"]["state"],
                         {"anthropic": int(NOW) + 90})
        decision = admit_worker(healthy, now=NOW + 5,
                                provider_cooldown_until=NOW + 95)
        self.assertEqual(decision["constraining_resource"], "provider_cooldown")

        # saturated host, no cooldown -> the constraint reported is the band,
        # proving the two dimensions do not leak into each other.
        sick = compose_snapshot(full_sources(), now=NOW, band="CRITICAL")
        decision = admit_worker(sick, now=NOW + 5)
        self.assertEqual(decision["constraining_resource"], "band_critical")

    def test_08_verification_overload_defers_test_work_only(self) -> None:
        sources = full_sources()
        sources["scheduler"] = dict(
            sources["scheduler"],
            active_leases=3,
            active_leases_by_class={"test": 3},
        )
        snap_obj = compose_snapshot(sources, now=NOW, band="HEALTHY")
        verification = snap_obj["_meta"]["verification"]
        self.assertIs(verification["overloaded"], True)
        # capacity for new coding workers is untouched by a full test class
        self.assertEqual(
            snap_obj["recommended_worker_capacity"]["additional_heavy_coding"], 2
        )
        test_decision = admit_worker(snap_obj, profile="verification", now=NOW + 5)
        self.assertEqual(
            test_decision["constraining_resource"], "verification_overload"
        )
        self.assertEqual(test_decision["retry_after_s"], 120)
        coding_decision = admit_worker(snap_obj, profile="heavyweight_coding", now=NOW + 5)
        self.assertTrue(coding_decision["can_start"])

    def test_scenario_matrix_produces_bounded_retries_only(self) -> None:
        # every refusal hint stays inside the bounded hint table
        from agentos.worker_admission import _RETRY

        scenarios = [
            (None, {}),
            (snap(UNKNOWN), {}),
            (snap("CRITICAL"), {}),
            (snap("HEALTHY", janitor=None), {}),
            (snap("HEALTHY"), {"provider_cooldown_until": NOW + 20}),
        ]
        for snapshot, kwargs in scenarios:
            decision = admit_worker(snapshot, now=NOW + 5, **kwargs)
            self.assertFalse(decision["can_start"])
            retry = decision["retry_after_s"]
            self.assertIsInstance(retry, int)
            self.assertGreater(retry, 0)
            self.assertLessEqual(retry, 300)
            self.assertIn(decision["constraining_resource"],
                          list(_RETRY) + ["provider_cooldown"])
            self.assertTrue(decision["reason"])


if __name__ == "__main__":
    unittest.main()
