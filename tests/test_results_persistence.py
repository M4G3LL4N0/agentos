"""Durable persistence for the federation result cache.

Covers the conservative results-store methods on ``Store`` plus the thin
``ResultCache.save_to``/``from_store`` round-trip hooks. Mirrors the fixture
pattern used in ``test_store.py`` (temp db, reopen to simulate a restart).
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agentos.results_cache import ResultCache, cache_key, hit_payload
from agentos.store import Store


def _envelope_json(extra: dict | None = None) -> str:
    payload = {
        "status": "success",
        "executor": "echo",
        "cache_class": "repo_facts",
        "token_count": "64",
        "result": {"summary": "deterministic material"},
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload)


class ResultCachePersistenceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "agentos.db"
        self.store = Store(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def reopen(self) -> Store:
        self.store.close()
        self.store = Store(self.path)
        return self.store


class TestSaveReloadRoundTrip(ResultCachePersistenceTestCase):
    def test_entry_survives_fresh_store_instance(self) -> None:
        self.store.save_result_cache_entry(
            "key-roundtrip",
            _envelope_json(),
            ttl_seconds=300,
            usage_class="cached",
            stability_class="repo_facts",
        )
        store2 = self.reopen()
        entry = store2.get_result_cache_entry("key-roundtrip")
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry["cache_key"], "key-roundtrip")
        self.assertEqual(entry["ttl_seconds"], 300)
        self.assertEqual(entry["usage_class"], "cached")
        self.assertEqual(entry["stability_class"], "repo_facts")
        self.assertEqual(entry["envelope"]["executor"], "echo")
        self.assertEqual(entry["envelope"]["result"]["summary"], "deterministic material")
        self.assertEqual(entry["hits"], 0)

    def test_save_to_round_trips_through_fresh_store(self) -> None:
        self.store.save_result_cache_entry(
            "key-save-to",
            _envelope_json(),
            ttl_seconds=600,
            usage_class="cached",
            stability_class="deterministic_output",
        )
        cache = ResultCache(self.store)
        store2 = Store(Path(self.tmp.name) / "copy.db")
        try:
            persisted = cache.save_to(store2)
            self.assertEqual(persisted, 1)
            reloaded = ResultCache.from_store(store2)
            freshest = reloaded.store.get_result_cache_entry("key-save-to")
            self.assertIsNotNone(freshest)
            assert freshest is not None
            self.assertEqual(freshest["ttl_seconds"], 600)
            self.assertEqual(freshest["stability_class"], "deterministic_output")
        finally:
            store2.close()


class TestTouchIncrementsHits(ResultCachePersistenceTestCase):
    def test_touch_bumps_hits(self) -> None:
        self.store.save_result_cache_entry(
            "key-touch",
            _envelope_json(),
            ttl_seconds=300,
            usage_class="cached",
            stability_class="repo_facts",
        )
        self.assertEqual(self.store.get_result_cache_entry("key-touch")["hits"], 0)
        self.assertTrue(self.store.touch_result_cache_entry("key-touch"))
        entry = self.store.get_result_cache_entry("key-touch")
        assert entry is not None
        self.assertEqual(entry["hits"], 1)
        self.assertFalse(self.store.touch_result_cache_entry("key-missing"))


class TestPruneRemovesExpired(ResultCachePersistenceTestCase):
    def test_prune_drops_expired_keeps_fresh(self) -> None:
        now = datetime.now(timezone.utc)
        past = (now - timedelta(hours=2)).isoformat()
        self.store.save_result_cache_entry(
            "key-fresh",
            _envelope_json({"payload": "fresh"}),
            ttl_seconds=1800,
            usage_class="cached",
            stability_class="repo_facts",
        )
        self.store.conn.execute(
            "INSERT INTO result_cache ("
            " cache_key, envelope_json, created_at, ttl_seconds, hits,"
            " usage_class, stability_class"
            ") VALUES (?,?,?,?,?,?,?)",
            (
                "key-expired",
                json.dumps({"payload": "stale"}),
                past,
                300,
                0,
                "cached",
                "repo_facts",
            ),
        )
        self.store.conn.commit()
        removed = self.store.prune_result_cache(now_iso=now.isoformat())
        self.assertEqual(removed, 1)
        self.assertIsNone(self.store.get_result_cache_entry("key-expired"))
        fresh = self.store.get_result_cache_entry("key-fresh")
        assert fresh is not None
        self.assertEqual(fresh["envelope"]["payload"], "fresh")

    def test_prune_with_zero_ttl_keeps_entry(self) -> None:
        self.store.save_result_cache_entry(
            "key-zero-ttl",
            _envelope_json(),
            ttl_seconds=0,
            usage_class="cached",
            stability_class="dynamic",
        )
        self.assertEqual(self.store.prune_result_cache(), 0)
        self.assertIsNotNone(self.store.get_result_cache_entry("key-zero-ttl"))


class TestHonestCachedEnvelope(ResultCachePersistenceTestCase):
    def test_store_envelope_carries_cache_provenance(self) -> None:
        job = {
            "targetCapability": "fs.read",
            "deliverable": "readme",
            "compactContext": "stable probe",
            "dataClass": "PUBLIC",
            "executionClass": "INSPECT",
        }
        key = cache_key(job, "1.0")
        self.store.save_result_cache_entry(
            key,
            _envelope_json(),
            ttl_seconds=900,
            usage_class="cached",
            stability_class="stable_probe",
        )
        store2 = self.reopen()
        entry = store2.get_result_cache_entry(key)
        self.assertIsNotNone(entry)
        assert entry is not None
        envelope = hit_payload(job, entry["envelope"], key)
        self.assertEqual(envelope["provenance"], f"cache://{key}")
        self.assertEqual(envelope["actualCost"], "0")
        self.assertEqual(envelope["usageClass"], "cached")
        self.assertEqual(envelope["executor"], "echo")


class TestMigrationIdempotent(ResultCachePersistenceTestCase):
    def test_creating_store_twice_does_not_error(self) -> None:
        store2 = Store(self.path)
        store2.save_result_cache_entry(
            "key-migrate",
            _envelope_json(),
            ttl_seconds=300,
            usage_class="cached",
            stability_class="repo_facts",
        )
        entry = Store(self.path).get_result_cache_entry("key-migrate")
        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry["envelope"]["executor"], "echo")
        store2.close()


if __name__ == "__main__":
    unittest.main()