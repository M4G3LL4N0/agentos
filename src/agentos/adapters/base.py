"""Provider-neutral adapter boundary.

AgentOS Core -> Capability Adapter -> Provider / Tool / Agent

The core never contains provider-specific logic. An adapter translates an
AgentOS operation request into provider-specific execution and returns a
structured AdapterResult. Future adapters (Grok/xAI, OpenCode, OpenClaw,
LangGraph, MCP, GitHub, browsers, ...) implement this same protocol without
any change to the orchestration core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from agentos.models import Evidence


class ExecutionMode(StrEnum):
    """Execution mode for agent capabilities."""
    INSPECT = "inspect"      # Dry-run: show what would be done without executing
    SIMULATED = "simulated"  # Run with mocked/fake responses (no API calls)
    LIVE = "live"            # Actual execution with real API calls


@dataclass
class ExecutionRequest:
    """What the engine hands to an adapter."""

    operation: str
    params: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 120.0
    # SIMULATED is the safe default: nothing performs LIVE execution unless
    # a caller explicitly opts in (and supplies authorization, see safety.py).
    mode: ExecutionMode = ExecutionMode.SIMULATED
    authorization: list[dict] = field(default_factory=list)
    project_path: str | None = None


@dataclass
class AdapterResult:
    """Structured outcome returned by every adapter."""

    ok: bool
    output: dict[str, Any] = field(default_factory=dict)
    exit_code: int | None = None
    error: str | None = None
    evidence: list[Evidence] = field(default_factory=list)


class Adapter(Protocol):
    """The protocol every capability adapter must satisfy."""

    name: str

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        """Execute one operation. Must not raise on provider failure:
        return ``ok=False`` with ``error`` instead."""
        ...

    def probe(self) -> bool:
        """Lightweight availability check. Returns True when usable."""
        ...


def text_of(output: dict[str, Any]) -> str:
    """Best-effort textual rendering of an adapter output dict."""
    for key in ("stdout", "output", "text", "content"):
        value = output.get(key)
        if isinstance(value, str):
            return value
    parts = [f"{k}={v}" for k, v in output.items()]
    return " ".join(parts)