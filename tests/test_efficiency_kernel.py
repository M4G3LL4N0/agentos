"""Low-usage efficiency kernel integration tests.

Pins the garbage-collected IntelligenceCache honest contract (L0-L6) plus the
efficiency triage/team/delta paths as exercised through AgentOS services and
the shared Store. These tests encode the "no fake progress" rules:
hits are honest replays with provenance and zero model cost; every miss has a
reason; secrets and HIGHLY_SENSITIVE material are never cached; dependency
invalidation is narrow.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from agentos import efficiency as eff
from agentos.intelligence_cache import (
    CATEGORIES,
    DEFAULT_TTL_SECONDS,
    LAYERS,
    SecurityScope,
    CacheEntry,
    CacheEntryType,
    CacheLevel,
    CacheState,
    IntelligenceCache,
    content_hash,
    intent_shingles,
    intcache_key,
    looks_secret,
    semantic_key,
)
from agentos.services import AgentOS
from agentos.store import Store


def _fresh_home() -> tempfile.TemporaryDirectory:
    home = tempfile.TemporaryDirectory()
    os.environ["AGENTOS_HOME"] = home.name
    return home


class TestIntelligenceCacheContract(unittest.TestCase):
    """The cache's honesty contract against a real Store."""

    def setUp(self) -> None:
        self._home = _fresh_home()
        store = Store(os.path.join(os.environ["AGENTOS_HOME"], "agentos.db"))
        self.cache = IntelligenceCache(
            store, None, project="kernel", requester_scope=SecurityScope.internal
        )

    def tearDown(self) -> None:
        self._home.cleanup()

    def test_contract_constants(self) -> None:
        self.assertEqual(len(LAYERS), 7)
        self.assertEqual(len(CATEGORIES), 14)
        self.assertEqual(DEFAULT_TTL_SECONDS["default"], 21600)

    def test_project_is_scoped_and_round_trips(self) -> None:
        entry = self.cache.build_entry(
            "lint the codebase", category=CacheEntryType.exact
        )
        self.cache.put(entry)
        got = self.cache.get("lint the codebase")
        self.assertTrue(got["hit"])
        stored = self.cache.store.get_intelligence_entry(got["cacheKey"])
        self.assertEqual(stored["project"], "kernel")
        reloaded = CacheEntry.from_dict(stored)
        self.assertEqual(reloaded.project, "kernel")

    def test_exact_hit_carries_provenance_and_zero_model_calls(self) -> None:
        self.cache.put(
            self.cache.build_entry("sum 1 2 3", payload={"answer": 6})
        )
        got = self.cache.get("sum 1 2 3")
        self.assertTrue(got["hit"])
        self.assertEqual(got["modelCalls"], 0)
        self.assertEqual(got["level"], CacheLevel.L0)
        self.assertTrue(got["provenance"].startswith("intcache://"))
        self.assertEqual(got["payload"]["answer"], 6)

    def test_unrecorded_intent_is_an_honest_miss_with_reason(self) -> None:
        got = self.cache.get("never recorded")
        self.assertFalse(got["hit"])
        self.assertEqual(got["modelCalls"], 1)
        self.assertIn("no entry stored", got["reason"])

    def test_order_change_exact_misses_semantic_hits(self) -> None:
        self.cache.put(
            self.cache.build_entry("build the best site", category=CacheEntryType.exact)
        )
        exact = self.cache.get("best site the build")
        self.assertFalse(exact["hit"])
        sem = self.cache.get_semantic("best site the build")
        self.assertTrue(sem["hit"])
        self.assertEqual(sem["level"], CacheLevel.L4)
        self.assertEqual(sem["modelCalls"], 0)

    def test_invalid_entry_stays_invalid_after_put(self) -> None:
        entry = self.cache.build_entry(
            "invalid target", status=CacheState.INVALID
        )
        self.cache.put(entry)
        stored = self.cache.store.get_intelligence_entry(entry.cacheKey)
        self.assertEqual(stored["status"], CacheState.INVALID)
        self.assertFalse(self.cache.get("invalid target")["hit"])

    def test_semantic_alias_is_scoped_by_output_schema(self) -> None:
        self.cache.put(self.cache.build_entry(
            "schema task",
            output_schema="schema-a",
            payload={"value": "a"},
        ))
        self.assertFalse(self.cache.get_semantic("schema task")["hit"])
        self.assertTrue(
            self.cache.get_semantic("schema task", output_schema="schema-a")["hit"]
        )
        self.assertFalse(
            self.cache.get_semantic("schema task", output_schema="schema-b")["hit"]
        )

    def test_semantic_key_is_order_independent_bag(self) -> None:
        self.assertEqual(
            semantic_key("build the best site"),
            semantic_key("best site the build"),
        )
        self.assertEqual(
            intent_shingles("build the best site")[0],
            intent_shingles("best site the build")[0],
        )

    def test_stale_entry_is_rejected_not_silently_fresh(self) -> None:
        entry = self.cache.build_entry("stale target", ttl=1)
        entry.lastVerifiedAt = "2000-01-01T00:00:00+00:00"
        self.cache.store.save_intelligence_entry(entry.to_dict())
        got = self.cache.get("stale target")
        self.assertFalse(got["hit"])
        self.assertIn("fresh reasoning required", got["reason"])
        self.assertGreater(self.cache.stats()["staleRejected"], 0)

    def test_scope_gate_blocks_cross_scope_read(self) -> None:
        entry = self.cache.build_entry(
            "confidential plan",
            security_scope=SecurityScope.confidential,
            category=CacheEntryType.exact,
        )
        self.cache.put(entry)
        public_cache = IntelligenceCache(
            self.cache.store, None,
            project="kernel", requester_scope=SecurityScope.public,
        )
        got = public_cache.get("confidential plan")
        self.assertFalse(got["hit"])
        self.assertIn("security scope", got["reason"])
        self.assertGreater(self.cache.stats()["scopeRejections"], 0)

    def test_highly_sensitive_is_never_cached(self) -> None:
        with self.assertRaises(ValueError):
            self.cache.build_entry(
                "top secret", security_scope=SecurityScope.highly_sensitive
            )

    def test_secret_bearing_payload_never_cached(self) -> None:
        for secret in (
            {"token": "access_token=abc123"},
            {"content": "-----BEGIN RSA PRIVATE KEY-----"},
            {"content": "PRIVATE KEY BLOCK"},
            {"content": "api_key=xyz"},
        ):
            with self.assertRaises(ValueError):
                self.cache.build_entry(
                    "store secret", payload=secret, category=CacheEntryType.exact
                )

    def test_looks_secret_heuristics(self) -> None:
        for probe in (
            "-----BEGIN RSA PRIVATE KEY-----",
            "-----BEGIN EC PRIVATE KEY-----",
            "session_token=abc",
            "refresh_token=abc",
            "recovery code: 123",
            "bearer eyJhbGciOi",
        ):
            self.assertTrue(looks_secret(probe), probe)
        self.assertFalse(looks_secret("build a landing page"))
        self.assertFalse(looks_secret({"answer": 42}))

    def test_narrow_dependency_invalidation(self) -> None:
        self.cache.put(
            self.cache.build_entry(
                "compile",
                dependencies={"src/main.c": "hash-old"},
                category=CacheEntryType.exact,
            )
        )
        self.cache.put(
            self.cache.build_entry(
                "other task",
                dependencies={"unrelated": "keep"},
                category=CacheEntryType.exact,
            )
        )
        result = self.cache.invalidate({"src/main.c": "hash-new"})
        self.assertEqual(len(result["invalidated"]), 1)
        self.assertEqual(self.cache.get("compile")["hit"], False)
        self.assertTrue(self.cache.get("other task")["hit"])
        self.assertGreater(self.cache.stats()["invalidations"], 0)

    def test_mark_verified_refreshes_only_fresh_eligible(self) -> None:
        entry = self.cache.build_entry("verify me", ttl=1)
        entry.lastVerifiedAt = "2000-01-01T00:00:00+00:00"
        self.cache.store.save_intelligence_entry(entry.to_dict())
        self.assertTrue(self.cache.mark_verified(entry.cacheKey))
        self.assertTrue(self.cache.get("verify me")["hit"])

    def test_cache_key_functional(self) -> None:
        key = intcache_key("lint", "json", "kernel")
        self.assertEqual(key, intcache_key("lint", "json", "kernel"))
        self.assertNotEqual(
            key, intcache_key("lint", "json", "another-project")
        )
        self.assertEqual(content_hash({"b": 2, "a": 1}), content_hash({"a": 1, "b": 2}))


