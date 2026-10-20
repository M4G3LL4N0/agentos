"""Tests for the NOAERTH resource contract snapshot (Phase 4B)."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from agentos.resource_contract import (
    CONTRACT_FIELDS,
    MAX_ADDITIONAL_HEAVY_CODING,
    UNKNOWN,
    ResourcePaths,
    build_snapshot,
    collect_sources,
    compose_snapshot,
    read_snapshot,
    recommend_capacity,
    write_snapshot,
)

NOW = 1_760_000_000.0
REPO_ROOT = Path(__file__).resolve().parent.parent


def full_sources(**overrides):
    sources = {
        "aos": {
            "mode": "HEALTHY",
            "allow_new_work": True,
            "host": {
                "cpu": {"usage": 42.5},
                "load1": 1.0,
                "load5": 1.1,
                "load15": 1.2,
                "memory": {"available_gb": 8.0},
                "disk": {"free": 50 * 1024**3},
            },
            "pressure": {
                "memory": {"some_avg10": 0.5},
                "io": {"some_avg10": 0.2},
            },
            "cpu_budget": {"cpu_heavy_cap": 2, "cpu_heavy_remaining": 2},
        },
        "scheduler": {
            "queued_demand": 5,
            "scheduler_active_workers": 4,
            "active_leases": 1,
            "active_leases_by_class": {"test": 0},
        },
        "studio": {"studio_active": 0, "provider_waits": {}},
        "janitor": {
            "health": "ok",
            "disk": {"avail_bytes": 20 * 1024**3, "band": "ok"},
            "forecast": {
                "trend": "shrinking",
                "avail_change_bytes_per_day": -(1024**3),
            },
            "admission_default": {"admit": True, "band": "ok", "reason": "healthy"},
        },
        "proc": {
            "active_processes": 210,
            "stuck_finds": 0,
            "janitor_processes": 1,
            "disk_watch_processes": 1,
            "boot_start_ticks": None,
            "clock_ticks": 100,
        },
    }
    sources.update(overrides)
    return sources


class TestSnapshotContract(unittest.TestCase):
    def test_contract_fields_present_in_order(self) -> None:
        snap = compose_snapshot(full_sources(), now=NOW, band="HEALTHY")
        public = [k for k in snap if not k.startswith("_") and k != "schema"]
        self.assertEqual(public, list(CONTRACT_FIELDS))

    def test_source_precedence_and_values(self) -> None:
        snap = compose_snapshot(full_sources(), now=NOW, band="HEALTHY")
        self.assertEqual(snap["cpu_usage"], 42.5)
        self.assertEqual(snap["load_averages"], [1.0, 1.1, 1.2])
        self.assertEqual(snap["available_memory"], int(8.0 * 1024**3))
        self.assertEqual(snap["memory_pressure"], 0.5)
        self.assertEqual(snap["disk_io_pressure"], 0.2)
        self.assertEqual(snap["disk_free"], 20 * 1024**3)  # janitor wins
        self.assertEqual(snap["disk_growth"], -(1024**3))
        self.assertEqual(snap["storage_admission"], "ALLOW")
        self.assertEqual(snap["active_workers"], 4)
        self.assertEqual(snap["queued_demand"], 5)
        self.assertEqual(snap["active_processes"], 210)
        self.assertEqual(snap["resource_band"], "HEALTHY")
        self.assertEqual(snap["updated_utc"].endswith("Z"), True)
        sources_map = snap["_meta"]["field_sources"]
        self.assertEqual(sources_map["disk_free"], "janitoros.space_status")
        self.assertEqual(sources_map["cpu_usage"], "autonomousos.governor")
        self.assertEqual(sources_map["active_workers"], "autonomousos.demands")
        # swap reads /proc/meminfo: UNKNOWN only where the kernel file is absent
        expected_unknown = [] if Path("/proc/meminfo").exists() else ["swap_pressure"]
        self.assertEqual(snap["_meta"]["unknown_fields"], expected_unknown)

    def test_janitor_missing_fails_capacity_closed(self) -> None:
        snap = compose_snapshot(full_sources(janitor=None), now=NOW, band="HEALTHY")
        self.assertEqual(snap["disk_free"], 50 * 1024**3)  # governor fallback
        self.assertEqual(snap["disk_growth"], UNKNOWN)
        self.assertEqual(snap["storage_admission"], UNKNOWN)
        capacity = snap["recommended_worker_capacity"]
        self.assertEqual(capacity["additional_heavy_coding"], 0)
        self.assertIn("storage_unknown", capacity["constraints"])
        self.assertIn("disk_growth", snap["_meta"]["unknown_fields"])
        self.assertIn("storage_admission", snap["_meta"]["unknown_fields"])

    def test_scheduler_missing_yields_unknown_worker_counts(self) -> None:
        snap = compose_snapshot(full_sources(scheduler=None), now=NOW, band="HEALTHY")
        self.assertEqual(snap["active_workers"], UNKNOWN)
        self.assertEqual(snap["queued_demand"], UNKNOWN)
        self.assertIn("active_workers", snap["_meta"]["unknown_fields"])
        self.assertIn("queued_demand", snap["_meta"]["unknown_fields"])
        self.assertEqual(snap["_meta"]["scheduler"]["active_leases"], UNKNOWN)

    def test_empty_sources_still_build_honestly(self) -> None:
        snap = compose_snapshot(
            {"aos": None, "scheduler": None, "studio": None, "janitor": None, "proc": {}},
            now=NOW,
            band=UNKNOWN,
        )
        self.assertEqual(snap["resource_band"], UNKNOWN)
        self.assertEqual(snap["storage_admission"], UNKNOWN)
        self.assertEqual(snap["disk_growth"], UNKNOWN)
        self.assertEqual(snap["active_processes"], UNKNOWN)
        for field in (
            "resource_band",
            "storage_admission",
            "disk_growth",
            "active_processes",
        ):
            self.assertIn(field, snap["_meta"]["unknown_fields"])
        # kernel-dependent fields: either honestly UNKNOWN (no /proc, listed)
        # or read from a real kernel source with provenance recorded
        kernel_fields = (
            "cpu_usage",
            "load_averages",
            "available_memory",
            "memory_pressure",
            "swap_pressure",
            "disk_io_pressure",
            "disk_free",
        )
        for field in kernel_fields:
            with self.subTest(field=field):
                value = snap[field]
                source = snap["_meta"]["field_sources"][field]
                if value == UNKNOWN:
                    self.assertEqual(source, UNKNOWN)
                    self.assertIn(field, snap["_meta"]["unknown_fields"])
                else:
                    self.assertNotEqual(source, UNKNOWN)
        # capacity fails closed on UNKNOWN band
        self.assertEqual(snap["recommended_worker_capacity"]["additional_heavy_coding"], 0)

    def test_aos_allow_false_zeroes_capacity(self) -> None:
        aos = full_sources()["aos"]
        aos = dict(aos, allow_new_work=False, mode="CRITICAL")
        snap = compose_snapshot(full_sources(aos=aos), now=NOW, band="HEALTHY")
        capacity = snap["recommended_worker_capacity"]
        self.assertEqual(capacity["additional_heavy_coding"], 0)
        self.assertIn("autonomousos_allow_new_work_false", capacity["constraints"])

    def test_provider_cooldown_meta(self) -> None:
        active = compose_snapshot(
            full_sources(studio={"studio_active": 1, "provider_waits": {"x": int(NOW) + 30}}),
            now=NOW,
            band="HEALTHY",
        )
        meta = active["_meta"]["provider_cooldown"]
        self.assertEqual(meta["state"], {"x": int(NOW) + 30})
        self.assertEqual(meta["active"], {"x": int(NOW) + 30})

        expired = compose_snapshot(
            full_sources(studio={"studio_active": 1, "provider_waits": {"x": int(NOW) - 5}}),
            now=NOW,
            band="HEALTHY",
        )
        self.assertEqual(expired["_meta"]["provider_cooldown"]["state"], "NONE")

        absent = compose_snapshot(full_sources(studio=None), now=NOW, band="HEALTHY")
        self.assertEqual(absent["_meta"]["provider_cooldown"]["state"], UNKNOWN)

    def test_active_lease_and_verification_meta(self) -> None:
        snap = compose_snapshot(
            full_sources(
                scheduler={
                    "queued_demand": 0,
                    "scheduler_active_workers": 3,
                    "active_leases": 3,
                    "active_leases_by_class": {"test": 3},
                }
            ),
            now=NOW,
            band="HEALTHY",
        )
        verification = snap["_meta"]["verification"]
        self.assertEqual(verification["active_test_leases"], 3)
        self.assertEqual(verification["test_cap"], 3)
        self.assertIs(verification["overloaded"], True)


class TestCapacityRecommendation(unittest.TestCase):
    def test_matrix(self) -> None:
        cases = [
            # (band, aos_allow, storage, expected)
            ("HEALTHY", True, "ALLOW", MAX_ADDITIONAL_HEAVY_CODING),
            ("PRESSED", True, "ALLOW", 1),
            ("RECOVERING", True, "ALLOW", 1),
            ("CRITICAL", True, "ALLOW", 0),
            (UNKNOWN, True, "ALLOW", 0),
            ("HEALTHY", False, "ALLOW", 0),
            ("HEALTHY", True, "DENY", 0),
            ("HEALTHY", True, UNKNOWN, 0),
            ("HEALTHY", None, "ALLOW", MAX_ADDITIONAL_HEAVY_CODING),
        ]
        for band, allow, storage, expected in cases:
            with self.subTest(band=band, allow=allow, storage=storage):
                capacity = recommend_capacity(
                    band, aos_allow=allow, storage_admission=storage
                )
                self.assertEqual(capacity["additional_heavy_coding"], expected)

    def test_cpu_heavy_remaining_narrows(self) -> None:
        capacity = recommend_capacity(
            "HEALTHY", aos_allow=True, storage_admission="ALLOW", cpu_heavy_remaining=1
        )
        self.assertEqual(capacity["additional_heavy_coding"], 1)
        capacity = recommend_capacity(
            "HEALTHY", aos_allow=True, storage_admission="ALLOW", cpu_heavy_remaining=0
        )
        self.assertEqual(capacity["additional_heavy_coding"], 0)

    def test_never_exceeds_ceiling(self) -> None:
        capacity = recommend_capacity(
            "HEALTHY", aos_allow=True, storage_admission="ALLOW", cpu_heavy_remaining=99
        )
        self.assertEqual(capacity["additional_heavy_coding"], MAX_ADDITIONAL_HEAVY_CODING)
        self.assertEqual(capacity["max_absolute"], MAX_ADDITIONAL_HEAVY_CODING)


class TestInfrastructureSignals(unittest.TestCase):
    def test_stacking_zeroes_capacity(self) -> None:
        snap = compose_snapshot(
            full_sources(proc={
                "active_processes": 300,
                "stuck_finds": 0,
                "janitor_processes": 4,
                "disk_watch_processes": 1,
                "clock_ticks": 100,
            }),
            now=NOW,
            band="HEALTHY",
        )
        infra = snap["_meta"]["infrastructure"]
        self.assertIs(infra["stacking_detected"], True)
        capacity = snap["recommended_worker_capacity"]
        self.assertEqual(capacity["additional_heavy_coding"], 0)
        self.assertIn("process_stacking_detected", capacity["constraints"])

    def test_stuck_find_zeroes_capacity(self) -> None:
        snap = compose_snapshot(
            full_sources(proc={
                "active_processes": 300,
                "stuck_finds": 2,
                "janitor_processes": 1,
                "disk_watch_processes": 1,
                "clock_ticks": 100,
            }),
            now=NOW,
            band="HEALTHY",
        )
        infra = snap["_meta"]["infrastructure"]
        self.assertEqual(infra["stuck_finds"], 2)
        self.assertEqual(
            snap["recommended_worker_capacity"]["additional_heavy_coding"], 0
        )
        self.assertIn(
            "stuck_filesystem_scan",
            snap["recommended_worker_capacity"]["constraints"],
        )

    def test_infrastructure_unknown_is_listed_not_invented(self) -> None:
        snap = compose_snapshot(
            full_sources(proc={"active_processes": 210}), now=NOW, band="HEALTHY"
        )
        infra = snap["_meta"]["infrastructure"]
        self.assertCountEqual(
            infra["unknown"],
            ["stuck_finds", "janitor_processes", "disk_watch_processes"],
        )
        # unknown infra does not block capacity on its own (band decides)
        self.assertEqual(
            snap["recommended_worker_capacity"]["additional_heavy_coding"],
            MAX_ADDITIONAL_HEAVY_CODING,
        )


class TestPersistence(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "resource-snapshot.json"

    def test_write_read_roundtrip(self) -> None:
        snap = compose_snapshot(full_sources(), now=NOW, band="HEALTHY")
        write_snapshot(snap, self.out)
        self.assertTrue(self.out.exists())
        self.assertFalse(self.out.with_suffix(".json.tmp").exists())
        loaded = read_snapshot(self.out, max_age_s=3600, now=NOW + 60)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["resource_band"], "HEALTHY")
        self.assertEqual(loaded["cpu_usage"], 42.5)

    def test_stale_snapshot_reads_as_none(self) -> None:
        snap = compose_snapshot(full_sources(), now=NOW, band="HEALTHY")
        write_snapshot(snap, self.out)
        stale = read_snapshot(self.out, max_age_s=180, now=NOW + 1000)
        self.assertIsNone(stale)

    def test_corrupt_or_missing_reads_as_none(self) -> None:
        self.assertIsNone(read_snapshot(self.out))
        self.out.write_text("{not json", encoding="utf-8")
        self.assertIsNone(read_snapshot(self.out))

    def test_oversize_document_rejected(self) -> None:
        with self.assertRaises(ValueError):
            write_snapshot({"pad": "x" * (70 * 1024)}, self.out)


class TestCollectSourcesFailureTolerance(unittest.TestCase):
    def test_all_unreadable_paths_still_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = ResourcePaths(
                aos_resource=str(Path(tmp) / "nope.json"),
                aos_db=str(Path(tmp) / "nope.sqlite3"),
                studio_db=str(Path(tmp) / "nope.sqlite3"),
                janitor=str(Path(tmp) / "no-janitor"),
                snapshot_out=str(Path(tmp) / "out.json"),
                governor_state_dir=str(Path(tmp) / "home"),
            )
            sources = collect_sources(paths, runner=lambda argv, t: (-1, ""))
            self.assertIsNone(sources["aos"])
            self.assertIsNone(sources["scheduler"])
            self.assertIsNone(sources["studio"])
            self.assertIsNone(sources["janitor"])
            snap = build_snapshot(
                paths, runner=lambda argv, t: (-1, ""), now=time.time(), band=UNKNOWN
            )
            self.assertEqual(snap["storage_admission"], UNKNOWN)
            self.assertEqual(snap["resource_band"], UNKNOWN)
            self.assertEqual(snap["recommended_worker_capacity"]["additional_heavy_coding"], 0)


class TestContractDocument(unittest.TestCase):
    def test_contract_json_matches_code_fields(self) -> None:
        doc = json.loads(
            (REPO_ROOT / "NOAERTH_RESOURCE_CONTRACT.json").read_text(encoding="utf-8")
        )
        self.assertEqual(doc["schema"], "noaerth.resource-contract/1")
        self.assertEqual(set(doc["fields"]), set(CONTRACT_FIELDS))
        self.assertEqual(
            doc["policy"]["admission"]["fail_closed"] is not None, True
        )
        self.assertEqual(
            doc["policy"]["capacity"]["initial_concurrency"],
            "at most 2 additional heavyweight coding workers "
            "(MAX_ADDITIONAL_HEAVY_CODING=2)",
        )


if __name__ == "__main__":
    unittest.main()
