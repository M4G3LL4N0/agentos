"""Tests for the mode gate: no LIVE execution without explicit authorization.

SIMULATED/INSPECT always pass. LIVE requires an authorization carrying a
'live' or 'approved' grant. Fakes prove the adapter-level pattern; the
`agentos.safety` gate is the source of truth for LIVE authorization.
"""

import os
import stat
import tempfile
import unittest
from pathlib import Path

from agentos.adapters.base import AdapterResult, ExecutionMode, ExecutionRequest
from agentos.adapters.echo import ECHO_OPERATION, EchoAdapter
from agentos.adapters.grok import GROK_OPERATION, GrokAdapter
from agentos.adapters.opencode import OPENCODE_OPERATION, OpenCodeAdapter
from agentos.adapters.openclaw import OPENCLAW_OPERATION, OpenClawAdapter
from agentos.adapters.xai import CHAT_OPERATION, XAIAdapter
from agentos.engine import AgentOSError, Engine, EngineOptions
from agentos.events import EventBus
from agentos.models import Evidence, Objective, new_id
from agentos.registry import CapabilityRegistry
from agentos.safety import (
    LIVE_APPROVAL_EVENT,
    gate_allows,
    require_execution_authority,
)
from agentos.store import Store


def write_shim(directory: Path, name: str, script: str) -> str:
    path = directory / name
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class LiveOnlyFakeAdapter:
    """Fake that only implements LIVE; refuses other modes itself."""

    name = "live-only-fake"

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        if request.mode is not ExecutionMode.LIVE:
            reason = (
                f"fake does not support mode {request.mode.value}; only LIVE"
            )
            return AdapterResult(
                ok=False,
                error=reason,
                evidence=[
                    Evidence(
                        kind="unsupported_mode", detail=reason, source="live-only-fake"
                    )
                ],
            )
        return AdapterResult(
            ok=True,
            output={"fake": True},
            exit_code=0,
            evidence=[
                Evidence(kind="fake_run", detail="live-only fake ran", source="live-only-fake")
            ],
        )

    def probe(self) -> bool:
        return True


class SimulatedInspectFakeAdapter:
    """Fake supporting SIMULATED+INSPECT; non-LIVE never touches binary()."""

    name = "sim-inspect-fake"

    def __init__(self) -> None:
        self.binary_called = False

    def binary(self) -> str | None:
        self.binary_called = True
        return None

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        if request.mode is not ExecutionMode.LIVE:
            return AdapterResult(
                ok=True,
                output={"preview": True, "mode": request.mode.value},
                exit_code=0,
                evidence=[
                    Evidence(
                        kind="preview",
                        detail=f"non-live preview for mode {request.mode.value}",
                        source="sim-inspect-fake",
                    )
                ],
            )
        binary = self.binary()
        if binary is None:
            return AdapterResult(ok=False, error="no binary for LIVE")
        return AdapterResult(ok=True, output={"fake": True})

    def probe(self) -> bool:
        return True


class TestFakeAdapterModeRefusal(unittest.TestCase):
    def test_live_only_fake_refuses_simulated_and_inspect(self) -> None:
        adapter = LiveOnlyFakeAdapter()
        for mode in (ExecutionMode.SIMULATED, ExecutionMode.INSPECT):
            with self.subTest(mode=mode.value):
                result = adapter.execute(
                    "fake.op", ExecutionRequest(operation="fake.op", mode=mode)
                )
                self.assertFalse(result.ok)
                self.assertIn("mode", (result.error or "").lower())

    def test_inspect_does_not_invoke_binary_path(self) -> None:
        adapter = SimulatedInspectFakeAdapter()
        result = adapter.execute(
            "fake.op",
            ExecutionRequest(operation="fake.op", mode=ExecutionMode.INSPECT),
        )
        self.assertTrue(result.ok)
        self.assertFalse(adapter.binary_called)


class TestGateAllows(unittest.TestCase):
    def test_non_live_always_passes(self) -> None:
        for mode in (ExecutionMode.INSPECT, ExecutionMode.SIMULATED):
            for auth in (None, [], [{"kind": "anything"}]):
                with self.subTest(mode=mode.value, auth=auth):
                    allowed, _ = gate_allows(mode, auth)
                    self.assertTrue(allowed)

    def test_live_blocked_without_authorization(self) -> None:
        for auth in (None, [], [{"kind": "note", "grant": "read"}]):
            with self.subTest(auth=auth):
                allowed, reason = gate_allows(ExecutionMode.LIVE, auth)
                self.assertFalse(allowed)
                self.assertIn("authorization", reason.lower())

    def test_live_permitted_with_grant(self) -> None:
        for auth in (
            [{"kind": "authorization", "grant": "live"}],
            [{"kind": "approval", "grant": "approved"}],
            [{"kind": "authorization", "grant": "LIVE"}],
        ):
            with self.subTest(auth=auth):
                allowed, _ = gate_allows(ExecutionMode.LIVE, auth)
                self.assertTrue(allowed)

    def test_live_approval_event_constant(self) -> None:
        self.assertEqual(LIVE_APPROVAL_EVENT, "authorization.live_granted")


