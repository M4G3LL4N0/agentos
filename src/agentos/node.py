"""Machine (Mac) node state detection and routing gates (Part 2A).

The laptop is the *coordinating node* for the federation. Routing must
know whether the laptop is online so that local executors are only chosen
when they can actually reach their targets, and so that persistent remote
executors (GrokBot office) win when the laptop is offline.

Detection is a real socket scan against three well-known public endpoints.
There is deliberately no fake heartbeat: when detection cannot run, the
state is reported UNKNOWN and routing does not hard-gate on it, but flags
the uncertainty.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

PROBE_SPECS: list[tuple[str, int]] = [
    ("1.1.1.1", 443),
    ("8.8.8.8", 443),
    ("208.67.222.222", 53),
]


class NodeState(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"


def _endpoint_ok(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def detect_node_state(timeout: float = 2.0) -> NodeState:
    """Probe the three endpoints and classify connectivity.

    0 reachable  -> OFFLINE
    1 reachable  -> DEGRADED
    2+ reachable -> ONLINE
    """
    ok = sum(
        1
        for host, port in PROBE_SPECS
        if _endpoint_ok(host, port, timeout)
    )
    if ok == 0:
        return NodeState.OFFLINE
    if ok >= 2:
        return NodeState.ONLINE
    return NodeState.DEGRADED


@dataclass
class NodeStatus:
    state: NodeState = NodeState.UNKNOWN
    source: str = "config"  # config | manual | auto
    updated_at: str | None = None
    detail: str = "no override configured; treating as reachable"
    probes_scanned: int = 0
    probes_reachable: int = 0
    manual: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value if isinstance(self.state, NodeState) else str(self.state),
            "source": self.source,
            "updatedAt": self.updated_at,
            "detail": self.detail,
            "probesScanned": self.probes_scanned,
            "probesReachable": self.probes_reachable,
            "manual": self.manual,
        }


def node_status_from_config(config: dict[str, Any]) -> NodeStatus:
    """Derive node status from the ``node`` config block without probing."""
    block = config.get("node") or {}
    raw = str(block.get("state", "")).upper()
    try:
        state = NodeState(raw)
    except ValueError:
        state = NodeState.UNKNOWN
    source = str(block.get("source") or "config")
    detail = str(block.get("detail") or "from configuration")
    manual = source in ("manual", "cli", "config")
    return NodeStatus(
        state=state,
        source=source,
        updated_at=block.get("updatedAt"),
        detail=detail,
        manual=manual,
    )


def effective_node_state(config: dict[str, Any]) -> str:
    """State used by the routing gate.

    Manual overrides win. Absent a manual override we return UNKNOWN
    (treated as "assume reachable" so no local work is spuriously gated),
    with the uncertainty surfaced by ``node_status``.
    """
    return node_status_from_config(config).state.value


def probe_node_online(timeout: float = 2.0) -> NodeStatus:
    """Run a true scan and return status with probe counts."""
    status = NodeStatus(state=NodeState.UNKNOWN, source="auto")
    status.probes_scanned = len(PROBE_SPECS)
    status.probes_reachable = sum(
        1 for host, port in PROBE_SPECS if _endpoint_ok(host, port, timeout)
    )
    status.updated_at = __import__("agentos.models", fromlist=["utc_now_iso"]).utc_now_iso()
    n = status.probes_reachable
    if n == 0:
        status.state = NodeState.OFFLINE
        status.detail = "0/3 public endpoints reachable; laptop appears offline"
    elif n >= 2:
        status.state = NodeState.ONLINE
        status.detail = f"{n}/3 public endpoints reachable"
    else:
        status.state = NodeState.DEGRADED
        status.detail = f"{n}/3 public endpoints reachable; partial connectivity"
    return status


def node_gate(cell_state: dict[str, Any], node_state: str) -> tuple[bool, str]:
    """Apply the node gate to one executor cell.

    Returns ``(eligible, note)``. Local cells that ``requires_online`` are
    hard-gated when the node is OFFLINE. Persistent remote cells (GrokBot)
    remain eligible offline. DEGRADED and UNKNOWN states do not hard-gate
    but annotate the result honestly.
    """
    requires_online = bool(cell_state.get("requires_online", True))
    persistent_remote = bool(cell_state.get("persistent_remote", False))
    estado = str(node_state).upper()
    if persistent_remote:
        if estado == NodeState.UNKNOWN.value:
            return True, "persistent-remote executor; eligible under UNKNOWN node state"
        return True, "persistent-remote executor; eligible regardless of node state"
    if not requires_online:
        return True, "cell does not require the node to be online"
    if estado == NodeState.OFFLINE.value:
        return False, "node OFFLINE; local executor requires connectivity"
    if estado == NodeState.DEGRADED.value:
        return True, "node DEGRADED; execution may be slower or flaky"
    if estado == NodeState.UNKNOWN.value:
        return True, "node state UNKNOWN; assuming reachable (unverified)"
    return True, "node ONLINE"