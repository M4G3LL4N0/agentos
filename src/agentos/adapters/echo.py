"""Echo adapter: returns inputs unchanged. Used by tests and as a demo seam."""

from __future__ import annotations

from agentos.adapters.base import AdapterResult, ExecutionRequest
from agentos.models import Evidence
from agentos.safety import gate_allows

ECHO_OPERATION = "echo"


class EchoAdapter:
    """Echoes the request payload back. No external effects."""

    name = "echo"

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        allowed, reason = gate_allows(request.mode, request.authorization)
        if not allowed:
            return AdapterResult(
                ok=False,
                error=reason,
                evidence=[
                    Evidence(kind="policy_block", detail=reason, source="echo")
                ],
            )
        if operation != ECHO_OPERATION:
            return AdapterResult(
                ok=False,
                error=f"echo adapter does not support operation {operation!r}",
                evidence=[
                    Evidence(
                        kind="unsupported_operation",
                        detail=f"echo adapter received {operation!r}",
                        source="echo",
                    )
                ],
            )
        return AdapterResult(
            ok=True,
            output={"echo": dict(request.params)},
            exit_code=0,
            evidence=[
                Evidence(
                    kind="echoed",
                    detail="echo adapter returned the input payload",
                    source="echo",
                )
            ],
        )

    def probe(self) -> bool:
        return True