class TestRequireExecutionAuthority(unittest.TestCase):
    def test_unauthorized_live_raises_policy_block(self) -> None:
        # An objective whose authorization carries no live grant.
        authorization_without_approval: list[dict] = []
        evidence = [{"kind": "comment", "detail": "no approval recorded"}]
        for auth in (authorization_without_approval, evidence):
            with self.subTest(auth=auth):
                with self.assertRaises(AgentOSError) as ctx:
                    require_execution_authority(ExecutionMode.LIVE, auth)
                self.assertIn("POLICY_BLOCK", str(ctx.exception))

    def test_inspect_and_simulated_never_raise(self) -> None:
        for mode in (ExecutionMode.INSPECT, ExecutionMode.SIMULATED):
            for auth in (None, [], [{"kind": "note"}]):
                with self.subTest(mode=mode.value, auth=auth):
                    self.assertIsNone(require_execution_authority(mode, auth))


class TestRequestPlumbing(unittest.TestCase):
    def test_authorization_and_project_path_fields(self) -> None:
        request = ExecutionRequest(
            operation="echo",
            authorization=[{"kind": "approval", "grant": "live"}],
            project_path="/tmp/proj",
        )
        self.assertEqual(
            request.authorization, [{"kind": "approval", "grant": "live"}]
        )
        self.assertEqual(request.project_path, "/tmp/proj")
        defaulted = ExecutionRequest(operation="echo")
        self.assertEqual(defaulted.authorization, [])
        self.assertIsNone(defaulted.project_path)


class TestAdapterLiveGate(unittest.TestCase):
    def _cases(self):
        return [
            (EchoAdapter(), ECHO_OPERATION, {"a": 1}),
            (GrokAdapter(config={"binary": "/nonexistent/grok"}), GROK_OPERATION, {"prompt": "hi"}),
            (OpenClawAdapter(config={"binary": "/nonexistent/openclaw"}), OPENCLAW_OPERATION, {"message": "hi"}),
            (OpenCodeAdapter(config={"binary": "/nonexistent/opencode"}), OPENCODE_OPERATION, {"prompt": "hi"}),
            (XAIAdapter(config={"api_key": "test-key"}), CHAT_OPERATION, {"model": "m", "prompt": "hi"}),
        ]

    def test_unauthorized_live_blocked_before_dispatch(self) -> None:
        for adapter, operation, params in self._cases():
            with self.subTest(adapter=adapter.name):
                result = adapter.execute(
                    operation,
                    ExecutionRequest(
                        operation=operation, params=params, mode=ExecutionMode.LIVE
                    ),
                )
                self.assertFalse(result.ok)
                self.assertIn("authorization", (result.error or "").lower())
                self.assertTrue(
                    any(e.kind == "policy_block" for e in result.evidence)
                )

    def test_authorized_live_passes_gate_on_echo(self) -> None:
        result = EchoAdapter().execute(
            ECHO_OPERATION,
            ExecutionRequest(
                operation=ECHO_OPERATION,
                params={"a": 1},
                mode=ExecutionMode.LIVE,
                authorization=[{"kind": "approval", "grant": "live"}],
            ),
        )
        self.assertTrue(result.ok)


class TestOpenCodeModeRecord(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = Path(self.tmp.name) / "bin"
        self.bin.mkdir()
        self.old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.bin) + os.pathsep + self.old_path

    def tearDown(self) -> None:
        os.environ["PATH"] = self.old_path
        self.tmp.cleanup()

    def _shimmed(self) -> OpenCodeAdapter:
        write_shim(
            self.bin,
            "opencode",
            '#!/bin/sh\necho \'{"type":"message.part.updated","part":{"type":"text","text":"did the thing"}}\'\nexit 0\n',
        )
        return OpenCodeAdapter()

    def test_simulated_success_records_mode_used_without_auth(self) -> None:
        adapter = self._shimmed()
        result = adapter.execute(
            OPENCODE_OPERATION,
            ExecutionRequest(
                operation=OPENCODE_OPERATION,
                params={"prompt": "implement x"},
                mode=ExecutionMode.SIMULATED,
            ),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.output.get("_mode_used"), "simulated")

    def test_authorized_live_success_records_live_mode(self) -> None:
        adapter = self._shimmed()
        result = adapter.execute(
            OPENCODE_OPERATION,
            ExecutionRequest(
                operation=OPENCODE_OPERATION,
                params={"prompt": "implement x"},
                mode=ExecutionMode.LIVE,
                authorization=[{"kind": "approval", "grant": "live"}],
            ),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.output.get("_mode_used"), "live")


class TestEngineModeBoundary(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "agentos.db")
        self.bus = EventBus(self.store)
        self.registry = CapabilityRegistry(self.store, self.bus)
        self.engine = Engine(self.store, self.bus, self.registry)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def _objective(self) -> Objective:
        objective = Objective(
            id=new_id("obj"),
            title="echo test",
            description="d",
            context={"operation": "echo", "params": {"hello": "world"}},
        )
        self.store.save_objective(objective)
        return objective

    def test_unauthorized_live_never_reaches_adapter(self) -> None:
        objective = self._objective()
        with self.assertRaises(AgentOSError) as ctx:
            self.engine.run(
                objective.id,
                EngineOptions(mode=ExecutionMode.LIVE, authorization=[]),
            )
        self.assertIn("POLICY_BLOCK", str(ctx.exception))
        self.assertEqual(self.store.list_executions(objective.id), [])

    def test_simulated_run_dispatches(self) -> None:
        from agentos.models import ObjectiveState

        objective = self._objective()
        report = self.engine.run(
            objective.id, EngineOptions(mode=ExecutionMode.SIMULATED)
        )
        self.assertEqual(report.state, ObjectiveState.COMPLETED)


if __name__ == "__main__":
    unittest.main()
