from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from agentos.store import Store


BASE = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
DIGEST = "a" * 64


class DurableLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "agentos.sqlite3"
        self.store = Store(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def claim(self, task="task-1", worker="forge-worker-1", request="request-1", ttl=30, now=BASE):
        return self.store.claim_lease(task, worker, request, ttl, now=now)

    def test_claim_is_persisted_and_starts_at_epoch_one(self):
        lease = self.claim()
        self.assertEqual(lease["fencing_epoch"], 1)
        self.assertEqual(lease["status"], "ACTIVE")
        self.assertEqual(self.store.get_lease(lease["lease_id"]), lease)
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.get_lease(lease["lease_id"])["status"], "ACTIVE")

    def test_duplicate_request_is_idempotent_but_bound_to_same_worker(self):
        first = self.claim()
        duplicate = self.claim()
        self.assertEqual(first, duplicate)
        with self.assertRaisesRegex(ValueError, "another worker"):
            self.claim(worker="other-worker")

    def test_unexpired_lease_blocks_a_second_worker_request(self):
        first = self.claim()
        with self.assertRaisesRegex(ValueError, "unexpired active lease"):
            self.claim(worker="worker-2", request="request-2")
        self.assertEqual(self.store.get_lease(first["lease_id"])["status"], "ACTIVE")

    def test_expiry_increments_epoch_and_fences_old_lease(self):
        old = self.claim()
        new = self.claim(worker="worker-2", request="request-2", now=BASE + timedelta(seconds=30))
        self.assertEqual(new["fencing_epoch"], 2)
        self.assertEqual(self.store.get_lease(old["lease_id"])["status"], "EXPIRED")
        with self.assertRaisesRegex(ValueError, "not active"):
            self.store.commit_lease_result(old["lease_id"], "forge-worker-1", 1, DIGEST, now=BASE + timedelta(seconds=31))
        accepted = self.store.commit_lease_result(new["lease_id"], "worker-2", 2, DIGEST, now=BASE + timedelta(seconds=31))
        self.assertEqual(accepted["status"], "COMPLETED")

    def test_result_at_expiry_is_rejected_and_expiry_persists(self):
        lease = self.claim(ttl=5)
        with self.assertRaisesRegex(ValueError, "expired before result commit"):
            self.store.commit_lease_result(lease["lease_id"], "forge-worker-1", 1, DIGEST, now=BASE + timedelta(seconds=5))
        self.assertEqual(self.store.get_lease(lease["lease_id"])["status"], "EXPIRED")

    def test_result_requires_matching_worker_epoch_and_digest(self):
        lease = self.claim()
        with self.assertRaisesRegex(ValueError, "worker identity or fencing epoch"):
            self.store.commit_lease_result(lease["lease_id"], "impostor", 1, DIGEST, now=BASE)
        with self.assertRaisesRegex(ValueError, "worker identity or fencing epoch"):
            self.store.commit_lease_result(lease["lease_id"], "forge-worker-1", 2, DIGEST, now=BASE)
        with self.assertRaisesRegex(ValueError, "64-character hexadecimal"):
            self.store.commit_lease_result(lease["lease_id"], "forge-worker-1", 1, "bad", now=BASE)
        self.assertEqual(self.store.get_lease(lease["lease_id"])["status"], "ACTIVE")

    def test_result_commit_is_idempotent_only_for_same_digest(self):
        lease = self.claim()
        result = self.store.commit_lease_result(lease["lease_id"], "forge-worker-1", 1, DIGEST, now=BASE + timedelta(seconds=1))
        self.assertEqual(result["status"], "COMPLETED")
        replay = self.store.commit_lease_result(lease["lease_id"], "forge-worker-1", 1, DIGEST, now=BASE + timedelta(seconds=2))
        self.assertEqual(replay, result)
        with self.assertRaisesRegex(ValueError, "different result"):
            self.store.commit_lease_result(lease["lease_id"], "forge-worker-1", 1, "b" * 64, now=BASE + timedelta(seconds=2))

    def test_cancellation_fences_late_result_and_is_idempotent(self):
        lease = self.claim()
        cancelled = self.store.cancel_lease(lease["lease_id"], now=BASE + timedelta(seconds=1))
        self.assertEqual(cancelled["status"], "CANCELLED")
        self.assertEqual(self.store.cancel_lease(lease["lease_id"], now=BASE + timedelta(seconds=2)), cancelled)
        with self.assertRaisesRegex(ValueError, "not active"):
            self.store.commit_lease_result(lease["lease_id"], "forge-worker-1", 1, DIGEST, now=BASE + timedelta(seconds=2))

    def test_invalid_ttl_and_naive_time_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "ttl_seconds"):
            self.claim(ttl=3601)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            self.claim(now=datetime(2026, 11, 26, 12, 0))

    def test_concurrent_claimers_cannot_both_own_one_task(self):
        def attempt(worker):
            try:
                return ("claimed", self.store.claim_lease("task-race", worker, f"request-{worker}", 30, now=BASE))
            except ValueError as exc:
                return ("blocked", str(exc))

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(attempt, ["worker-a", "worker-b"]))
        self.assertEqual(sum(status == "claimed" for status, _ in outcomes), 1)
        self.assertEqual(sum(status == "blocked" for status, _ in outcomes), 1)
        active = self.store.conn.execute(
            "SELECT COUNT(*) FROM task_leases WHERE task_id = 'task-race' AND status = 'ACTIVE'"
        ).fetchone()[0]
        self.assertEqual(active, 1)


if __name__ == "__main__":
    unittest.main()
