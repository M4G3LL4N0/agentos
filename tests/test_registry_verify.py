"""Tests for registry discovery/selection and verification/recovery logic."""

import tempfile
import unittest
from pathlib import Path

from agentos.events import EventBus
from agentos.models import (
    EventType,
    Execution,
    ExecutionStatus,
    Objective,
    new_id,
)
from agentos.registry import CapabilityRegistry
from agentos.store import Store
from agentos.verify import (
    Verifier,
    backoff_delay_seconds,
    classify_failure,
    retryable,
)


class RegistryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "agentos.db")
        self.bus = EventBus(self.store)
        self.registry = CapabilityRegistry(self.store, self.bus)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()


class TestDiscovery(RegistryTestCase):
    def test_discover_registers_native_capabilities(self) -> None:
        capabilities = self.registry.discover()
        ids = {c.id for c in capabilities}
        self.assertEqual(
            ids,
            {
                "shell",
                "filesystem",
                "echo",
                "opencode",
                "github",
                "xai",
                "grok",
                "openclaw",
                "langgraph",
                "grokbot-office",
                "openhands",
                "hermes",
                "browser-harness",
            },
        )
        events = self.store.list_events()
        discovered = [e for e in events if e.event_type == EventType.CAPABILITY_DISCOVERED]
        self.assertEqual(len(discovered), 13)

    def test_discover_is_idempotent(self) -> None:
        self.registry.discover()
        self.registry.discover()
        self.assertEqual(len(self.store.list_capabilities()), 13)
        discovered = [
            e
            for e in self.store.list_events()
            if e.event_type == EventType.CAPABILITY_DISCOVERED
        ]
        self.assertEqual(len(discovered), 13)

    def test_refresh_probes_health(self) -> None:
        capabilities = self.registry.discover(refresh_health=True)
        health = {c.id: c.health.value for c in capabilities}
        self.assertEqual(health["shell"], "OK")
        self.assertEqual(health["filesystem"], "OK")
        self.assertEqual(health["echo"], "OK")
        for cap_id in ("opencode", "github", "xai", "grok", "openclaw", "langgraph"):
            self.assertIn(
                health[cap_id],
                ("AVAILABLE", "UNAVAILABLE", "AUTH_REQUIRED", "MISCONFIGURED", "DOWN", "UNKNOWN"),
            )


class TestSelection(RegistryTestCase):
    def test_select_by_operation(self) -> None:
        self.registry.discover()
        selected = self.registry.select("shell.run")
        assert selected is not None
        self.assertEqual(selected.id, "shell")
        selected = self.registry.select("fs.write")
        assert selected is not None
        self.assertEqual(selected.id, "filesystem")

    def test_select_unknown_operation_returns_none(self) -> None:
        self.registry.discover()
        self.assertIsNone(self.registry.select("teleport.run"))

    def test_down_capability_is_deprioritized(self) -> None:
        self.registry.discover()
        shell = self.store.get_capability("shell")
        assert shell is not None
        from agentos.models import HealthStatus

        shell.health = HealthStatus.DOWN
        self.store.save_capability(shell)
        # No other shell.run provider exists, so selection still returns it
        # (engine will surface adapter failure honestly) — but a healthy
        # alternative would win. Register one and check.
        self.registry.register_configured_cli("alt", "Alt", "true")
        alt = self.store.get_capability("alt")
        assert alt is not None
        alt.operations = ["shell.run"]
        alt.health = HealthStatus.OK
        self.store.save_capability(alt)
        selected = self.registry.select("shell.run")
        assert selected is not None
        self.assertEqual(selected.id, "alt")


