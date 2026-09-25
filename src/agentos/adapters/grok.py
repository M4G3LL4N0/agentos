"""Grok Build CLI adapter: single-turn headless execution via ``grok -p``.

Uses the supported interface verified against ``grok --help``::

    grok -p <PROMPT> [--cwd DIR] [--agent NAME] [--output-format FMT]

No private interfaces, no invented endpoints. Availability is detected
honestly (binary + ``--version``); model auth surfaces at execution time.
Permission modes are never escalated by this adapter: no bypass flags.
"""

from __future__ import annotations

from typing import Any

from agentos.adapters.base import AdapterResult, ExecutionRequest
from agentos.adapters.proc import resolve_binary, run_command, version_output
from agentos.models import Evidence, HealthStatus
from agentos.safety import gate_allows

GROK_OPERATION = "grok.run"


class GrokAdapter:
    """Runs single-turn Grok prompts through the Grok Build CLI."""

    name = "grok"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def binary(self) -> str | None:
        binary = self.config.get("binary")
        return resolve_binary(str(binary) if binary else None, "GROK_BIN", "grok")

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        allowed, reason = gate_allows(request.mode, request.authorization)
        if not allowed:
            return AdapterResult(
                ok=False,
                error=reason,
                evidence=[
                    Evidence(kind="policy_block", detail=reason, source="grok")
                ],
            )
        if operation != GROK_OPERATION:
            return AdapterResult(
                ok=False,
                error=f"grok adapter does not support operation {operation!r}",
                evidence=[
                    Evidence(
                        kind="unsupported_operation",
                        detail=f"grok adapter received {operation!r}",
                        source="grok",
                    )
                ],
            )
        binary = self.binary()
        if binary is None:
            return AdapterResult(
                ok=False,
                error="grok binary not found (set grok.binary or install the Grok CLI)",
                evidence=[
                    Evidence(
                        kind="not_found", detail="grok executable missing", source="grok"
                    )
                ],
            )
        params = request.params
        prompt = params.get("prompt") or params.get("message")
        if not prompt or not str(prompt).strip():
            return AdapterResult(
                ok=False,
                error="grok.run requires a 'prompt' param",
                evidence=[
                    Evidence(
                        kind="missing_input", detail="no prompt provided", source="grok"
                    )
                ],
            )
        argv = [binary, "-p", str(prompt)]
        cwd = params.get("cwd") or params.get("project") or params.get("dir")
        if cwd:
            argv += ["--cwd", str(cwd)]
        agent = params.get("agent") or self.config.get("agent")
        if agent:
            argv += ["--agent", str(agent)]
        output_format = params.get("output_format") or self.config.get("output_format")
        if output_format:
            argv += ["--output-format", str(output_format)]
        timeout = float(
            params.get("timeout_seconds")
            or params.get("timeout")
            or self.config.get("timeout_seconds", 600)
            or 600
        )
        result = run_command(argv, timeout=timeout, cwd=str(cwd) if cwd else None)
        if result.get("timed_out"):
            return AdapterResult(
                ok=False,
                output={"exit_code": None, "duration_ms": result["duration_ms"]},
                error=str(result.get("error")),
                evidence=[
                    Evidence(kind="timeout", detail=str(result.get("error")), source="grok")
                ],
            )
        if result.get("error"):
            return AdapterResult(
                ok=False,
                error=str(result.get("error")),
                evidence=[
                    Evidence(kind="not_found", detail=str(result.get("error")), source="grok")
                ],
            )
        text = (result["stdout"] or "").strip()
        output = {
            "text": text,
            "stderr": result["stderr"],
            "exit_code": result["exit_code"],
            "duration_ms": result["duration_ms"],
        }
        if result["exit_code"] == 0 and text:
            return AdapterResult(
                ok=True,
                output=output,
                exit_code=0,
                evidence=[
                    Evidence(
                        kind="grok_run",
                        detail=f"grok exited 0 in {result['duration_ms']}ms, {len(text)} chars",
                        source="grok",
                    )
                ],
            )
        return AdapterResult(
            ok=False,
            output=output,
            exit_code=result["exit_code"],
            error=(
                f"grok exit_code={result['exit_code']}: "
                f"{(result['stderr'] or text or '').strip()[:500]}"
            ),
            evidence=[
                Evidence(
                    kind="exit_code",
                    detail=f"grok exit code {result['exit_code']}",
                    source="grok",
                )
            ],
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