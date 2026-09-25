"""Read-only OrgOS + PAIOS bridges (no competing hierarchies).

Authority separation is structural:
- OrgOS owns roles/organization/responsibility. AgentOS only *reads* an
  operator-supplied JSON snapshot (path in config); without one the bridge
  reports UNCONFIGURED. Nothing is invented, nothing is written back.
- grokbot-office owns Grok-specific workforce definitions (never
  duplicated here; only cell metadata already held by the federation
  store is surfaced).
- PAIOS gets a machine-readable snapshot of live federation state
  (executors, cells, capabilities, jobs, health, usage/scarcity, routing
  decisions, A2A peers, MCP-approved tools, durable backend). No UI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def orgos_view(snapshot_path: str | None) -> dict[str, Any]:
    """Read an operator-supplied OrgOS snapshot file (JSON), or report
    UNCONFIGURED. The snapshot is validated structurally; malformed files
    fail loudly instead of yielding invented org data."""
    if not snapshot_path:
        return {
            "status": "UNCONFIGURED",
            "detail": "no OrgOS snapshot configured (config.orgos.snapshot_path)",
        }
    path = Path(snapshot_path).expanduser()
    if not path.is_file():
        return {
            "status": "UNCONFIGURED",
            "detail": f"OrgOS snapshot not found at {path}",
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"status": "UNAVAILABLE", "detail": f"unreadable snapshot: {exc}"}
    if not isinstance(data, dict):
        return {"status": "UNAVAILABLE", "detail": "snapshot is not an object"}
    view: dict[str, Any] = {"status": "OK", "source": str(path)}
    for key in (
        "organization",
        "roles",
        "participants",
        "capabilities",
        "teams",
        "missions",
        "policies",
    ):
        value = data.get(key)
        if isinstance(value, list):
            view[key] = {"count": len(value)}
        elif isinstance(value, dict):
            view[key] = {"present": True, "keys": sorted(value)[:12]}
        else:
            view[key] = {"present": False}
    return view


def paios_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Shape live federation state for PAIOS consumption (pure function)."""
    return {
        "schema": "agentos.federation-snapshot/1",
        "executors": state.get("executors", []),
        "cells": state.get("cells", []),
        "capabilities": state.get("capabilities", []),
        "jobs": state.get("jobs", []),
        "health": state.get("health", {}),
        "usage_scarcity": state.get("usage_scarcity", {}),
        "routing_decisions": state.get("routing_decisions", []),
        "a2a_peers": state.get("a2a_peers", []),
        "mcp_approved_tools": state.get("mcp_approved_tools", []),
        "durable_backend": state.get("durable_backend", {}),
    }
