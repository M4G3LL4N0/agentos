"""Ecosystem scout: provider-neutral candidate discovery + certification.

Mirrors the MCP candidate lifecycle seam (``mcp_discovery``) exactly so the
governor sees one consistent shape: DISCOVERED -> INSPECTED -> TESTING ->
TESTED -> APPROVED, with REJECTED/REVOKED as terminal refusals.

Discovery never equals trust. A scout source is an inert *metadata* record —
provider-neutral (works with a registry hill, a federation peer, a worker
report, or a checked-in metadata sheet) and executor-neutral (never a live
GrokBot, never a live proxy, never a downloaded binary). Nothing here
performs network I/O, installs, or executes; anything that would require a
live sandbox is reported as an honest UNKNOWN finding, never certified.

Self-certification is refused for anything carrying secret-looking values
(reuses the src/federation guard so the same contains_secret rules apply).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from agentos.federation import contains_secret

STATES = (
    "DISCOVERED",
    "INSPECTED",
    "TESTING",
    "TESTED",
    "APPROVED",
    "REJECTED",
    "REVOKED",
)

TERMINAL_STATES = ("APPROVED", "REJECTED", "REVOKED")

_MAX_PERMISSIONS = 16

# Legal lifecycle edges. Anything else is refused loudly; discovery never
# implies approval, and an approved record is inert metadata until a human
# explicitly enables its executor hook (mirroring the MCP DEFERRED transport).
_EDGES: dict[str, tuple[str, ...]] = {
    "DISCOVERED": ("INSPECTED", "REJECTED"),
    "INSPECTED": ("TESTING", "REJECTED"),
    "TESTING": ("TESTED", "REJECTED"),
    "TESTED": ("APPROVED", "REJECTED"),
    "APPROVED": ("REVOKED",),
    "REJECTED": (),
    "REVOKED": (),
}

_SOURCE_KINDS = ("registry", "federation", "worker", "sheet")


class EcosystemScoutError(Exception):
    """A scout source could not be ingested (malformed or secret-bearing)."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def candidate_id(category: str, name: str, version: str = "") -> str:
    """Stable candidate identifier: name@version within a source category."""
    return f"{name.strip()}@{version.strip() or 'unversioned'}"


def _risk_class(source: dict[str, Any], category: str) -> str:
    """Heuristic risk class from declared metadata only (never a live probe).

    Mirrors ``mcp_discovery._risk_class``: surfaces a number of secrets the
    source would request, and any remote transport it declares, as NORMAL or
    HIGH — but never fabricates absence. A source that declares nothing is
    LOW *only* because it declares nothing remotely reachable.
    """
    required_secrets = 0
    remote_transports = 0
    for package in source.get("packages") or []:
        if not isinstance(package, dict):
            continue
        transport = str((package.get("transport") or {}).get("type") or "").lower()
        if transport in ("streamable-http", "sse", "remote", "network"):
            remote_transports += 1
        for var in package.get("environmentVariables") or []:
            if isinstance(var, dict) and var.get("isSecret") and var.get("isRequired"):
                required_secrets += 1
    if required_secrets >= 2 or remote_transports >= 2:
        return "HIGH"
    if required_secrets == 1 or remote_transports:
        return "NORMAL"
    return "LOW"


def ingest_source(
    category: str,
    name: str,
    source: str,
    source_kind: str = "sheet",
    version: str = "unversioned",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize one declared ecosystem source into an inert DISCOVERED record.

    Provider-neutral by construction: works whether the source is a registry
    hill, a federation peer, a worker report, or a checked-in metadata sheet.
    The record carries declared metadata only — never a live endpoint url that
    would imply an executor, and never a secret.
    """
    if not name or not name.strip():
        raise EcosystemScoutError("ecosystem source is missing a name")
    if source_kind not in _SOURCE_KINDS:
        raise EcosystemScoutError(f"unknown source kind {source_kind!r}")
    if source_kind != "sheet" and not str(source or "").strip():
        raise EcosystemScoutError(
            f"source kind {source_kind!r} requires a non-empty source pointer"
        )
    metadata = metadata or {}
    blob = json.dumps(metadata, default=str)
    if contains_secret(blob):
        raise EcosystemScoutError(
            "ecosystem source carries secret-looking values; refused"
        )
    category = (category or "uncategorized").strip()
    now = utc_now_iso()
    return {
        "id": candidate_id(category, name, version),
        "category": category,
        "name": name.strip(),
        "version": str(version or "unversioned").strip(),
        "source": source_kind,
        "provenance": source_kind,
        "risk_class": _risk_class(metadata, category),
        "requested_permissions": sorted(
            str(p) for p in (metadata.get("requested_permissions") or [])
        ),
        "runtime_requirements": sorted(
            str(p) for p in (metadata.get("runtime_requirements") or [])
        ),
        "signature": {
            "source": source_kind,
            "note": "declared provenance only; not independently verified",
        },
        "test_result": None,
        "state": "DISCOVERED",
        "created_at": now,
        "updated_at": now,
    }


def transition(candidate: dict[str, Any], to_state: str) -> dict[str, Any]:
    """Move a candidate along a legal lifecycle edge (returns updated copy)."""
    current = str(candidate.get("state", ""))
    if to_state not in STATES:
        raise EcosystemScoutError(f"unknown candidate state {to_state!r}")
    if to_state not in _EDGES.get(current, ()):
        raise EcosystemScoutError(
            f"illegal transition {current!r} -> {to_state!r}; "
            "discovery never implies approval"
        )
    updated = dict(candidate)
    updated["state"] = to_state
    updated["updated_at"] = utc_now_iso()
    return updated


def certify_static(candidate: dict[str, Any]) -> dict[str, Any]:
    """Bounded static certification: metadata only, zero execution.

    Mirrors ``mcp_discovery.certify_static``: checks declared permission
    surface, discovery metadata shape, and warns (never passes) that no live
    sandbox exists in this build. Approving therefore never certifies network
    behavior, side effects, or timeout guarantees — those stay UNKNOWN.
    """
    findings: list[str] = []
    name = str(candidate.get("name") or "").strip()
    category = str(candidate.get("category") or "").strip()
    if not name or not category:
        findings.append("missing name/category: discovery schema invalid")
    permissions = candidate.get("requested_permissions") or []
    if len(permissions) > _MAX_PERMISSIONS:
        findings.append(
            f"excessive requested permission surface: {len(permissions)} "
            f"(limit {_MAX_PERMISSIONS})"
        )
    secrets = [
        p for p in permissions if "secret" in p.lower() or "key" in p.lower()
    ]
    if len(secrets) >= 2:
        findings.append(
            f"declares {len(secrets)} secret-like permissions; "
            "credential-bearing candidates need per-secret human review"
        )
    transports = candidate.get("runtime_requirements") or []
    if any(
        t in str(x).lower() for x in transports for t in ("http", "sse", "remote")
    ):
        findings.append(
            "declares remote/network runtime; actual network expectations "
            "unverified without a sandbox"
        )
    findings.append(
        "no live sandbox available in this build: behavior and side effects "
        "unverified (static metadata only)"
    )
    hard = [f for f in findings if "sandbox" not in f and "unverified" not in f]
    return {
        "passed": not hard,
        "findings": findings,
        "checked_at": utc_now_iso(),
        "scope": "static-metadata-only",
    }
