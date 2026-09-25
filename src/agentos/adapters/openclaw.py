"""OpenClaw adapter: OpenClaw as an execution substrate, not as AgentOS.

Uses the supported ``openclaw agent`` interface (verified against help)::

    openclaw agent --message <text> --json [--session-id ID] [--timeout SEC]
    openclaw agent --local --message <text> --json   (embedded, needs provider keys)

``--deliver`` is never set: AgentOS consumes the reply, it does not fan out
to chat channels. Availability is detected honestly: binary presence,
``--version``, and (for gateway mode) whether a turn can actually start —
reported at execution time, never fabricated.

Security note, enforced by documentation and capability metadata: multiple
Bots may share one underlying execution environment, so separate Bot
identities must NOT be treated as security isolation.
"""

from __future__ import annotations

import json
from typing import Any

from agentos.adapters.base import AdapterResult, ExecutionRequest
from agentos.adapters.proc import resolve_binary, run_command, version_output
from agentos.models import Evidence, HealthStatus
from agentos.safety import gate_allows

OPENCLAW_OPERATION = "openclaw.run"


class OpenClawAdapter:
    """Runs one agent turn through the OpenClaw CLI."""

    name = "openclaw"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def binary(self) -> str | None:
        binary = self.config.get("binary")
        return resolve_binary(str(binary) if binary else None, "OPENCLAW_BIN", "openclaw")

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        allowed, reason = gate_allows(request.mode, request.authorization)
        if not allowed:
            return AdapterResult(
                ok=False,
                error=reason,
                evidence=[
                    Evidence(kind="policy_block", detail=reason, source="openclaw")
                ],
            )
        if operation != OPENCLAW_OPERATION:
            return AdapterResult(
                ok=False,
                error=f"openclaw adapter does not support operation {operation!r}",
                evidence=[
                    Evidence(
                        kind="unsupported_operation",
                        detail=f"openclaw adapter received {operation!r}",
                        source="openclaw",
                    )
                ],
            )
        binary = self.binary()
        if binary is None:
            return AdapterResult(
                ok=False,
                error="openclaw binary not found (set openclaw.binary or install openclaw)",
                evidence=[
                    Evidence(
                        kind="not_found", detail="openclaw executable missing", source="openclaw"
                    )
                ],
            )
        params = request.params
        message = params.get("message") or params.get("prompt")
        if not message or not str(message).strip():
            return AdapterResult(
                ok=False,
                error="openclaw.run requires a 'message' param",
                evidence=[
                    Evidence(
                        kind="missing_input", detail="no message provided", source="openclaw"
                    )
                ],
            )
        argv = [binary, "agent", "--message", str(message), "--json"]
        if params.get("local") or self.config.get("local"):
            argv.append("--local")
        session_id = params.get("session_id") or self.config.get("session_id")
        if session_id:
            argv += ["--session-id", str(session_id)]
        agent = params.get("agent") or self.config.get("agent")
        if agent:
            argv += ["--agent", str(agent)]
        timeout = float(
            params.get("timeout_seconds")
            or params.get("timeout")
            or self.config.get("timeout_seconds", 600)
            or 600
        )
        argv += ["--timeout", str(int(timeout))]
        result = run_command(argv, timeout=timeout + 30)
        if result.get("timed_out"):
            return AdapterResult(
                ok=False,
                output={"exit_code": None, "duration_ms": result["duration_ms"]},
                error=str(result.get("error")),
                evidence=[
                    Evidence(kind="timeout", detail=str(result.get("error")), source="openclaw")
                ],
            )
        if result.get("error"):
            return AdapterResult(
                ok=False,
                error=str(result.get("error")),
                evidence=[
                    Evidence(kind="not_found", detail=str(result.get("error")), source="openclaw")
                ],
            )
        text = self._extract_text(result["stdout"])
        output = {
            "text": text,
            "raw": result["stdout"],
            "stderr": result["stderr"],
            "exit_code": result["exit_code"],
            "duration_ms": result["duration_ms"],
        }
        if result["exit_code"] == 0 and text.strip():
            return AdapterResult(
                ok=True,
                output=output,
                exit_code=0,
                evidence=[
                    Evidence(
                        kind="openclaw_run",
                        detail=f"openclaw turn ok in {result['duration_ms']}ms, {len(text)} chars",
                        source="openclaw",
                    )
                ],
            )
        return AdapterResult(
            ok=False,
            output=output,
            exit_code=result["exit_code"],
            error=(
                f"openclaw exit_code={result['exit_code']}: "
                f"{(result['stderr'] or text or '').strip()[:500]}"
            ),
            evidence=[
                Evidence(
                    kind="exit_code",
                    detail=f"openclaw exit code {result['exit_code']}",
                    source="openclaw",
                )
            ],
        )

    @staticmethod
    def _extract_text(stdout: str) -> str:
        try:
            payload = json.loads(stdout) if stdout.strip() else None
        except json.JSONDecodeError:
            return stdout or ""
        if isinstance(payload, dict):
            for key in ("reply", "response", "text", "output", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return json.dumps(payload)[:4000]
        return stdout or ""

    def probe(self) -> HealthStatus:
        from agentos.adapters.proc import binary_usable

        binary = self.binary()
        if not binary_usable(binary):
            return HealthStatus.UNAVAILABLE
        result = version_output(binary, args=["--version"])
        if result.get("exit_code") == 0:
            return HealthStatus.AVAILABLE
        return HealthStatus.MISCONFIGURED