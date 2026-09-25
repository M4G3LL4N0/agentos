"""Tests for capability readiness states + execution evidence ledger.

Task 3: capability health must be honest — DETECTED is never presented as
READY. Readiness is derived from probe outcome + binary executability, and
every execution attempt is persisted on the Capability ledger itself (single
source of truth; no parallel dataclass).

Fakes only: never touch real binaries, never require network, never LIVE.
"""

import tempfile
import unittest
from pathlib import Path

from agentos.events import EventBus
from agentos.models import (
    READINESS_STATES,
    Capability,
    CapabilityType,
    HealthStatus,
    ReadinessState,
    evolve_readiness,
    readiness,
)
from agentos.registry import CapabilityRegistry
from agentos.store import Store


class _ProbeDownAdapter:
    """Fake adapter whose probe always fails (no binary involved)."""

    name = "fake-down"

    def probe(self):
        return False

    def execute(self, operation, request):  # pragma: no cover - never called
        raise AssertionError("must not execute")


class _GhostBinaryAdapter:
    """Fake adapter that probes ok but names a binary that cannot exist."""

    name = "fake-ghost"

    def probe(self):
        return True

    def binary(self):
        return "/nonexistent/agentos-fake-binary-xyz"

    def execute(self, operation, request):  # pragma: no cover - never called
        raise AssertionError("must not execute")


def _capability(cap_id: str, adapter: str) -> Capability:
    return Capability(
        id=cap_id,
        name=cap_id,
        type=CapabilityType.TOOL,
        description="fake",
        operations=[f"{cap_id}.run"],
        adapter=adapter,
    )


class ReadinessTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "agentos.db")
        self.bus = EventBus(self.store)
        self.registry = CapabilityRegistry(self.store, self.bus)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()


class TestReadinessStates(ReadinessTestCase):
    def test_states_cover_honest_lifecycle(self) -> None:
        self.assertEqual(
            set(READINESS_STATES),
            {
                "UNKNOWN",
                "UNAVAILABLE",
                "DETECTED",
                "AVAILABLE",
                "CONFIGURATION_REQUIRED",
                "AUTH_REQUIRED",
                "CONFIGURED",
                "READY",
                "DEGRADED",
                "FAILED",
            },
        )
        self.assertEqual({s.value for s in ReadinessState}, set(READINESS_STATES))

    def test_probe_down_yields_unavailable(self) -> None:
        self.registry.register_adapter(_ProbeDownAdapter())
        capability = self.registry.register(_capability("down-cap", "fake-down"))
        health = self.registry.probe(capability)
        self.assertEqual(health, HealthStatus.DOWN)
        self.assertEqual(readiness(_ProbeDownAdapter(), health), "UNAVAILABLE")

    def test_probe_ok_but_missing_binary_yields_detected(self) -> None:
        self.registry.register_adapter(_GhostBinaryAdapter())
        capability = self.registry.register(_capability("ghost-cap", "fake-ghost"))
        health = self.registry.probe(capability)
        self.assertEqual(health, HealthStatus.OK)
        self.assertEqual(
            readiness(_GhostBinaryAdapter(), health), "DETECTED"
        )

    def test_never_ready_from_detection_alone(self) -> None:
        self.registry.register_adapter(_GhostBinaryAdapter())
        result = readiness(_GhostBinaryAdapter(), HealthStatus.AVAILABLE)
        self.assertNotEqual(result, "READY")
        result = readiness(_GhostBinaryAdapter(), HealthStatus.UNKNOWN)
        self.assertNotEqual(result, "READY")

    def test_evolve_detected_not_executable_needs_configuration(self) -> None:
        self.assertEqual(
            evolve_readiness("DETECTED", "not executable"), "CONFIGURATION_REQUIRED"
        )

    def test_evolve_executable_signal_promotes_to_ready(self) -> None:
        self.assertEqual(evolve_readiness("DETECTED", "binary executable"), "READY")
        self.assertEqual(evolve_readiness("AVAILABLE", "probe executable ok"), "READY")

    def test_evolve_never_ready_without_positive_signal(self) -> None:
        self.assertNotEqual(evolve_readiness("DETECTED", "seen on PATH"), "READY")
        self.assertNotEqual(evolve_readiness("UNKNOWN", "whatever"), "READY")

    def test_evolve_neutral_ok_substring_never_promotes_to_ready(self) -> None:
        # "ok" inside an unrelated word ("broken", "smoke") is not
        # executability evidence — must not invent READY.
        for signal in ("looks broken", "smoke"):
            self.assertNotEqual(evolve_readiness("DETECTED", signal), "READY")
            self.assertEqual(evolve_readiness("DETECTED", signal), "DETECTED")
            self.assertNotEqual(evolve_readiness("UNKNOWN", signal), "READY")

    def test_failed_recovers_through_degraded_not_single_leap(self) -> None:
        self.assertEqual(evolve_readiness("FAILED", "executed ok"), "DEGRADED")
        self.assertEqual(evolve_readiness("DEGRADED", "executed ok"), "READY")
        self.assertEqual(evolve_readiness("READY", "failed: boom"), "DEGRADED")
        self.assertEqual(evolve_readiness("DEGRADED", "failed: boom"), "FAILED")


