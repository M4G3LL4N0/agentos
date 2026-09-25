"""End-to-end A2A interop against the loopback test peer.

Covers the full AgentOS -> remote loop: card discovery (including honest
unreachability and untrusted-by-default posture), task submission, bounded
status polling, real measured result fetching, 4xx rejection of a malformed
envelope, and the task state machine's two honest cancel outcomes.
"""

from __future__ import annotations

import re
import unittest

from agentos.a2a import (
    A2ADiscovery,
    A2AError,
    A2APeerUnreachable,
    UNTRUSTED,
    build_task_envelope,
    cancel_task,
    discover,
    fetch_result,
    submit_task,
    wait_for_result,
)
from agentos.a2a_local_peer import (
    DEFAULT_OPERATION,
    MAX_WORK_ROUNDS,
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_QUEUED,
    start_peer,
    stop_peer,
)

HEX64 = re.compile(r"^[0-9a-f]{64}$")


class A2AInteropTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.peer_thread, self.base_url = start_peer()
        self.discovery = A2ADiscovery(timeout=5.0)

    def tearDown(self) -> None:
        stop_peer(self.peer_thread)

    # ---------- discovery -------------------------------------------------

    def test_discovery_returns_card_with_advertised_operation(self) -> None:
        card = discover(self.base_url)
        self.assertEqual(card.type, "test-peer")
        self.assertEqual(card.trust, UNTRUSTED)
        self.assertIn(DEFAULT_OPERATION, card.operations)
        self.assertIn(DEFAULT_OPERATION, card.capabilities)
        self.assertEqual(card.url, self.base_url)
        self.assertTrue(card.name)
        self.assertTrue(card.version)

    def test_discovery_reports_not_running_peer_unreachable(self) -> None:
        dead_thread, dead_url = start_peer()
        stop_peer(dead_thread)
        with self.assertRaises(A2APeerUnreachable):
            self.discovery.discover(dead_url + "/agent")

    def test_discovery_never_trusts_wire_claims(self) -> None:
        card = self.discovery.from_payload(
            {
                "name": "sneaky",
                "version": "9.9.9",
                "trust": "trusted",  # a card asserting its own trust
                "operations": ["a2a.test"],
                "url": "http://127.0.0.1:1",
            },
            source_url="http://127.0.0.1:9999",
        )
        self.assertEqual(card.trust, UNTRUSTED)
        self.assertEqual(card.url, "http://127.0.0.1:9999")

    def test_approval_requires_explicit_evidence(self) -> None:
        card = self.discovery.from_payload(
            {"name": "peer", "operations": ["a2a.test"]}
        )
        self.assertEqual(card.trust, UNTRUSTED)
        self.assertEqual(
            self.discovery.approve(card, evidence=[]).trust,
            UNTRUSTED,
        )
        approved = self.discovery.approve(
            card,
            evidence=[{"kind": "explicit_approval", "detail": "human reviewed"}],
        )
        self.assertEqual(approved.trust, "trusted")
        self.assertEqual(card.trust, UNTRUSTED)

    # ---------- full interop loop -----------------------------------------

    def test_full_interop_submit_poll_fetch_result(self) -> None:
        payload = {"message": "ping a2a peer", "work": 5_000}
        envelope = build_task_envelope(DEFAULT_OPERATION, payload)
        ack = submit_task(self.base_url, envelope)
        task_id = ack["task_id"]
        self.assertTrue(task_id.startswith("task_"))
        self.assertEqual(ack["status"], STATUS_QUEUED)

        record = wait_for_result(
            self.base_url, task_id, max_attempts=200, interval_seconds=0.01
        )
        self.assertEqual(record["status"], STATUS_DONE)

        result = fetch_result(self.base_url, task_id)
        self.assertEqual(result["status"], STATUS_DONE)
        self.assertEqual(result["echo"], payload)
        self.assertRegex(result["digest"], HEX64)
        self.assertIsInstance(result["duration_ms"], (int, float))
        self.assertGreaterEqual(result["duration_ms"], 0.0)
        self.assertTrue(result["received_at"])
        self.assertTrue(result["completed_at"])
        self.assertEqual(result["operation"], DEFAULT_OPERATION)

    # ---------- honest failures --------------------------------------------

    def test_submit_missing_required_field_rejected_4xx(self) -> None:
        envelope = build_task_envelope(DEFAULT_OPERATION, {"message": "x"})
        del envelope["operation"]
        with self.assertRaises(A2AError) as ctx:
            submit_task(self.base_url, envelope)
        status = ctx.exception.status_code
        self.assertTrue(status is not None and 400 <= status < 500)

    def test_submit_unsupported_operation_rejected_4xx(self) -> None:
        envelope = build_task_envelope("not.advertised", {"message": "x"})
        with self.assertRaises(A2AError) as ctx:
            submit_task(self.base_url, envelope)
        status = ctx.exception.status_code
        self.assertTrue(status is not None and 400 <= status < 500)

    # ---------- cancel state machine ---------------------------------------

    def test_cancel_active_task_and_result_never_exists(self) -> None:
        # Heavy task A occupies the peer's single FIFO worker for a real,
        # measured digest window; light task B is queued behind it, so B is
        # deterministically still queued/runnable (not done) when we cancel.
        ack_a = submit_task(
            self.base_url,
            build_task_envelope(
                DEFAULT_OPERATION, {"message": "backpressure", "work": MAX_WORK_ROUNDS}
            ),
        )
        ack_b = submit_task(
            self.base_url,
            build_task_envelope(DEFAULT_OPERATION, {"message": "cancel me", "work": 0}),
        )
        self.assertEqual(ack_b["status"], STATUS_QUEUED)

        outcome = cancel_task(self.base_url, ack_b["task_id"])
        self.assertEqual(outcome["status"], STATUS_CANCELLED)

        record = wait_for_result(
            self.base_url, ack_b["task_id"], max_attempts=100, interval_seconds=0.01
        )
        self.assertEqual(record["status"], STATUS_CANCELLED)
        self.assertIsNone(record["result"])
        with self.assertRaises(A2AError) as ctx:
            fetch_result(self.base_url, ack_b["task_id"])
        self.assertEqual(ctx.exception.status_code, 200)

        # A still completes normally (bounded), proving cancel did not wedge
        # the worker and real execution continues for other tasks.
        done = wait_for_result(
            self.base_url,
            ack_a["task_id"],
            max_attempts=300,
            interval_seconds=0.02,
        )
        self.assertEqual(done["status"], STATUS_DONE)

    def test_cancel_done_task_conflicts_409(self) -> None:
        envelope = build_task_envelope(DEFAULT_OPERATION, {"message": "fast", "work": 0})
        ack = submit_task(self.base_url, envelope)
        record = wait_for_result(
            self.base_url, ack["task_id"], max_attempts=100, interval_seconds=0.01
        )
        self.assertEqual(record["status"], STATUS_DONE)
        with self.assertRaises(A2AError) as ctx:
            cancel_task(self.base_url, ack["task_id"])
        self.assertEqual(ctx.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()