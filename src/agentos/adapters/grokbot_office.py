"""GrokBot Office adapter: read-only bridge to the grokbot-office CLI.

Routes ``grokbot-office.*`` operations to *read-only* grokbot-office
commands (``tsx src/cli.ts <cmd>``). The bridge never mutates GrokBot
usage or materialization state: commands that set usage percentages, reset
dates, flip billing/harvest toggles, log usage events, materialize plans,
write bootstrap cards or push to the network are deliberately absent from
this adapter's code path.

Availability is probed honestly: missing repository, missing ``tsx``
runtime, or a failing ``validate`` maps to UNAVAILABLE / MISCONFIGURED —
never a fabricated healthy signal. ``probe()`` is the only command this
adapter runs implicitly; everything else executes only when a caller asks.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from agentos.adapters.base import (
    AdapterResult,
    ExecutionMode,
    ExecutionRequest,
)
from agentos.adapters.proc import run_command
from agentos.federation import contains_secret
from agentos.models import Evidence, HealthStatus
from agentos.safety import gate_allows

GROKBOT_OFFICE_OPERATION = "grokbot-office.run"

# Read-only CLI surface the bridge may invoke. Keyed by AgentOS operation.
# Every command here is verified read-only against src/cli.ts; anything that
# writes state (usage:set, usage:reset, usage:ondemand, usage:harvest,
# usage:log, materialize:*, bootstrap:set, webhook, handoff, create-card,
# bridge:bundle) is excluded on purpose.
READ_ONLY_COMMANDS: dict[str, str] = {
    "grokbot-office.validate": "validate",
    "grokbot-office.route": "route",
    "grokbot-office.should-create": "should-create",
    "grokbot-office.allowed": "allowed",
    "grokbot-office.mode": "mode",
    "grokbot-office.usage": "usage:report",
    "grokbot-office.roster": "roster",
    "grokbot-office.bridge-check": "bridge:check",
}

# Guard for tests and future review: these grokbot-office commands must never
# appear in any argv the adapter builds.
FORBIDDEN_GROKBOT_COMMANDS = (
    "usage:set",
    "usage:reset",
    "usage:ondemand",
    "usage:harvest",
    "usage:log",
    "materialize:plan",
    "materialize:set",
    "bootstrap:set",
    "webhook",
    "handoff",
    "create-card",
    "bridge:bundle",
)

_DEFAULT_REPO_CANDIDATES = (
    Path.home() / "grokbot-office",
)

_SUBJECT_OPERATIONS = {
    "grokbot-office.route",
    "grokbot-office.should-create",
    "grokbot-office.allowed",
}


class GrokBotOfficeAdapter:
    """Read-only execution channel to the grokbot-office control plane."""

    name = "grokbot-office"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    # ------------------------------------------------------------------
    # resolution
    # ------------------------------------------------------------------
    def root(self) -> Path | None:
        """Resolve the grokbot-office repository root, or None."""
        candidate = self.config.get("dir")
        if not candidate:
            candidate = os.environ.get("GROKBOT_OFFICE_DIR")
        if candidate:
            return Path(candidate).expanduser()
        for path in _DEFAULT_REPO_CANDIDATES:
            if self._looks_like_repo(path):
                return path
        return None

    def _looks_like_repo(self, root: Path) -> bool:
        return (
            root.is_dir()
            and (root / "package.json").is_file()
            and (root / "src" / "cli.ts").is_file()
            and (root / "registry" / "roles.yaml").is_file()
        )

    def launcher(self, root: Path | None) -> list[str] | None:
        """``[tsx, src/cli.ts]`` argv prefix, or None when runtime is absent."""
        if root is None:
            return None
        local = root / "node_modules" / ".bin" / "tsx"
        binary = str(local) if local.is_file() else shutil.which("tsx")
        if not binary:
            return None
        return [binary, "src/cli.ts"]

    # ------------------------------------------------------------------
    # probe (honest health)
    # ------------------------------------------------------------------
    def probe(self) -> HealthStatus:
        root = self.root()
        if root is None:
            return HealthStatus.UNAVAILABLE
        launcher = self.launcher(root)
        if launcher is None:
            return HealthStatus.UNAVAILABLE
        result = run_command([*launcher, "validate"], cwd=str(root), timeout=30.0)
        if result.get("timed_out"):
            return HealthStatus.UNAVAILABLE
        if result.get("error"):
            return HealthStatus.MISCONFIGURED
        if result["exit_code"] == 0:
            return HealthStatus.AVAILABLE
        return HealthStatus.MISCONFIGURED

    # ------------------------------------------------------------------
    # execute
    # ------------------------------------------------------------------
    def _argv(
        self, operation: str, root: Path, params: dict[str, Any]
    ) -> list[str]:
        launcher = self.launcher(root)
        if launcher is None:
            return []
        command = READ_ONLY_COMMANDS[operation]
        argv = [*launcher, command]
        if operation in _SUBJECT_OPERATIONS:
            subject = (
                params.get("subject")
                or params.get("task")
                or params.get("task_line")
                or params.get("taskline")
            )
            if subject:
                argv.append(str(subject).strip()[:2000])
        return argv

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        allowed, reason = gate_allows(request.mode, request.authorization)
        if not allowed:
            return AdapterResult(
                ok=False,
                error=reason,
                evidence=[
                    Evidence(
                        kind="policy_block", detail=reason, source=self.name
                    )
                ],
            )
        if operation not in READ_ONLY_COMMANDS:
            return AdapterResult(
                ok=False,
                error=f"grokbot-office adapter does not support operation {operation!r}",
                evidence=[
                    Evidence(
                        kind="unsupported_operation",
                        detail=f"grokbot-office received {operation!r}",
                        source=self.name,
                    )
                ],
            )
        command = READ_ONLY_COMMANDS[operation]
        if command in FORBIDDEN_GROKBOT_COMMANDS:
            return AdapterResult(
                ok=False,
                error=f"refusing forbidden grokbot-office command {command!r}",
                evidence=[
                    Evidence(
                        kind="policy_block",
                        detail=f"forbidden command {command!r} never executes",
                        source=self.name,
                    )
                ],
            )
        root = self.root()
        argv = self._argv(operation, root, request.params)
        if not argv:
            return AdapterResult(
                ok=False,
                error="grokbot-office repository or tsx runtime not found",
                evidence=[
                    Evidence(
                        kind="not_found",
                        detail="grokbot-office repo/runtime missing",
                        source=self.name,
                    )
                ],
            )
        if request.mode == ExecutionMode.SIMULATED:
            return AdapterResult(
                ok=True,
                output={
                    "simulated": True,
                    "operation": operation,
                    "command": " ".join(argv),
                    "read_only": True,
                },
                evidence=[
                    Evidence(
                        kind="bridge_dry_run",
                        detail=f"would run: {' '.join(argv)}",
                        source=self.name,
                    )
                ],
            )
        if request.mode == ExecutionMode.INSPECT:
            return AdapterResult(
                ok=True,
                output={
                    "inspect": argv,
                    "operation": operation,
                    "read_only": True,
                },
                evidence=[
                    Evidence(
                        kind="bridge_inspect",
                        detail=f"argv: {' '.join(argv)}",
                        source=self.name,
                    )
                ],
            )
        timeout = float(
            request.params.get("timeout_seconds")
            or self.config.get("timeout_seconds", 60)
            or 60
        )
        result = run_command(argv, timeout=timeout, cwd=str(root))
        combined = "{}\n{}".format(result.get("stdout") or "", result.get("stderr") or "")
        if result.get("timed_out"):
            return AdapterResult(
                ok=False,
                error=str(result.get("error") or "timed out"),
                evidence=[
                    Evidence(
                        kind="timeout",
                        detail=f"{command} timed out",
                        source=self.name,
                    )
                ],
            )
        output = {
            "stdout": result.get("stdout"),
            "stderr": result.get("stderr"),
            "exit_code": result["exit_code"],
            "duration_ms": result["duration_ms"],
            "command": command,
        }
        if result["exit_code"] != 0:
            return AdapterResult(
                ok=False,
                output=output,
                exit_code=result["exit_code"],
                error=(combined or "command failed").strip()[:1000],
                evidence=[
                    Evidence(
                        kind="exit_code",
                        detail=f"{command} exit code {result['exit_code']}",
                        source=self.name,
                    )
                ],
            )
        if contains_secret(combined):
            return AdapterResult(
                ok=False,
                output=output,
                error="grokbot-office output looks like a credential; not forwarded",
                evidence=[
                    Evidence(
                        kind="secret_guard",
                        detail="bridge output tripped the secret guard",
                        source=self.name,
                    )
                ],
            )
        return AdapterResult(
            ok=True,
            output=output,
            exit_code=0,
            evidence=[
                Evidence(
                    kind="bridge_ok",
                    detail=f"{command} exited 0 in {result['duration_ms']}ms",
                    source=self.name,
                )
            ],
        )


# ---------------------------------------------------------------------------
# Official usage-export parsing (data only, provider-specific → adapters/).
# These helpers map a GrokBot official usage export into the provider-neutral
# UsageSnapshot shape. Nothing here fabricates numbers or reset dates.
# ---------------------------------------------------------------------------


def parse_official_usage_export(data: dict[str, Any]) -> dict[str, Any]:
    """Map a GrokBot official usage export into an AgentOS usage snapshot.

    Expects the export inside ``data["usage"]`` (or the whole dict when it
    already carries ``usedPct``/``used``). Unknown fields are dropped;
    percentages are passed through unchanged for strict validation by
    ``resource_governor.parse_snapshot``. A missing reset is simply absent —
    never invented.
    """
    if not isinstance(data, dict):
        raise ValueError("official usage export must be a JSON object")
    usage = data.get("usage")
    if isinstance(usage, dict):
        root = usage
    else:
        root = data
    account = str(root.get("account") or root.get("accountCell") or "primary")
    used_pct = root.get("usedPct", root.get("used_percent", root.get("used")))
    resets_at = root.get("resetsAt", root.get("reset_at", root.get("resets")))
    if isinstance(resets_at, dict):
        resets_at = resets_at.get("date") or resets_at.get("at")
    return {
        "provider": "grokbot-office",
        "accountCell": account,
        "usedPct": used_pct,
        "resetsAt": resets_at,
        "source": "OFFICIAL_UI",
        "asOf": root.get(
            "asOf",
            root.get("generatedAt", root.get("updated_at")),
        ),
        "periodStart": root.get("periodStart", root.get("period_start")),
        "confidence": root.get("confidence"),
        "onDemandEnabled": root.get("onDemandEnabled"),
        "onDemandUsed": root.get("onDemandUsed"),
        "onDemandLimit": root.get("onDemandLimit"),
    }


def usage_from_official_export(data: dict[str, Any]) -> dict[str, Any]:
    """Shortcut: parse official export then run strict governor validation.

    Import errors surface as ``ValueError`` (never silently normalized).
    """
    from agentos.resource_governor import parse_snapshot

    mapped = parse_official_usage_export(data)
    snapshot = parse_snapshot(mapped)
    return snapshot.to_dict()