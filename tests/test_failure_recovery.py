"""Task 8: typed failure taxonomy + bounded, class-dependent recovery.

Failure classification stays provider-neutral (no provider logic in the
core); recovery is bounded (never infinite), class-dependent, persists
every attempt, and never really sleeps (delays are computed, not slept).
"""

import tempfile
import time
import unittest
from pathlib import Path

from agentos.adapters.base import AdapterResult
from agentos.engine import Engine
from agentos.events import EventBus
from agentos.models import Objective, ObjectiveState, new_id
from agentos.registry import CapabilityRegistry
from agentos.store import Store
from agentos.verify import backoff_delay_seconds, classify_failure, retryable


class CountingAdapter:
    """Fake adapter that records executions (must stay at zero on recovery)."""

    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, operation, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        return AdapterResult(ok=True, output={"echo": True})

    def probe(self) -> bool:
        return True


class RecoveryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "agentos.db")
        self.bus = EventBus(self.store)
        self.registry = CapabilityRegistry(self.store, self.bus)
        self.engine = Engine(self.store, self.bus, self.registry)
        self.counting = CountingAdapter()
        self._adapter_for = self.registry.adapter_for
        self.registry.adapter_for = lambda capability: self.counting  # type: ignore[assignment]

    def tearDown(self) -> None:
        self.registry.adapter_for = self._adapter_for  # type: ignore[assignment]
        self.store.close()
        self.tmp.cleanup()

    def make_objective(self, title: str = "t") -> Objective:
        objective = Objective(id=new_id("obj"), title=title, description="d")
        self.store.save_objective(objective)
        return objective


class TestTypedTaxonomy(RecoveryTestCase):
    def test_binary_missing(self) -> None:
        self.assertEqual(
            classify_failure("opencode: binary not found", None), "BINARY_MISSING"
        )

    def test_new_classes(self) -> None:
        self.assertEqual(
            classify_failure("missing API key: set it before retry", None),
            "AUTH_REQUIRED",
        )
        self.assertEqual(
            classify_failure("invalid configuration: bad config value", None),
            "CONFIGURATION_ERROR",
        )
        self.assertEqual(
            classify_failure("rate limit exceeded, slow down", None), "RATE_LIMIT"
        )
        self.assertEqual(
            classify_failure("blocked by policy: disallowed command", None),
            "POLICY_BLOCK",
        )
        self.assertEqual(
            classify_failure("merge conflict in output file", None), "CONFLICT"
        )
        self.assertEqual(
            classify_failure("build failed: compilation error", None), "BUILD_FAILURE"
        )
        self.assertEqual(
            classify_failure("typecheck failed: mypy errors", None),
            "TYPECHECK_FAILURE",
        )

    def test_existing_classes_keep_working(self) -> None:
        self.assertEqual(classify_failure("command timed out after 5s", None), "timeout")
        self.assertEqual(classify_failure("no such file: /x", None), "not_found")
        self.assertEqual(classify_failure("permission denied", None), "permission")
        self.assertEqual(classify_failure("unauthorized", None), "auth")
        self.assertEqual(
            classify_failure("connection refused by host", None), "connectivity"
        )
        self.assertEqual(
            classify_failure("requires a destination argument", None), "invalid_request"
        )
        self.assertEqual(classify_failure("boom", 1), "exit_code")
        self.assertEqual(classify_failure("weird provider wobble", None), "execution")
        self.assertEqual(classify_failure(None, None), "unknown")


class TestRetryableMembership(RecoveryTestCase):
    def test_binary_missing_not_retryable_timeout_is(self) -> None:
        self.assertFalse(retryable("BINARY_MISSING"))
        self.assertTrue(retryable("TIMEOUT"))

    def test_existing_membership_intact(self) -> None:
        self.assertTrue(retryable("timeout"))
        self.assertTrue(retryable("exit_code"))
        self.assertFalse(retryable("permission"))
        self.assertFalse(retryable("auth"))
        self.assertFalse(retryable("not_found"))

    def test_new_classes_membership(self) -> None:
        self.assertFalse(retryable("AUTH_REQUIRED"))
        self.assertFalse(retryable("POLICY_BLOCK"))
        self.assertFalse(retryable("CONFLICT"))
        self.assertTrue(retryable("RATE_LIMIT"))


class TestBoundedRecover(RecoveryTestCase):
    def test_auth_required_blocks_after_one_attempt_without_execution(self) -> None:
        objective = self.make_objective("needs credential")
        result = AdapterResult(
            ok=False, error="missing API key for the provider (sk-live-SECRET)"
        )
        outcome = self.engine.bounded_recover(objective, result, 3)
        self.assertEqual(outcome.terminal_state, ObjectiveState.BLOCKED)
        self.assertEqual(outcome.attempts_used, 1)
        # No further execution on retry: the adapter is never called.
        self.engine.bounded_recover(objective, result, 3)
        self.assertEqual(self.counting.calls, 0)
        stored = self.store.get_objective(objective.id)
        assert stored is not None
        self.assertEqual(stored.status, ObjectiveState.BLOCKED)
        blocked_reason = (stored.result or {}).get("blocked_reason", "")
        self.assertIn("API key", blocked_reason)
        self.assertNotIn("sk-live-SECRET", blocked_reason)
        failures = self.store.list_failures(objective.id)
        self.assertTrue(any(f.failure_type == "AUTH_REQUIRED" for f in failures))

    def test_rate_limit_bounded_backoff_without_sleeping(self) -> None:
        objective = self.make_objective("throttled")
        result = AdapterResult(ok=False, error="rate limit exceeded: slow down", exit_code=None)
        started = time.monotonic()
        outcome = self.engine.bounded_recover(objective, result, 4)
        elapsed = time.monotonic() - started
        self.assertLessEqual(outcome.attempts_used, 4)
        self.assertTrue(outcome.backoff_seconds)
        for delay in outcome.backoff_seconds:
            self.assertGreater(delay, 0)
            self.assertLessEqual(delay, 30.0)
        self.assertEqual(
            list(outcome.backoff_seconds),
            [backoff_delay_seconds(i) for i in range(1, outcome.attempts_used + 1)],
        )
        # Computed delays, never really slept: 1+2+4+8=15s would blow this.
        self.assertLess(elapsed, 5.0)
        self.assertEqual(self.counting.calls, 0)

    def test_policy_block_rejects_immediately(self) -> None:
        objective = self.make_objective("disallowed")
        result = AdapterResult(ok=False, error="blocked by policy: disallowed command")
        outcome = self.engine.bounded_recover(objective, result, 5)
        self.assertEqual(outcome.attempts_used, 1)
        self.assertEqual(outcome.action, "reject")
        self.assertEqual(self.counting.calls, 0)
        failures = self.store.list_failures(objective.id)
        self.assertTrue(any(f.failure_type == "POLICY_BLOCK" for f in failures))

    def test_conflict_reports_without_resolution(self) -> None:
        objective = self.make_objective("conflicting")
        result = AdapterResult(ok=False, error="merge conflict in output file")
        outcome = self.engine.bounded_recover(objective, result, 3)
        self.assertEqual(outcome.action, "report")
        self.assertEqual(self.counting.calls, 0)

    def test_hard_cap_bounds_attempts(self) -> None:
        from agentos.engine import ABSOLUTE_CAP

        objective = self.make_objective("flaky")
        result = AdapterResult(ok=False, error="command timed out after 5s")
        outcome = self.engine.bounded_recover(objective, result, ABSOLUTE_CAP + 10)
        self.assertLessEqual(outcome.attempts_used, ABSOLUTE_CAP)


if __name__ == "__main__":
    unittest.main()