class TestConfiguredCapabilities(RegistryTestCase):
    def test_register_and_remove_configured_cli(self) -> None:
        capability = self.registry.register_configured_cli(
            "lint", "Lint", "echo lint-ok", verify_expected="lint-ok"
        )
        self.assertEqual(capability.operations, ["lint.run"])
        self.assertEqual(capability.source, "configured")
        self.assertTrue(self.registry.remove("lint"))
        self.assertFalse(self.registry.remove("lint"))

    def test_adapter_resolves_for_configured(self) -> None:
        capability = self.registry.register_configured_cli("lint", "Lint", "true")
        adapter = self.registry.adapter_for(capability)
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.name, "shell")


class TestVerifier(unittest.TestCase):
    def setUp(self) -> None:
        self.verifier = Verifier()

    def _execution(
        self, output: dict, error: dict | None = None, operation: str = "shell.run"
    ) -> Execution:
        execution = Execution(
            id=new_id("exec"),
            objective_id="obj_1",
            capability_id="shell",
            operation=operation,
            status=ExecutionStatus.EXECUTED,
            output=output,
            error=error,
        )
        return execution

    def _objective(self, **kwargs) -> Objective:
        return Objective(id=new_id("obj"), title="t", description="d", **kwargs)

    def test_exit_code_zero_passes(self) -> None:
        verification = self.verifier.verify(
            self._objective(context={"verify": {"method": "exit_code"}}),
            self._execution({"exit_code": 0, "stdout": "ok"}),
        )
        self.assertTrue(verification.verified)
        self.assertEqual(verification.method, "exit_code")

    def test_exit_code_nonzero_fails(self) -> None:
        verification = self.verifier.verify(
            self._objective(context={"verify": {"method": "exit_code"}}),
            self._execution({"exit_code": 1, "stderr": "boom"}),
        )
        self.assertFalse(verification.verified)

    def test_adapter_error_never_verifies(self) -> None:
        verification = self.verifier.verify(
            self._objective(),
            self._execution(
                {"stdout": "looks fine"}, error={"message": "adapter blew up"}
            ),
        )
        self.assertFalse(verification.verified)

    def test_contains_check(self) -> None:
        objective = self._objective(constraints=["expected:hello"])
        passing = self.verifier.verify(
            objective, self._execution({"stdout": "well hello there"})
        )
        self.assertTrue(passing.verified)
        failing = self.verifier.verify(
            objective, self._execution({"stdout": "nothing relevant"})
        )
        self.assertFalse(failing.verified)

    def test_file_exists_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "proof.txt"
            target.write_text("proof", encoding="utf-8")
            objective = self._objective(
                context={"verify": {"method": "file_exists"}}
            )
            execution = self._execution(
                {"path": str(target)}, operation="fs.write"
            )
            verification = self.verifier.verify(objective, execution)
            self.assertTrue(verification.verified)
            target.unlink()
            verification2 = self.verifier.verify(objective, execution)
            self.assertFalse(verification2.verified)

    def test_structural_default_rejects_empty_output(self) -> None:
        verification = self.verifier.verify(self._objective(), self._execution({}))
        self.assertFalse(verification.verified)


class TestFailureClassification(unittest.TestCase):
    def test_classify(self) -> None:
        self.assertEqual(classify_failure("command timed out after 5s", None), "timeout")
        self.assertEqual(classify_failure("no such file: /x", None), "not_found")
        self.assertEqual(classify_failure("permission denied", None), "permission")
        self.assertEqual(classify_failure("exit_code=1: boom", 1), "exit_code")
        self.assertEqual(classify_failure(None, None), "unknown")

    def test_retryable(self) -> None:
        self.assertTrue(retryable("timeout"))
        self.assertTrue(retryable("exit_code"))
        self.assertFalse(retryable("permission"))
        self.assertFalse(retryable("auth"))
        self.assertFalse(retryable("not_found"))

    def test_backoff_grows_and_caps(self) -> None:
        self.assertEqual(backoff_delay_seconds(1), 1.0)
        self.assertEqual(backoff_delay_seconds(2), 2.0)
        self.assertEqual(backoff_delay_seconds(10), 30.0)


if __name__ == "__main__":
    unittest.main()