"""Reusable workflow catalog (Mission §22: reuse, never re-plan).

Mirrors ``mcp_candidates`` (store.py:233) and the ``ecosystem_scout``
lifecycle seam exactly: REGISTRY -> VERIFIED -> USED_WIDELY -> RETIRED, where
reuse is the ONLY thing that can upgrade a workflow toward USED_WIDELY and
that upgrade additionally requires crossing a human-reviewable reuse floor.
Registration is inert metadata; nothing here executes, installs, or contacts
the network;
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from agentos.federation import contains_secret

STATES = (
    "REGISTERED",
    "VERIFIED",
    "USED_WIDELY",
    "RETIRED",
)

TERMINAL_STATES = ("RETIRED",)

_UPGRADE_FLOOR = 5


class WorkflowCatalogError(Exception):
    """The workflow catalog rejected the record (schema-invalid or secret)."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def workflow_id(name: str) -> str:
    return f"workflow:{name.strip().lower()}"


def _risk_bucket(permissions: list[str]) -> str:
    secret_like = [p for p in permissions if "secret" in p.lower() or "key" in p.lower()]
    if len(secret_like) >= 2:
        return "NORMAL"
    if len(secret_like) == 1:
        return "LOW"
    return "LOW"


def register(
    name: str,
    steps: list[str],
    source: str = "sheet",
    requested_permissions: list[str] | None = None,
    schedule_hint: str = "",
) -> dict[str, Any]:
    """Register one reusable workflow as inert REGISTERED metadata."""
    _validate(name, steps)
    permissions = sorted(set(str(p) for p in (requested_permissions or [])))
    body = json.dumps(
        {"name": name, "steps": steps, "requested_permissions": permissions},
        default=str,
    )
    if contains_secret(body):
        raise WorkflowCatalogError("workflow carries secret-looking values; refused")
    now = utc_now_iso()
    return {
        "id": workflow_id(name),
        "name": name.strip(),
        "steps": [str(s).strip() for s in steps],
        "source": source,
        "requested_permissions": permissions,
        "risk_bucket": _risk_bucket(permissions),
        "reuse_count": 0,
        "state": "REGISTERED",
        "created_at": now,
        "updated_at": now,
    }


def _validate(name: str, steps: list[str]) -> None:
    if not name or not name.strip():
        raise WorkflowCatalogError("workflow is missing 'name'")
    steps = [s for s in (steps or []) if str(s).strip()]
    if not steps:
        raise WorkflowCatalogError(
            f"workflow {name!r} has no steps; a catalog entry must be reusable"
        )


def transition(workflow: dict[str, Any], to_state: str) -> dict[str, Any]:
    """Record a legal catalog edge, rejecting anything else loudly."""
    current = str(workflow.get("state", ""))
    if to_state not in STATES:
        raise WorkflowCatalogError(f"unknown workflow state {to_state!r}")
    edges = {
        "REGISTERED": ("VERIFIED",),
        "VERIFIED": ("USED_WIDELY", "RETIRED"),
        "USED_WIDELY": ("RETIRED",),
        "RETIRED": (),
    }
    if to_state not in edges.get(current, ()):
        raise WorkflowCatalogError(
            f"illegal transition {current!r} -> {to_state!r}; "
            "reuse is the only legal upgrade path"
        )
    updated = dict(workflow)
    updated["state"] = to_state
    updated["updated_at"] = utc_now_iso()
    return updated


def record_use(workflow: dict[str, Any], found_useful: bool = True) -> dict[str, Any]:
    """Increment the honest reuse count (never auto-upgrades past the facts)."""
    updated = dict(workflow)
    updated["reuse_count"] = int(updated.get("reuse_count") or 0) + 1
    updated["found_useful_count"] = int(updated.get("found_useful_count") or 0) + (
        1 if found_useful else 0
    )
    updated["updated_at"] = utc_now_iso()
    if (
        updated["state"] == "VERIFIED"
        and updated["reuse_count"] >= _UPGRADE_FLOOR
        and updated.get("found_useful_count", 0) >= _UPGRADE_FLOOR
    ):
        updated = transition(updated, "USED_WIDELY")
    return updated


def certify_static(workflow: dict[str, Any]) -> dict[str, Any]:
    """Static certification: declared metadata only, zero execution.

    Mirrors ``ecosystem_scout.certify_static`` and appends the honest
    no-sandbox note so a certified workflow is never claimed to have been
    sandboxed.
    """
    findings: list[str] = []
    name = str(workflow.get("name") or "").strip()
    steps = workflow.get("steps") or []
    if not name or not steps:
        findings.append("workflow is missing name/steps (schema invalid)")
    permissions = workflow.get("requested_permissions") or []
    if len(permissions) > 16:
        findings.append(
            f"excessive requested permission surface: {len(permissions)}"
        )
    secrets = [p for p in permissions if "secret" in p.lower() or "key" in p.lower()]
    if len(secrets) >= 2:
        findings.append(
            f"declares {len(secrets)} secret-like permissions; "
            "credential-bearing workflows need per-secret review"
        )
    findings.append(
        "no live sandbox available in this build: side effects and timeout "
        "behavior unverified (static only)"
    )
    hard = [f for f in findings if "sandbox" not in f and "unverified" not in f]
    return {
        "passed": not hard,
        "findings": findings,
        "checked_at": utc_now_iso(),
        "scope": "static-metadata-only",
    }
