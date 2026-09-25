"""OpenHands adapter (Part 2A): one execute turn via the OpenHands CLI.

OpenHands is a general software agent runtime. AgentOS treats it as an
*execution substrate*, not as AgentOS itself: each call maps to a single
CLI run from source/venv, and every result is read back from the actual
stdout/exit code. Nothing is faked — if OpenHands is not installed or not
runnable, the adapter reports that honestly instead of pretending.

OpenHands is treated as a *general-purpose* executor that can do most
software-engineering-labour capability classes at moderate quality — it is
cheaper and more abundant than premium model executors, so the router
prefers it for bulk/refactor work while keeping premium cells for scarce
high-value synthesis.
"""

from __future__ import annotations

from typing import Any

from agentos.adapters.base import AdapterResult, ExecutionRequest
from agentos.adapters.proc import resolve_binary, run_command, version_output
from agentos.models import Evidence, HealthStatus

OPENHANDS_OPERATION = "openhands.run"


class OpenHandsAdapter:
    """OpenHands CLI adapter. One turn, one run, honest health."""

    name = "openhands"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    # ------------------------------------------------------------------
    # binary resolution
    # ------------------------------------------------------------------
    def binary(self) -> str | None:
        return resolve_binary(
            self.config.get("binary"),
            "OPENHANDS_BIN",
            "openhands",
        )

    def project_dir(self) -> str | None:
        return self.config.get("project") or self.config.get("cwd")

    def probe(self) -> HealthStatus:
        binary = self.binary()
        if not binary:
            return HealthStatus.UNAVAILABLE
        result = version_output(binary, args=["--version"])
        if result.get("exit_code") == 0:
            return HealthStatus.AVAILABLE
        return HealthStatus.MISCONFIGURED

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        # ------------------------------------------------------------------
        # gate: capability boundary
        # ------------------------------------------------------------------
        if operation != OPENHANDS_OPERATION:
            return AdapterResult(
                ok=False,
                error=f"openhands adapter does not support operation {operation!r}",
                evidence=[
                    Evidence(
                        kind="unsupported_operation",
                        detail=f"openhands adapter received {operation!r}",
                        source="openhands",
                    )
                ],
            )
        binary = self.binary()
        if binary is None:
            return AdapterResult(
                ok=False,
                error="openhands binary not found (set OPENHANDS_BIN or install openhands)",
                evidence=[
                    Evidence(
                        kind="not_found",
                        detail="openhands executable missing",
                        source="openhands",
                    )
                ],
            )
        params = request.params
        message = params.get("message") or params.get("prompt")
        if not message or not str(message).strip():
            return AdapterResult(
                ok=False,
                error="openhands.run requires a 'message' param",
                evidence=[
                    Evidence(
                        kind="missing_input",
                        detail="no message provided",
                        source="openhands",
                    )
                ],
            )
        cwd = self.project_dir() or params.get("cwd")
        timeout = float(
            params.get("timeout_seconds")
            or params.get("timeout")
            or self.config.get("timeout_seconds", 600)
            or 600
        )
        argv = ["openhands", "run", str(message)]
        if cwd:
            argv += ["--cwd", str(cwd)]
        result = run_command(argv, timeout=timeout + 30, cwd=cwd)
        if result.get("timed_out"):
            return AdapterResult(
                ok=False,
                output={
                    "exit_code": None,
                    "duration_ms": result["duration_ms"],
                    "stderr": result.get("stderr", ""),
                },
                error="openhands turn timed out",
                evidence=[
                    Evidence(
                        kind="timeout",
                        detail="openhands turn exceeded configured timeout",
                        source="openhands",
                    )
                ],
            )
        if result.get("error"):
            return AdapterResult(
                ok=False,
                error=str(result.get("error")),
                evidence=[
                    Evidence(
                        kind="not_found",
                        detail=str(result.get("error")),
                        source="openhands",
                    )
                ],
            )
        text = (result.get("stdout") or "").strip()
        output = {
            "text": text,
            "raw_stdout": result.get("stdout"),
            "stderr": result.get("stderr"),
            "exit_code": result.get("exit_code"),
            "duration_ms": result["duration_ms"],
        }
        if result.get("exit_code") == 0 and text:
            return AdapterResult(
                ok=True,
                output=output,
                exit_code=0,
                evidence=[
                    Evidence(
                        kind="openhands_run",
                        detail=(
                            f"openhands turn ok in {result['duration_ms']}ms, "
                            f"{len(text)} chars"
                        ),
                        source="openhands",
                    )
                ],
            )
        return AdapterResult(
            ok=False,
            output=output,
            exit_code=result.get("exit_code"),
            error=(
                f"openhands exit_code={result.get('exit_code')}: "
                f"{(result.get('stderr') or text or '').strip()[:500]}"
            ),
            evidence=[
                Evidence(
                    kind="exit_code",
                    detail=f"openhands exit code {result.get('exit_code')}",
                    source="openhands",
                )
            ],
        )


__all__ = ["OPENHANDS_OPERATION", "OpenHandsAdapter"]
