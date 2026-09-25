"""LangGraph boundary: reserved, honestly reported.

LangGraph is used only where stateful/complex orchestration materially
benefits — never for simple tasks. The package is not installed in this
environment and no model provider is configured, so this adapter reports
UNAVAILABLE with the concrete remediation. The router treats GRAPH as a
fallback to SEQUENTIAL until a usable graph capability exists. When the
package plus a provider become available, real delegation slots in here
without touching the core.
"""

from __future__ import annotations

import importlib.util
from typing import Any

from agentos.adapters.base import AdapterResult, ExecutionRequest
from agentos.models import Evidence, HealthStatus

GRAPH_OPERATION = "graph.run"


class LangGraphAdapter:
    """Boundary for future graph-based execution."""

    name = "langgraph"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    @staticmethod
    def installed() -> bool:
        return importlib.util.find_spec("langgraph") is not None

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        if not self.installed():
            return AdapterResult(
                ok=False,
                error=(
                    "langgraph is not installed in the AgentOS environment "
                    "(pip install langgraph) and no model provider is configured; "
                    "graph-based execution is unavailable"
                ),
                evidence=[
                    Evidence(
                        kind="unavailable",
                        detail="langgraph package missing",
                        source="langgraph",
                    )
                ],
            )
        return AdapterResult(
            ok=False,
            error="langgraph delegation is not wired yet (boundary reserved)",
            evidence=[
                Evidence(
                    kind="unavailable",
                    detail="graph delegation not implemented",
                    source="langgraph",
                )
            ],
        )

    def probe(self) -> HealthStatus:
        if self.installed():
            return HealthStatus.AVAILABLE
        return HealthStatus.UNAVAILABLE