"""Provider-neutral execution mode gate.

No adapter may perform LIVE execution without explicit authorization.
INSPECT/SIMULATED always pass. This module is provider-neutral: it never
references a specific provider, binary, or model — adapters consult it at
their execution boundary and the engine enforces it before dispatch.
"""

from __future__ import annotations

from agentos.adapters.base import ExecutionMode

LIVE_APPROVAL_EVENT = "authorization.live_granted"


def gate_allows(mode: ExecutionMode, authorization: list[dict] | None) -> tuple[bool, str]:
    """Return (allowed, reason). LIVE requires an explicit authorization
    carrying a 'live' or 'approved' grant; INSPECT/SIMULATED always pass."""
    if mode is not ExecutionMode.LIVE:
        return True, f"mode {mode.value} does not require authorization"
    granted = False
    for item in (authorization or []):
        tags = [str(item.get("kind", "")).lower(), str(item.get("grant", "")).lower()]
        if "live" in tags or "approved" in tags:
            granted = True
    if granted:
        return True, "LIVE authorized by approval evidence"
    return False, "LIVE execution requires explicit authorization; none granted"


def require_execution_authority(mode, auth) -> None:
    # Lazy import: agentos.engine imports this module, so importing it here
    # at module top would be circular.
    from agentos.engine import AgentOSError

    allowed, reason = gate_allows(mode, auth)
    if not allowed:
        raise AgentOSError(f"POLICY_BLOCK: {reason} ({mode.value})")
