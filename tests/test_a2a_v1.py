"""A2A v1.0 remote-semantics interop against the loopback peer.

Covers the canonical stable surface only: well-known card discovery,
strict card parsing, version negotiation, JSON-RPC task lifecycle
(submit/status/result/cancel), unsupported operations, malformed peers,
and the auth-metadata boundary (no secrets cross the wire).
"""

from __future__ import annotations

import unittest

from agentos.a2a_local_peer import start_peer, stop_peer


class A2AV1InteropTests(unittest.TestCase):
    def setUp(self) -> None:
        self.peer_thread, self.base_url = start_peer()
        from agentos import a2a_v1

        self.v1 = a2a_v1

    def tearDown(self) -> None:
        stop_peer(self.peer_thread)

    def test_well_known_card_discovery(self) -> None:
        card = self.v1.discover_card(self.base_url)
        self.assertEqual(card.protocol_version, "1.0")
        self.assertTrue(card.name)
        self.assertTrue(card.skills)
        self.assertEqual(card.trust, "untrusted")

    def test_card_rejects_missing_name(self) -> None:
        with self.assertRaises(self.v1.V1InvalidCard):
            self.v1.V1AgentCard.from_dict({"version": "1.0"})

    def test_card_rejects_wire_trust_claim(self) -> None:
        card = self.v1.V1AgentCard.from_dict(
            {
                "name": "sneaky",
                "version": "9.9.9",
                "trust": "trusted",  # a card asserting its own trust
                "supportedInterfaces": [
                    {
                        "url": "http://127.0.0.1:9999/",
                        "protocolBinding": "JSONRPC",
                        "protocolVersion": "1.0",
                    }
                ],
                "skills": [
                    {
                        "id": "a2a.test",
                        "name": "test",
                        "description": "t",
                        "tags": ["test"],
                    }
                ],
            }
        )
        self.assertEqual(card.trust, "untrusted")

    def test_version_mismatch_rejected(self) -> None:
        with self.assertRaises(self.v1.V1VersionNotSupported):
            self.v1.discover_card(self.base_url, protocol_version="9.9")

    def test_submit_status_result_round_trip(self) -> None:
        card = self.v1.discover_card(self.base_url)
        task = self.v1.send_message(
            card, skill="a2a.test", text="hello v1", timeout=5.0
        )
        self.assertIn(task.state, ("SUBMITTED", "WORKING", "COMPLETED"))
        final = self.v1.wait_task(card, task.task_id, timeout=5.0)
        self.assertEqual(final.state, "COMPLETED")
        artifacts = self.v1.fetch_artifacts(card, task.task_id, timeout=5.0)
        self.assertTrue(artifacts)

    def test_unsupported_skill_rejected(self) -> None:
        card = self.v1.discover_card(self.base_url)
        with self.assertRaises(self.v1.V1UnsupportedOperation):
            self.v1.send_message(card, skill="nope.missing", text="x", timeout=5.0)

    def test_cancel_terminal_task_conflicts(self) -> None:
        card = self.v1.discover_card(self.base_url)
        task = self.v1.send_message(
            card, skill="a2a.test", text="fast", timeout=5.0
        )
        final = self.v1.wait_task(card, task.task_id, timeout=5.0)
        self.assertEqual(final.state, "COMPLETED")
        with self.assertRaises(self.v1.V1TaskNotCancelable):
            self.v1.cancel_task(card, task.task_id, timeout=5.0)

    def test_unknown_task_is_not_found(self) -> None:
        card = self.v1.discover_card(self.base_url)
        with self.assertRaises(self.v1.V1TaskNotFound):
            self.v1.get_task(card, "task_missing", timeout=5.0)

    def test_unreachable_peer_reported(self) -> None:
        dead_thread, dead_url = start_peer()
        stop_peer(dead_thread)
        with self.assertRaises(self.v1.V1PeerUnreachable):
            self.v1.discover_card(dead_url, timeout=2.0)

    def test_secret_payload_never_sent(self) -> None:
        card = self.v1.discover_card(self.base_url)
        with self.assertRaises(self.v1.V1Error):
            self.v1.send_message(
                card,
                skill="a2a.test",
                text="use api_key=super-secret-value",
                timeout=5.0,
            )

    def test_agentos_card_is_minimal_read_only(self) -> None:
        card = self.v1.agentos_card()
        self.assertEqual(card.protocol_version, "1.0")
        skill_ids = {s.skill_id for s in card.skills}
        self.assertNotIn("shell.run", skill_ids)
        self.assertNotIn("fs.write", skill_ids)
        for skill in card.skills:
            self.assertNotIn("secret", (skill.description + skill.skill_id).lower())


if __name__ == "__main__":
    unittest.main()