class TestEfficiencyThroughServices(unittest.TestCase):
    """Services-path (what the CLI calls) for the kernel commands."""

    def setUp(self) -> None:
        self._home = _fresh_home()
        self.app = AgentOS()

    def tearDown(self) -> None:
        self.app.store.close()
        self._home.cleanup()

    def test_fingerprint_dedupe(self) -> None:
        first = self.app.efficiency_fingerprint(
            {"intent": "refactor utils", "output_schema": "patch", "constraints": []}
        )
        second = self.app.efficiency_fingerprint(
            {"intent": "refactor utils", "output_schema": "patch", "constraints": []}
        )
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])

    def test_delta_context_never_full_history(self) -> None:
        result = self.app.efficiency_delta(
            {"src/a.py": "h-old", "src/b.py": "same"},
            {"src/a.py": "h-new", "src/b.py": "same"},
            "fix the bug",
            cache_scope="intcache",
        )
        delta = result["delta"]
        self.assertEqual(delta["changedInputs"], {"src/a.py": "h-new"})
        self.assertIn("intcache/src/a.py", delta["invalidatedCacheRefs"])
        self.assertNotIn("src/b.py", delta["changedInputs"])

    def test_triage_cheap_before_premium(self) -> None:
        decision = self.app.efficiency_triage(
            "sum and count totals", exact_cache_hit=True
        )["decision"]
        self.assertEqual(decision["resolution"], "CACHE_RETURN")

    def test_team_defaults_single_worker(self) -> None:
        plan = self.app.efficiency_team()["teamPlan"]
        self.assertEqual(plan["size"], 1)
        self.assertEqual(plan["roles"], ["worker"])

    def test_cache_put_get_invalidate_via_services(self) -> None:
        put = self.app.efficiency_cache_put(
            "seed the kernel", category="exact", payload={"ok": True}
        )
        self.assertTrue(put["cache"]["id"])
        hit = self.app.efficiency_cache_get("seed the kernel")
        self.assertTrue(hit["cache"]["hit"])
        stats = self.app.efficiency_stats()["efficiency"]
        self.assertEqual(stats["entries"], 2)  # exact row + semantic alias
        self.assertGreater(stats["exactHits"], 0)
        self.assertEqual(sum(1 for _ in CATEGORIES) > 0, True)

    def test_stats_shape(self) -> None:
        stats = self.app.efficiency_stats()["efficiency"]
        for key in (
            "entries", "hits", "misses", "semanticHits", "exactHits",
            "modelCallsAvoided", "scopeRejections", "staleRejected",
            "invalidations", "hitRate", "enabled",
        ):
            self.assertIn(key, stats)


class TestEfficiencyPureFunctions(unittest.TestCase):
    def test_triage_grok_late_escalation(self) -> None:
        d = eff.triage(
            "need persistent cloud computer and authenticated browser for durable supervisor identity"
        )
        self.assertEqual(d.resolution, eff.Resolution.GROKBOT)

    def test_plan_team_verifier_only_on_justification(self) -> None:
        single = eff.plan_team()
        self.assertEqual(single.size, 1)
        multi = eff.plan_team(
            decomposable=True,
            independent_subtasks=3,
            quality_floor="VERIFIED",
        )
        self.assertIn("verifier", multi.roles)

    def test_content_state_hashes(self) -> None:
        state = eff.content_state({"a.py": "print('x')"})
        self.assertEqual(len(state), 1)
        self.assertEqual(state["a.py"], content_hash("print('x')"))


if __name__ == "__main__":
    unittest.main()