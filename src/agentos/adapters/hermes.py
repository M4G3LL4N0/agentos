"""Hermes adapter: Hermes as an execution substrate, not as AgentOS.

Runs one agent turn through the Hermes CLI (``hermes run`` — a single
turn for a purpose, not a long-lived REPL). Availability is detected
honestly: binary presence, ``--version``, and, for gateway mode, whether
a turn can actually start — reported at execution time, never fabricated.

This adapter is the *gateway/session* contract for the hermes executor:
it never binds an interactive terminal, never keeps a chat loop alive, and
never escalates anything without the caller asking for it.
"""

from __future__ import annotations

import json
import os
from typing import Any

from agentos.adapters.base import AdapterResult, ExecutionRequest
from agentos.adapters.proc import resolve_binary, run_command, version_output
from agentos.models import Evidence, HealthStatus

HERMES_OPERATION = "hermes.run"


class HermesAdapter:
    """Runs exactly one Hermes turn. One request, one turn, honest result."""

    name = "hermes"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def binary(self) -> str | None:
        return resolve_binary(
            self.config.get("binary"),
            "HERMES_BIN",
            "hermes",
        )

    def probe(self) -> HealthStatus:
        from agentos.adapters.proc import binary_usable

        binary = self.binary()
        if not binary_usable(binary):
            return HealthStatus.UNAVAILABLE
        result = version_output(binary)
        if result.get("exit_code") == 0:
            return HealthStatus.AVAILABLE
        return HealthStatus.MISCONFIGURED

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        if operation != HERMES_OPERATION:
            return AdapterResult(
                ok=False,
                error=f"hermes adapter does not support operation {operation!r}",
                evidence=[
                    Evidence(
                        kind="unsupported_operation",
                        detail=f"hermes adapter received {operation!r}",
                        source="hermes",
                    )
                ],
            )
        binary = self.binary()
        if binary is None:
            return AdapterResult(
                ok=False,
                error="hermes binary not found (set HERMES_BIN or install hermes)",
                evidence=[
                    Evidence(
                        kind="not_found",
                        detail="hermes executable missing",
                        source="hermes",
                    )
                ],
            )
        params = request.params
        message = params.get("message") or params.get("prompt")
        if not message or not str(message).strip():
            return AdapterResult(
                ok=False,
                error="hermes.run requires a 'message' param",
                evidence=[
                    Evidence(
                        kind="missing_input",
                        detail="no message provided",
                        source="hermes",
                    )
                ],
            )
        cwd = params.get("cwd")
        timeout = float(
            params.get("timeout_seconds")
            or params.get("timeout")
            or self.config.get("timeout_seconds", 180)
            or 180
        )
        argv = ["hermes", "run", str(message)]
        if cwd:
            argv += ["--cwd", str(cwd)]
        result = run_command(argv, timeout=timeout + 30, cwd=cwd)
        if result.get("timed_out"):
            return AdapterResult(
                ok=False,
                output={
                    "exit_code": None,
                    "duration_ms": result["duration_ms"],
                },
                error=str(result.get("error")),
                evidence=[
                    Evidence(
                        kind="timeout",
                        detail=str(result.get("error")),
                        source="hermes",
                    )
                ],
            )
        if result.get("error") and result.get("exit_code") is None:
            return AdapterResult(
                ok=False,
                error=str(result.get("error")),
                evidence=[
                    Evidence(
                        kind="not_found",
                        detail=str(result.get("error")),
                        source="hermes",
                    )
                ],
            )
        text = (result.get("stdout") or "").strip()
        output = {
            "text": text,
            "raw_stdout": result.get("stdout"),
            "stderr": result.get("stderr"),
            "exit_code": result.get("exit_code"),
            "duration_ms": result.get("duration_ms"),
        }
        if result.get("exit_code") == 0 and text:
            return AdapterResult(
                ok=True,
                output=output,
                exit_code=0,
                evidence=[
                    Evidence(
                        kind="hermes_run",
                        detail=f"hermes turn ok in {result['duration_ms']}ms, {len(text)} chars",
                        source="hermes",
                    )
                ],
            )
        return AdapterResult(
            ok=False,
            output=output,
            exit_code=result.get("exit_code"),
            error=(
                f"hermes exit_code={result.get('exit_code')}: "
                f"{(result.get('stderr') or text or '').strip()[:500]}"
            ),
            evidence=[
                Evidence(
                    kind="exit_code",
                    detail=f"hermes exit code {result.get('exit_code')}",
                    source="hermes",
                )
            ],
        )


__all__ = ["HERMES_OPERATION", "HermesAdapter"]
