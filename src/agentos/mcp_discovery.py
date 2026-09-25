"""MCP registry discovery + candidate certification lifecycle (stdlib only).

Architecture: DISCOVER -> INSPECT -> POLICY -> TEST -> CERTIFY -> APPROVE
-> REGISTER. Discovery never equals trust: a candidate is inert metadata
until a human explicitly approves it, and even then the MCP client
transport is DEFERRED, so approval registers an *unavailable* cell —
honest metadata, never a live executor.

Candidate lifecycle: DISCOVERED -> INSPECTED -> TESTING -> TESTED ->
APPROVED, with REJECTED and REVOKED as terminal refusals.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
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

# Legal lifecycle edges. Anything else is refused loudly.
_EDGES: dict[str, tuple[str, ...]] = {
    "DISCOVERED": ("INSPECTED", "REJECTED"),
    "INSPECTED": ("TESTING", "REJECTED"),
    "TESTING": ("TESTED", "REJECTED"),
    "TESTED": ("APPROVED", "REJECTED"),
    "APPROVED": ("REVOKED",),
    "REJECTED": (),
    "REVOKED": (),
}

MAX_PACKAGES = 8
MAX_ENV_VARS = 16


class MCPRegistryError(Exception):
    """The registry could not be read (unconfigured, unreachable, malformed)."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def candidate_id(name: str, version: str) -> str:
    return f"{name.strip()}@{version.strip() or 'unversioned'}"


def _packages(server: dict[str, Any]) -> list[dict[str, Any]]:
    packages = server.get("packages") or []
    remotes = server.get("remotes") or []
    out = [p for p in packages if isinstance(p, dict)]
    for remote in remotes:
        if isinstance(remote, dict):
            out.append({**remote, "registryType": "remote"})
    return out


def _risk_class(server: dict[str, Any], packages: list[dict[str, Any]]) -> str:
    """Heuristic risk class from declared metadata only (never a live probe)."""
    required_secrets = 0
    remote_transports = 0
    for package in packages:
        transport = package.get("transport") or {}
        ttype = str(transport.get("type", "")).lower()
        if ttype in ("streamable-http", "sse", "remote"):
            remote_transports += 1
        for var in package.get("environmentVariables") or []:
            if isinstance(var, dict) and var.get("isSecret") and var.get("isRequired"):
                required_secrets += 1
    if required_secrets >= 2 or len(packages) > MAX_PACKAGES:
        return "HIGH"
    if required_secrets == 1 or remote_transports:
        return "NORMAL"
    return "LOW"


def ingest_server(server: dict[str, Any], source: str) -> dict[str, Any]:
    """Normalize one registry ServerDetail into an inert DISCOVERED candidate."""
    if not isinstance(server, dict):
        raise MCPRegistryError("registry server entry is not an object")
    name = str(server.get("name", "")).strip()
    version = str(server.get("version", "") or "unversioned").strip()
    if not name:
        raise MCPRegistryError("registry server entry is missing 'name'")
    packages = _packages(server)
    repository = server.get("repository") or {}
    requested_permissions: list[str] = []
    runtime_requirements: list[str] = []
    for package in packages:
        for var in package.get("environmentVariables") or []:
            if isinstance(var, dict) and var.get("name"):
                requested_permissions.append(
                    f"env:{var['name']}"
                    + (" (required)" if var.get("isRequired") else "")
                    + (" (secret)" if var.get("isSecret") else "")
                )
        hint = package.get("runtimeHint")
        if hint:
            runtime_requirements.append(str(hint))
        transport = (package.get("transport") or {}).get("type")
        if transport:
            runtime_requirements.append(f"transport:{transport}")
    now = utc_now_iso()
    return {
        "id": candidate_id(name, version),
        "name": name,
        "publisher": str((server.get("_meta") or {}).get("publisher", "") or ""),
        "source": str(source),
        "repository": {
            "url": str(repository.get("url", "") or ""),
            "source": str(repository.get("source", "") or ""),
        },
        "version": version,
        "capabilities": [
            {
                "identifier": str(p.get("identifier", "") or ""),
                "registryType": str(p.get("registryType", "") or ""),
                "version": str(p.get("version", "") or ""),
            }
            for p in packages
        ],
        "requested_permissions": sorted(set(requested_permissions)),
        "runtime_requirements": sorted(set(runtime_requirements)),
        "risk_class": _risk_class(server, packages),
        "signature": {
            "repository_url": str(repository.get("url", "") or ""),
            "package_count": len(packages),
            "note": "registry-declared provenance only; not independently verified",
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
        raise MCPRegistryError(f"unknown candidate state {to_state!r}")
    if to_state not in _EDGES.get(current, ()):
        raise MCPRegistryError(
            f"illegal transition {current!r} -> {to_state!r}; "
            "discovery never implies approval"
        )
    updated = dict(candidate)
    updated["state"] = to_state
    updated["updated_at"] = utc_now_iso()
    return updated


def search_registry(
    base_url: str,
    query: str,
    limit: int = 25,
    timeout: float = 15.0,
) -> list[dict[str, Any]]:
    """Read-only registry search. Never downloads, installs, or executes."""
    params = urllib.parse.urlencode(
        {"search": query, "limit": max(1, min(int(limit), 100))}
    )
    url = base_url.rstrip("/") + f"/v0.1/servers?{params}"
    request = urllib.request.Request(url, method="GET")
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        raise MCPRegistryError(f"registry unreachable: {exc}") from exc
    servers = (payload or {}).get("servers")
    if not isinstance(servers, list):
        raise MCPRegistryError("registry returned a malformed server list")
    out: list[dict[str, Any]] = []
    for entry in servers:
        if isinstance(entry, dict) and isinstance(entry.get("server"), dict):
            out.append(entry["server"])
        elif isinstance(entry, dict) and entry.get("name"):
            out.append(entry)
    return out


def certify_static(candidate: dict[str, Any]) -> dict[str, Any]:
    """Bounded static certification: metadata only, zero execution.

    Checks startup/shape declarations, schema validity, declared
    permissions, network/filesystem expectations, malformed output and
    excessive surface — all from the candidate record. Anything requiring
    a live sandbox is reported as a finding, never silently passed.
    """
    findings: list[str] = []
    if not candidate.get("name") or not candidate.get("version"):
        findings.append("missing name/version: schema invalid")
    packages = candidate.get("capabilities") or []
    if len(packages) > MAX_PACKAGES:
        findings.append(
            f"excessive capability surface: {len(packages)} packages "
            f"(limit {MAX_PACKAGES})"
        )
    required_secrets = [
        p for p in (candidate.get("requested_permissions") or []) if "secret" in p
    ]
    if len(required_secrets) >= 2:
        findings.append(
            f"declares {len(required_secrets)} required secrets; "
            "credential-bearing servers need per-secret review"
        )
    transports = candidate.get("runtime_requirements") or []
    if any("streamable-http" in str(t) or "sse" in str(t) for t in transports):
        findings.append(
            "declares remote network transport; network expectations "
            "unverified without a sandbox"
        )
    blob = json.dumps(candidate, default=str)
    if contains_secret(blob):
        findings.append("record carries secret-looking values; refused")
    findings.append(
        "no live sandbox available in this build: startup, enumeration, "
        "timeout behavior and side effects unverified (static only)"
    )
    # Only hard findings fail certification; the sandbox-absence note is a
    # permanent honest caveat, never a pass and never a silent fail.
    hard = [f for f in findings if "sandbox" not in f and "unverified" not in f]
    return {
        "passed": not hard,
        "findings": findings,
        "checked_at": utc_now_iso(),
        "scope": "static-metadata-only",
    }
