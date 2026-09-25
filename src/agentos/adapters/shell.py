"""Shell adapter: real local CLI execution via subprocess.

Supports a command string or an argument vector, working directory,
environment handling, timeouts, captured stdout/stderr/exit code, duration,
and cancellation via timeout-kill. Every request passes through the
execution policy first: blocked patterns and destructive operations are
refused with a structured error, never executed.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import time

from agentos.adapters.base import AdapterResult, ExecutionRequest
from agentos.models import Evidence
from agentos.policies import ExecutionPolicy, redact_secrets, scrub_env

SHELL_OPERATION = "shell.run"


class ShellAdapter:
    """Executes shell commands with policy enforcement and full telemetry."""

    name = "shell"

    def __init__(self, policy: ExecutionPolicy | None = None) -> None:
        self.policy = policy or ExecutionPolicy()

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        params = request.params
        command = params.get("command")
        argv = params.get("argv") or params.get("args")
        if argv and not command:
            try:
                command = shlex.join([str(a) for a in argv])
            except (TypeError, ValueError):
                return AdapterResult(
                    ok=False,
                    error="shell.run 'argv' must be a list of strings",
                    evidence=[
                        Evidence(
                            kind="invalid_input",
                            detail="argv is not a string list",
                            source="shell",
                        )
                    ],
                )
        use_shell = True
        if argv and command is None:
            use_shell = False
        if not command and not argv:
            if operation != SHELL_OPERATION:
                return AdapterResult(
                    ok=False,
                    error=f"shell adapter does not support operation {operation!r}",
                    evidence=[
                        Evidence(
                            kind="unsupported_operation",
                            detail=f"shell adapter received {operation!r} with no command",
                            source="shell",
                        )
                    ],
                )
            return AdapterResult(
                ok=False,
                error="shell.run requires a non-empty 'command' param or 'argv' list",
                evidence=[
                    Evidence(
                        kind="missing_input", detail="no command provided", source="shell"
                    )
                ],
            )

        operation_class = str(params.get("operation_class", "standard"))
        decision = self.policy.check_shell(
            command if command else shlex.join([str(a) for a in argv or []]),
            operation_class,
        )
        if not decision.allowed:
            return AdapterResult(
                ok=False,
                error="refused by execution policy: " + "; ".join(decision.reasons),
                evidence=[
                    Evidence(
                        kind="policy_denied",
                        detail="; ".join(decision.reasons),
                        source="shell",
                    )
                ],
            )
        if decision.requires_approval:
            return AdapterResult(
                ok=False,
                error=(
                    f"operation class {operation_class!r} requires human approval; "
                    "grant via policy approvals, then re-run"
                ),
                evidence=[
                    Evidence(
                        kind="approval_required",
                        detail=f"class={operation_class}",
                        source="shell",
                    )
                ],
            )

        cwd = params.get("cwd")
        timeout = float(
            params.get("timeout_seconds")
            or params.get("timeout")
            or request.timeout_seconds
        )
        env = dict(os.environ)
        for key, value in (params.get("env") or {}).items():
            env[str(key)] = str(value)
        scrubbed = scrub_env({k: env.get(k, "") for k in (params.get("env") or {})})

        start = time.monotonic()
        try:
            if use_shell:
                proc = subprocess.run(
                    str(command),
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=cwd or None,
                    env=env,
                )
            else:
                proc = subprocess.run(
                    [str(a) for a in (argv or [])],
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=cwd or None,
                    env=env,
                )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start) * 1000)
            return AdapterResult(
                ok=False,
                output={
                    "stdout": "",
                    "stderr": "",
                    "exit_code": None,
                    "duration_ms": duration_ms,
                },
                error=f"command timed out after {timeout:g}s (process killed)",
                evidence=[
                    Evidence(
                        kind="timeout",
                        detail=f"command exceeded {timeout:g}s timeout; killed after {duration_ms}ms",
                        source="shell",
                    )
                ],
            )
        except FileNotFoundError as exc:
            return AdapterResult(
                ok=False,
                error=f"shell execution failed: {exc}",
                evidence=[Evidence(kind="not_found", detail=str(exc), source="shell")],
            )
        except OSError as exc:
            return AdapterResult(
                ok=False,
                error=f"shell execution failed: {exc}",
                evidence=[Evidence(kind="os_error", detail=str(exc), source="shell")],
            )
        duration_ms = int((time.monotonic() - start) * 1000)
        output = {
            "stdout": proc.stdout,
            "stderr": redact_secrets(proc.stderr or ""),
            "exit_code": proc.returncode,
            "command": redact_secrets(str(command) if command else shlex.join([str(a) for a in (argv or [])])),
            "duration_ms": duration_ms,
            "env": scrubbed,
        }
        if proc.returncode == 0:
            return AdapterResult(
                ok=True,
                output=output,
                exit_code=proc.returncode,
                evidence=[
                    Evidence(
                        kind="exit_code",
                        detail=f"exit code 0 in {duration_ms}ms",
                        source="shell",
                    )
                ],
            )
        return AdapterResult(
            ok=False,
            output=output,
            exit_code=proc.returncode,
            error=f"exit_code={proc.returncode}: {(proc.stderr or '').strip()[:500]}",
            evidence=[
                Evidence(
                    kind="exit_code",
                    detail=f"exit code {proc.returncode} in {duration_ms}ms",
                    source="shell",
                )
            ],
        )

    def probe(self) -> bool:
        try:
            proc = subprocess.run(
                "true", shell=True, capture_output=True, timeout=10
            )
            return proc.returncode == 0
        except Exception:
            return False