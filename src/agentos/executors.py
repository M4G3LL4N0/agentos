"""Executor capability catalog, matrix, and detection statuses (Part 2A).

This module is the canonical, machine-checkable description of which
executors exist, which capabilities they are allowed to claim, and how
their availability was measured. Nothing here is aspirational: health and
availability come from probes; a row is only printed if the executor was
actually detected or its probe ran.

Status vocabulary (used across this repo):

    AVAILABLE                    probe passed on this machine, usable now
    DETECTED_BUT_UNCONFIGURED    binary/present but runtime cannot be driven
    ADAPTER_ONLY                 code path exists, no live target configured
    UNAVAILABLE                  probe/detection found nothing usable
    REJECTED                     inspected and intentionally not integrated
    DEFERRED                     inspected, safe but not integrated yet
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# capability families
# ---------------------------------------------------------------------------

OPENCODE_FAMILY: dict[str, str] = {
    "code.inspect": "inspect repository / code / docs structure",
    "code.edit": "edit source files and apply requested changes",
    "code.test": "run and/or generate tests for code",
    "code.refactor": "refactor code while preserving behavior",
    "code.document": "write or update docs tied to code",
    "repo.audit": "audit a repository (structure, health, risks)",
    "repo.implement": "implement a feature across a repository",
    "local.command": "execute local commands inside the repository project",
}

OPENHANDS_FAMILY: dict[str, str] = {
    "code.autonomous": "longer autonomous coding task in an isolated workspace",
    "repo.multi_step": "multi-step repository engineering task",
    "repo.refactor": "isolated refactor of a repository",
    "isolated.workspace": "autonomous work in an isolated workspace",
}

HERMES_FAMILY: dict[str, str] = {
    "hermes.run": "single-turn agent prompt via Hermes CLI",
    "hermes.research": "low-cost research / exploration task",
    "hermes.delegate": "delegated routine agent work",
    "hermes.memory": "read from Hermes memory/skills surfaces",
    "hermes.skills": "read available Hermes skills",
    "hermes.web_task": "structured web/tool task",
}

OPENCLAW_FAMILY: dict[str, str] = {
    "openclaw.run": "one agent turn through OpenClaw",
}

BROWSER_FAMILY: dict[str, str] = {
    "browser.read": "read rendered page content (DOM text)",
    "browser.navigate": "navigate the local browser to a URL",
    "browser.click": "click an element by selector",
    "browser.type": "type text into a field (idempotent inputs only)",
    "browser.forms": "fill a set of form fields by selector",
    "browser.tabs": "list / open / close browser tabs",
    "browser.screenshot": "capture a page screenshot to a file",
    "browser.download": "download a resource to a local file",
    "browser.upload": "attach a local file to a file input",
    "browser.session.local": "keep / reuse a local automation session",
}


def capability_specs() -> dict[str, dict[str, str]]:
    """Merge families under their executor keys."""
    return {
        "opencode": OPENCODE_FAMILY,
        "openhands": OPENHANDS_FAMILY,
        "hermes": HERMES_FAMILY,
        "openclaw": OPENCLAW_FAMILY,
        "browser-harness": BROWSER_FAMILY,
    }


# Stability classes used to bound result caching (see results_cache.py).
STABILITY_CLASSES: dict[str, str] = {
    "code.inspect": "repo_facts",
    "code.edit": "deterministic_output",
    "code.test": "repo_facts",
    "code.refactor": "deterministic_output",
    "code.document": "unchanged_docs",
    "repo.audit": "repo_facts",
    "repo.implement": "deterministic_output",
    "local.command": "deterministic_output",
    "shell.run": "deterministic_output",
    "fs.read": "repo_facts",
    "fs.exists": "stable_probe",
    "fs.list": "stable_probe",
    "fs.write": "deterministic_output",
    "echo": "deterministic_output",
    "code.autonomous": "deterministic_output",
    "repo.multi_step": "deterministic_output",
    "repo.refactor": "deterministic_output",
    "isolated.workspace": "deterministic_output",
    "hermes.run": "deterministic_output",
    "hermes.research": "deterministic_output",
    "hermes.delegate": "deterministic_output",
}


def stability_class_for(operation: str) -> str:
    return STABILITY_CLASSES.get(str(operation), "dynamic")


# ---------------------------------------------------------------------------
# detection statuses (measured, never assumed)
# ---------------------------------------------------------------------------


@dataclass
class Detection:
    """Honest detection result for one executor integration."""

    status: str
    detail: str
    binary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "detail": self.detail,
            "binary": self.binary,
        }


# ---------------------------------------------------------------------------
# canonical capability matrix
# ---------------------------------------------------------------------------

# Dimension order is part of the contract (see `agentos federation matrix`).
MATRIX_DIMENSIONS: list[str] = [
    "executor",
    "capability",
    "qualityClass",
    "costClass",
    "scarcityClass",
    "latencyClass",
    "localOrRemote",
    "requiresMachineOnline",
    "persistentWhenLaptopOffline",
    "persistentBrowser",
    "authenticatedBrowser",
    "isolatedWorkspace",
    "dataClassesAllowed",
    "approvalBoundary",
    "availability",
    "health",
]

_DEFAULT_DIMS: dict[str, Any] = {
    "qualityClass": "verified",
    "costClass": "local",
    "scarcityClass": "abundant",
    "latencyClass": "interactive",
    "localOrRemote": "local",
    "requiresMachineOnline": True,
    "persistentWhenLaptopOffline": False,
    "persistentBrowser": False,
    "authenticatedBrowser": False,
    "isolatedWorkspace": False,
    "dataClassesAllowed": "PUBLIC,INTERNAL",
    "approvalBoundary": "none",
}


def executor_matrix(
    health: dict[str, str] | None = None,
    availability: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Canonical capability matrix rows for every executor.

    ``health`` / ``availability`` overrides come from live probes (services
    layer); every other dimension is the static description of the
    executor. Rows are honest about what the executor actually offers.
    """
    health_map = health or {}
    avail_map = availability or {}
    rows: list[dict[str, Any]] = []

    def _add(
        executor: str,
        capability: str | None,
        dims: dict[str, Any],
    ) -> None:
        row = dict(_DEFAULT_DIMS)
        row.update(dims)
        row["executor"] = executor
        row["capability"] = capability or ""
        row["health"] = health_map.get(executor, "UNKNOWN")
        row["availability"] = avail_map.get(
            executor, "UNAVAILABLE" if row["health"] in ("DOWN", "UNAVAILABLE") else "AVAILABLE"
        )
        rows.append(row)

    # local deterministic worker (T1): shell/fs/echo
    _add("local-worker", "deterministic.local", {
        "capability": "deterministic",
        "qualityClass": "deterministic",
        "costClass": "free",
        "latencyClass": "interactive",
        "localOrRemote": "local",
        "dataClassesAllowed": "PUBLIC,INTERNAL,CONFIDENTIAL,HIGHLY_SENSITIVE",
        "approvalBoundary": "policy-gated writes only",
        "isolatedWorkspace": False,
    })
    for op in ("shell.run", "fs.read", "fs.write", "fs.exists", "fs.list", "echo"):
        _add("local-worker", op, {
            "latencyClass": "interactive",
            "dataClassesAllowed": "PUBLIC,INTERNAL,CONFIDENTIAL,HIGHLY_SENSITIVE",
        })

    # opencode (T3 local agent, code work dominates)
    for op, desc in OPENCODE_FAMILY.items():
        _add("opencode", op, {
            "qualityClass": "agent_verified",
            "costClass": "free" if op in ("code.inspect", "repo.audit") else "per_run",
            "scarcityClass": "abundant",
            "latencyClass": "medium",
            "localOrRemote": "local",
            "isolatedWorkspace": False,
            "dataClassesAllowed": "PUBLIC,INTERNAL,CONFIDENTIAL",
            "approvalBoundary": "LIVE behind authorization; simulated default",
        })
    _add("opencode", "opencode.run", {
        "qualityClass": "agent_verified",
        "latencyClass": "medium",
        "approvalBoundary": "LIVE behind authorization; simulated default",
    })

    # openhands (T4, optional, isolated workspace when healthy)
    for op, desc in OPENHANDS_FAMILY.items():
        _add("openhands", op, {
            "qualityClass": "autonomous",
            "costClass": "per_run",
            "scarcityClass": "scarce",
            "latencyClass": "batch",
            "localOrRemote": "local" if False else "remote",
            "isolatedWorkspace": True,
            "dataClassesAllowed": "PUBLIC,INTERNAL,CONFIDENTIAL",
            "approvalBoundary": "premium-gated (approval required)",
        })

    # hermes (T4 worker, local orchestration + remote inference)
    for op, desc in HERMES_FAMILY.items():
        _add("hermes", op, {
            "qualityClass": "agent",
            "costClass": "per_run",
            "scarcityClass": "shareable",
            "latencyClass": "medium",
            "localOrRemote": "local",
            "dataClassesAllowed": "PUBLIC,INTERNAL,CONFIDENTIAL",
            "approvalBoundary": "premium-gated (approval required)",
        })

    # openclaw (T3 local agent)
    for op, desc in OPENCLAW_FAMILY.items():
        _add("openclaw", op, {
            "qualityClass": "agent",
            "latencyClass": "medium",
            "approvalBoundary": "LIVE behind authorization; simulated default",
        })

    # browser harness (T2 local authenticated-less browser)
    for op, desc in BROWSER_FAMILY.items():
        _add("browser-harness", op, {
            "qualityClass": "deterministic",
            "latencyClass": "interactive",
            "localOrRemote": "local",
            "persistentBrowser": True,
            "authenticatedBrowser": False,
            "dataClassesAllowed": "PUBLIC,INTERNAL,CONFIDENTIAL",
            "approvalBoundary": (
                "read/navigate broadly allowed; click/type/forms/upload "
                "are LIVE + approved; cookies/session tokens never read"
            ),
        })

    # grokbot-office (T5 remote persistent cloud computer)
    for op in ("grokbot-office.validate", "grokbot-office.route", "grokbot-office.mode",
               "grokbot-office.usage", "grokbot-office.roster", "grokbot-office.bridge-check"):
        _add("grokbot-office", op, {
            "qualityClass": "premium",
            "costClass": "scarce",
            "scarcityClass": "scarce",
            "latencyClass": "batch",
            "localOrRemote": "remote",
            "requiresMachineOnline": False,
            "persistentWhenLaptopOffline": True,
            "persistentBrowser": True,
            "authenticatedBrowser": True,
            "isolatedWorkspace": True,
            "dataClassesAllowed": "PUBLIC,INTERNAL,CONFIDENTIAL",
            "approvalBoundary": "premium-gated (approval required); read-only bridge",
        })
    return rows


def matrix_rows(
    health: dict[str, str] | None = None,
    availability: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Public wrapper used by services so the call surface is stable."""
    return executor_matrix(health=health, availability=availability)