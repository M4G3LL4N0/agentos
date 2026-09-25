"""OpenCode adapter: route software-implementation work to the OpenCode CLI.

Uses only supported non-interactive flags (verified against
``opencode run --help``)::

    opencode run --format json --dir <project> [--model p/m] [--agent a]
                 [--title t] [-f file...] [--session s] <message>

The adapter never hard-codes OpenCode into the core: OpenCode appears as a
registered capability (``opencode``, operation ``opencode.run``) and the
router selects it for implementation-class work. Availability is detected
honestly: missing binary -> UNAVAILABLE, broken binary -> MISCONFIGURED.
Model auth is verified at execution time (a failed run records the real
provider error, never a fabricated result).
"""

from __future__ import annotations

import json
from typing import Any

from agentos.adapters.base import AdapterResult, ExecutionRequest
from agentos.adapters.proc import resolve_binary, run_command, version_output
from agentos.models import Evidence, HealthStatus
from agentos.safety import gate_allows

OPENCODE_OPERATION = "opencode.run"


class OpenCodeAdapter:
    """Delegates implementation tasks to ``opencode run``."""

    name = "opencode"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def binary(self) -> str | None:
        binary = self.config.get("binary")
        return resolve_binary(
            str(binary) if binary else None, "OPENCODE_BIN", "opencode"
        )

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        allowed, reason = gate_allows(request.mode, request.authorization)
        if not allowed:
            return AdapterResult(
                ok=False,
                error=reason,
                evidence=[
                    Evidence(kind="policy_block", detail=reason, source="opencode")
                ],
            )
        if operation != OPENCODE_OPERATION:
            return AdapterResult(
                ok=False,
                error=f"opencode adapter does not support operation {operation!r}",
                evidence=[
                    Evidence(
                        kind="unsupported_operation",
                        detail=f"opencode adapter received {operation!r}",
                        source="opencode",
                    )
                ],
            )
        binary = self.binary()
        if binary is None:
            return AdapterResult(
                ok=False,
                error="opencode binary not found (set opencode.binary or install opencode)",
                evidence=[
                    Evidence(
                        kind="not_found",
                        detail="opencode executable missing",
                        source="opencode",
                    )
                ],
            )
        params = request.params
        prompt = params.get("prompt") or params.get("message")
        if not prompt or not str(prompt).strip():
            return AdapterResult(
                ok=False,
                error="opencode.run requires a 'prompt' param",
                evidence=[
                    Evidence(
                        kind="missing_input", detail="no prompt provided", source="opencode"
                    )
                ],
            )
        argv = [binary, "run", "--format", "json"]
        project = params.get("project") or params.get("dir") or params.get("cwd")
        if project:
            argv += ["--dir", str(project)]
        model = params.get("model") or self.config.get("model")
        if model:
            argv += ["--model", str(model)]
        agent = params.get("agent") or self.config.get("agent")
        if agent:
            argv += ["--agent", str(agent)]
        title = params.get("title")
        if title:
            argv += ["--title", str(title)]
        session = params.get("session")
        if session:
            argv += ["--session", str(session)]
        files = params.get("files") or params.get("file")
        if files:
            items = files if isinstance(files, list) else [files]
            for item in items:
                argv += ["-f", str(item)]
        argv.append(str(prompt))
        timeout = float(
            params.get("timeout_seconds")
            or params.get("timeout")
            or self.config.get("timeout_seconds", 600)
            or 600
        )
        result = run_command(argv, timeout=timeout, cwd=str(project) if project else None)
        if result.get("timed_out"):
            return AdapterResult(
                ok=False,
                output={
                    "exit_code": None,
                    "duration_ms": result["duration_ms"],
                    "prompt_chars": len(str(prompt)),
                },
                error=str(result.get("error")),
                evidence=[
                    Evidence(
                        kind="timeout",
                        detail=str(result.get("error")),
                        source="opencode",
                    )
                ],
            )
        if result.get("error"):
            return AdapterResult(
                ok=False,
                error=str(result.get("error")),
                evidence=[
                    Evidence(
                        kind="not_found", detail=str(result.get("error")), source="opencode"
                    )
                ],
            )
        text, events = self._parse_events(result["stdout"])
        output = {
            "stdout": result["stdout"],
            "stderr": result["stderr"],
            "exit_code": result["exit_code"],
            "duration_ms": result["duration_ms"],
            "text": text,
            "events": events,
            "prompt_chars": len(str(prompt)),
        }
        if result["exit_code"] == 0 and text.strip():
            output["_mode_used"] = request.mode.value
            return AdapterResult(
                ok=True,
                output=output,
                exit_code=0,
                evidence=[
                    Evidence(
                        kind="opencode_run",
                        detail=(
                            f"opencode exited 0 in {result['duration_ms']}ms, "
                            f"{len(text)} chars returned"
                        ),
                        source="opencode",
                    )
                ],
            )
        return AdapterResult(
            ok=False,
            output=output,
            exit_code=result["exit_code"],
            error=(
                f"opencode exit_code={result['exit_code']}: "
                f"{(result['stderr'] or text or '').strip()[:500]}"
            ),
            evidence=[
                Evidence(
                    kind="exit_code",
                    detail=f"opencode exit code {result['exit_code']}",
                    source="opencode",
                )
            ],
        )

    @staticmethod
    def _parse_events(stdout: str) -> tuple[str, int]:
        """Extract assistant text from ``--format json`` event stream."""
        texts: list[str] = []
        count = 0
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            count += 1
            texts.extend(OpenCodeAdapter._text_from_event(event))
        if texts:
            return "\n".join(texts), count
        return stdout or "", count

    @staticmethod
    def _text_from_event(event: Any) -> list[str]:
        found: list[str] = []
        if isinstance(event, dict):
            event_type = str(event.get("type", ""))
            part = event.get("part") or event.get("message") or {}
            if isinstance(part, dict):
                if part.get("type") in ("text",) and isinstance(part.get("text"), str):
                    found.append(part["text"])
                for key in ("text", "content", "output"):
                    value = part.get(key)
                    if isinstance(value, str) and event_type in (
                        "message.part.updated",
                        "text",
                        "assistant",
                    ):
                        found.append(value)
            for value in event.values():
                found.extend(OpenCodeAdapter._text_from_event(value))
        elif isinstance(event, list):
            for value in event:
                found.extend(OpenCodeAdapter._text_from_event(value))
        return found

    def probe(self) -> HealthStatus:
        from agentos.adapters.proc import binary_usable

        binary = self.binary()
        if not binary_usable(binary):
            return HealthStatus.UNAVAILABLE
        result = version_output(binary)
        if result.get("exit_code") == 0:
            return HealthStatus.AVAILABLE
        return HealthStatus.MISCONFIGURED