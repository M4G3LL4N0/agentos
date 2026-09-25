"""Honest seam tests: ecosystem-scout + reusable-workflow catalog.

These are the FIRST tests to ever pin these two seam modules on disk — and
they assert the lifecycle *refusals* as loudly as the legal edges, because a
scout that never refuses a secret-bearing source would be a liar, not a scout.
"""

from __future__ import annotations

import unittest

from agentos import ecosystem_scout
from agentos import reusable_workflows
from agentos import federation
from agentos.store import Store


class EcosystemSourceIngestTests(unittest.TestCase):
    def test_ingest_inert_discovered(self) -> None:
        candidate = ecosystem_scout.ingest_source(
            "registry",
            "neocortex",
            "hill.example.com/ecosystem/neocortex.json",
            version="0.3",
            metadata={"requested_permissions": ["read"], "runtime_requirements": ["http"]},
        )
        self.assertEqual(candidate["state"], "DISCOVERED")
        self.assertEqual(candidate["category"], "registry")
        self.assertIn(candidate["risk_class"], ("LOW", "NORMAL", "HIGH"))

    def test_ingest_refuses_secret_bearing_metadata(self) -> None:
        with self.assertRaises(ecosystem_scout.EcosystemScoutError):
            ecosystem_scout.ingest_source(
                "worker",
                "sneaky",
                "worker://vault/export",
                metadata={
                    "requested_permissions": ["vault-token"],
                },
            )

    def test_ingest_requires_category_and_name(self) -> None:
        with self.assertRaises(ecosystem_scout.EcosystemScoutError):
            ecosystem_scout.ingest_source("", "", "sheet://x.json")


class EcosystemLifecycleTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candidate = ecosystem_scout.ingest_source(
            "registry", "neocortex", "hill", version="0.3"
        )

    def test_legal_discovery_to_approval(self) -> None:
        state = self.candidate
        for expected in ("INSPECTED", "TESTING", "TESTED", "APPROVED"):
            state = ecosystem_scout.transition(state, expected)
            self.assertEqual(state["state"], expected)

    def test_illegal_discovered_to_approved_refused(self) -> None:
        with self.assertRaises(ecosystem_scout.EcosystemScoutError):
            ecosystem_scout.transition(self.candidate, "APPROVED")

    def test_rejected_is_terminal(self) -> None:
        rejected = ecosystem_scout.transition(self.candidate, "REJECTED")
        with self.assertRaises(ecosystem_scout.EcosystemScoutError):
            ecosystem_scout.transition(rejected, "INSPECTED")

    def test_certify_static_never_passes_without_sandbox(self) -> None:
        result = ecosystem_scout.certify_static(self.candidate)
        self.assertEqual(result["scope"], "static-metadata-only")
        self.assertTrue(
            any("no live sandbox available" in f for f in result["findings"])
        )
        self.assertTrue(
            any("sandbox" in f for f in result["findings"]),
            "static certification must always note the missing sandbox",
        )


class EcosystemStoreRoundTripTests(unittest.TestCase):
    def test_store_save_get_list(self) -> None:
        store = Store(":memory:")
        candidate = ecosystem_scout.ingest_source(
            "federation", "grok-worker", "peer://cells/a", version="1.0"
        )
        store.save_ecosystem_candidate(candidate)
        got = store.get_ecosystem_candidate(candidate["id"])
        self.assertEqual(got["id"], candidate["id"])
        self.assertEqual(got["state"], "DISCOVERED")
        self.assertEqual([c["id"] for c in store.list_ecosystem_candidates()], [candidate["id"]])


class WorkflowCatalogLifecycleTests(unittest.TestCase):
    def test_register_is_inert(self) -> None:
        workflow = reusable_workflows.register(
            "research-compile",
            steps=["collect", "normalize", "cross-check"],
            requested_permissions=[],
        )
        self.assertEqual(workflow["state"], "REGISTERED")
        self.assertEqual(workflow["reuse_count"], 0)

    def test_reuse_upgrades_only_after_floor(self) -> None:
        workflow = reusable_workflows.register(
            "research-compile",
            steps=["collect", "normalize", "cross-check"],
        )
        workflow = reusable_workflows.transition(workflow, "VERIFIED")
        for _ in range(2):
            workflow = reusable_workflows.record_use(workflow, found_useful=True)
        self.assertEqual(workflow["state"], "VERIFIED")
        for _ in range(3):
            workflow = reusable_workflows.record_use(workflow, found_useful=True)
        self.assertEqual(workflow["state"], "USED_WIDELY")

    def test_register_refuses_secret_permissions(self) -> None:
        with self.assertRaises(reusable_workflows.WorkflowCatalogError):
            reusable_workflows.register(
                "credential-copier",
                steps=["copy"],
                requested_permissions=["grok-token"],
            )

    def test_transition_rejects_unknown_state(self) -> None:
        workflow = reusable_workflows.register("a", steps=["b"])
        with self.assertRaises(reusable_workflows.WorkflowCatalogError):
            reusable_workflows.transition(workflow, "GLOWING")


class WorkflowStoreRoundTripTests(unittest.TestCase):
    def test_store_save_get_list(self) -> None:
        store = Store(":memory:")
        workflow = reusable_workflows.register("compile", steps=["a", "b"])
        store.save_workflow_catalog(workflow)
        got = store.get_workflow_catalog(workflow["id"])
        self.assertEqual(got["id"], workflow["id"])
        self.assertEqual([w["id"] for w in store.list_workflow_catalog()], [workflow["id"]])


if __name__ == "__main__":
    unittest.main()
