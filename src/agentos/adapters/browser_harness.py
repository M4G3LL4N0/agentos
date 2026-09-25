"""AgentOS browser-harness adapter (Part 2A).

Drives the local headless Chrome (CDP) through a single, purpose-built
Node driver (``browser_driver.js``) that ships with this package, so the
step never depends on a stray pip package or an npm install to run. Node
is the WebSocket substrate because AgentOS core is deliberately pure-Python
(spec: no third-party deps): the browser must be talked to over CDP WS, and
the Node runtime is already required by opencode and friends.

The harness is *honest about what it can do*: it can read, navigate, click,
type, fill forms, manage tabs, screenshot, download, and upload local files
inside the browser. It never reads cookies, session tokens, or anything
that would compromise an authenticated session, and it refuses destructive
or authenticated writes unless the caller supplied explicit approval
evidence (the same boundary the browser harness cell advertises).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agentos.adapters.base import AdapterResult, ExecutionRequest
from agentos.adapters.openclaw import (
    Evidence,
)
from agentos.adapters.proc import resolve_binary, run_command
from agentos.models import HealthStatus

BROWSER_HARNESS_OPERATION = "browser-harness.run"


def _driver_script() -> str | None:
    candidate = os.path.join(os.path.dirname(__file__), "browser_driver.js")
    if os.path.exists(candidate):
        return candidate
    return None


class BrowserHarnessAdapter:
    """One CDP operation executed against the local headless Chrome."""

    name = "browser-harness"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def driver_path(self) -> str | None:
        return _driver_script()

    def node_binary(self) -> str | None:
        return resolve_binary(
            self.config.get("node"),
            "AGENTOS_NODE_BIN",
            "node",
        )

    def probe(self) -> HealthStatus:
        chrome = os.environ.get("CHROME_EXECUTABLE")
        return HealthStatus.AVAILABLE if chrome else HealthStatus.UNKNOWN

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        if operation != BROWSER_HARNESS_OPERATION:
            return AdapterResult(
                ok=False,
                error=f"browser-harness adapter does not support operation {operation!r}",
                evidence=[
                    Evidence(
                        kind="unsupported_operation",
                        detail=f"browser-harness received {operation!r}",
                        source="browser-harness",
                    )
                ],
            )
        script = self.driver_path()
        if not script:
            return AdapterResult(
                ok=False,
                error="browser-harness driver script missing",
                evidence=[
                    Evidence(
                        kind="not_found",
                        detail="browser_driver.js not found in package",
                        source="browser-harness",
                    )
                ],
            )
        node = self.node_binary()
        if not node:
            return AdapterResult(
                ok=False,
                error="browser-harness requires node (set AGENTOS_NODE_BIN)",
                evidence=[
                    Evidence(
                        kind="not_found",
                        detail="node binary unavailable",
                        source="browser-harness",
                    )
                ],
            )
        payload = dict(request.params)
        debug_port = payload.pop("debugPort", None) or self.config.get("debug_port") or 9222
        cmd_payload = payload.pop("payload", payload)
        argv = [
            node,
            script,
            str(debug_port),
            str(payload.get("operation") or cmd_payload.get("operation") or "read"),
            json.dumps(cmd_payload or {}),
        ]
        result = run_command(argv, timeout=float(payload.get("timeout") or 60.0) + 5)
        if result.get("timed_out"):
            return AdapterResult(
                ok=False,
                error=f"browser-harness timed out after {payload.get('timeout', 60)}s",
                evidence=[
                    Evidence(
                        kind="timeout",
                        detail=str(result.get("error") or "cdp timeout"),
                        source="browser-harness",
                    )
                ],
            )
        try:
            parsed = json.loads(result.get("stdout") or "{}")
        except json.JSONDecodeError:
            parsed = {"ok": False, "error": str(result.get("stderr") or result.get("error"))}
        ok = bool(parsed.get("ok")) and parsed.get("result") is not None
        return AdapterResult(
            ok=ok,
            output=parsed.get("result") or {},
            exit_code=result.get("exit_code"),
            error=None if ok else str(parsed.get("error") or "browser-harness failed"),
            evidence=[
                Evidence(
                    kind=("cdp_ok" if ok else "cdp_error"),
                    detail=str(parsed.get("error") or "browser operation completed"),
                    source="browser-harness",
                )
            ],
        )


__all__ = ["BROWSER_HARNESS_OPERATION", "BrowserHarnessAdapter"]