class TestExecutionLedger(ReadinessTestCase):
    def test_record_success_updates_ledger(self) -> None:
        self.registry.discover()
        updated = self.registry.record_execution(
            "echo", True, None, duration_ms=12.5
        )
        self.assertEqual(updated.execution_count, 1)
        self.assertEqual(updated.success_count, 1)
        self.assertEqual(updated.failure_count, 0)
        self.assertIsNotNone(updated.last_success)
        persisted = self.store.get_capability("echo")
        assert persisted is not None
        self.assertEqual(persisted.execution_count, 1)
        self.assertEqual(persisted.success_count, 1)
        self.assertIsNotNone(persisted.last_success)

    def test_record_failure_updates_error_ledger(self) -> None:
        self.registry.discover()
        self.registry.record_execution("echo", True, None, duration_ms=12.5)
        updated = self.registry.record_execution(
            "echo", False, "boom", duration_ms=3.0
        )
        self.assertEqual(updated.execution_count, 2)
        self.assertEqual(updated.success_count, 1)
        self.assertEqual(updated.failure_count, 1)
        self.assertEqual(updated.last_error, "boom")
        self.assertIsNotNone(updated.last_failed)
        self.assertIsNotNone(updated.avg_duration_ms)

    def test_discover_refresh_sets_readiness(self) -> None:
        capabilities = self.registry.discover(refresh_health=True)
        by_id = {c.id: c for c in capabilities}
        # Local adapters probe ok and need no binary -> honest, usable, not READY.
        self.assertEqual(by_id["shell"].health, HealthStatus.OK)
        self.assertEqual(by_id["shell"].readiness, "AVAILABLE")
        self.assertIsNotNone(by_id["shell"].last_probe)
        for cap in capabilities:
            self.assertIn(cap.readiness, READINESS_STATES)

    def test_ledger_survives_dict_round_trip_and_old_dicts_load(self) -> None:
        self.registry.discover()
        self.registry.record_execution("echo", False, "boom", duration_ms=3.0)
        capability = self.store.get_capability("echo")
        assert capability is not None
        restored = Capability.from_dict(capability.to_dict())
        self.assertEqual(restored.execution_count, 1)
        self.assertEqual(restored.failure_count, 1)
        self.assertEqual(restored.last_error, "boom")
        old = {
            "id": "legacy",
            "name": "Legacy",
            "type": "tool",
            "description": "pre-ledger record",
        }
        loaded = Capability.from_dict(old)
        self.assertEqual(loaded.readiness, "UNKNOWN")
        self.assertEqual(loaded.execution_count, 0)
        self.assertIsNone(loaded.last_error)


class TestReadinessCLI(ReadinessTestCase):
    def test_doctor_includes_readiness(self) -> None:
        from agentos.services import AgentOS

        with AgentOS(home=str(Path(self.tmp.name) / "home")) as app:
            app.init()
            result = app.doctor()
            cap_checks = [
                c for c in result["checks"] if c["name"].startswith("capability:")
            ]
            self.assertTrue(cap_checks)
            for check in cap_checks:
                self.assertIn("readiness=", check["detail"])

    def test_capabilities_inspect_readiness_cli(self) -> None:
        import contextlib
        import io

        from agentos.cli import main as cli_main

        home = str(Path(self.tmp.name) / "home")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli_main(["--home", home, "init"]), 0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli_main(
                ["--home", home, "capabilities", "inspect", "--readiness"]
            )
        self.assertEqual(code, 0)
        self.assertIn("readiness", out.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
