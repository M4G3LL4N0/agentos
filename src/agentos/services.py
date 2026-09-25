"""AgentOS service layer: the single shared core.

The CLI (and, later, a lightweight API and MCP tools) call these services.
Business logic lives here and in the engine — never duplicated in the CLI.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from agentos.adapters import text_of
from agentos.adapters.base import ExecutionMode, ExecutionRequest
from agentos.adapters.grokbot_office import READ_ONLY_COMMANDS
from agentos.engine import AgentOSError, Engine, EngineOptions
from agentos.events import EventBus
from agentos.federation import (
    DataClass,
    ExecutionClass,
    JobEnvelope,
    JobStatus,
    LatencyClass,
    QualityFloor,
    ResultEnvelope,
    ResultStatus,
    export_telemetry,
    make_executor_cell,
    plan_before_premium,
    resolve_route,
    tier_name,
    tier_premium,
    usage_band,
)
from agentos.models import (
    Capability,
    CapabilityType,
    EventType,
    Execution,
    HealthStatus,
    IncidentRecord,
    LessonCandidate,
    LessonStatus,
    LowUsageMetrics,
    Objective,
    ObjectiveState,
    Priority,
    Report,
    SkillDefinition,
    VerificationOutcome,
    VerificationRecord,
    VerificationResult,
    VerificationStatus,
    WorkflowDefinition,
    WorkflowMaturity,
    WorkflowStatus,
    new_id,
    readiness,
    utc_now_iso,
)
from agentos.policies import ExecutionPolicy
from agentos.registry import CapabilityRegistry
from agentos.store import Store

DEFAULT_HOME_NAME = ".agentos"
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_TIMEOUT_SECONDS = 120.0
HUMAN_ATTENTION_REASONS = (
    "AUTHORIZATION",
    "IRREVERSIBLE_ACTION",
    "SECURITY",
    "GENUINELY_MISSING_INFORMATION",
    "POLICY",
    "CONSEQUENTIAL_JUDGMENT",
)


def default_home() -> Path:
    import os

    override = os.environ.get("AGENTOS_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_HOME_NAME


class AgentOS:
    """Facade over store, registry, engine and events."""

    def __init__(self, home: str | Path | None = None) -> None:
        self.home = Path(home).expanduser() if home else default_home()
        self.db_path = self.home / "agentos.db"
        self.config_path = self.home / "config.json"
        self.config = self._load_config()
        self.policy = ExecutionPolicy.from_dict(self.config.get("policy"))
        self.store = Store(self.db_path)
        self.bus = EventBus(self.store)
        self._wire_components()

    def _wire_components(self) -> None:
        from agentos.skill_model import SkillModel
        from agentos.role_classifier import RoleClassifier
        from agentos.autonomy_model import AutonomyModel
        from agentos.backup_reconstruction import BackupManager
        from agentos.persistent_bot_governor import PersistentBotGovernor

        self.registry = CapabilityRegistry(
            self.store, self.bus, config=self.config, policy=self.policy
        )
        self.engine = Engine(self.store, self.bus, self.registry)
        self.verification_engine = self.engine.verification_records
        self.coach = self.engine.coach
        self.workflow_compiler = self.engine.workflow_compiler
        self.skill_model = SkillModel(self.store)
        self.role_classifier = RoleClassifier(self.store)
        self.autonomy_model = AutonomyModel(self.store)
        self.fleet_monitor = self.engine.fleet_monitor
        self.learned_router = self.engine.learned_router
        self.routine_engine = self.engine.routine_engine
        self.backup_manager = BackupManager(self.store)
        self.bot_governor = PersistentBotGovernor(self.store)
        self.bus.subscribe(EventType.EXECUTION_FAILED, self.fleet_monitor.handle_event)
        self.bus.subscribe(EventType.OBJECTIVE_FAILED, self.fleet_monitor.handle_event)
        self.bus.subscribe(EventType.RECOVERY_STARTED, self.fleet_monitor.handle_event)
        self.bus.subscribe(EventType.OBJECTIVE_COMPLETED, self.routine_engine.handle_event)

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def init(self, reset: bool = False) -> dict[str, Any]:
        """Initialize home dir, config, DB and native capabilities."""
        if reset and self.db_path.exists():
            self.store.close()
            self.db_path.unlink()
            self.store = Store(self.db_path)
        self.home.mkdir(parents=True, exist_ok=True)
        self.config = self._load_config()
        if "policy" not in self.config:
            self.config["policy"] = ExecutionPolicy().to_dict()
        self._save_config()
        self.policy = ExecutionPolicy.from_dict(self.config.get("policy"))
        self.bus = EventBus(self.store)
        self._wire_components()
        capabilities = self.registry.discover(refresh_health=True)
        self.bus.emit(EventType.OBJECTIVE_READY, payload={"init": True})
        return {
            "home": str(self.home),
            "db": str(self.db_path),
            "capabilities": [c.id for c in capabilities],
            "config": self.config,
        }

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "AgentOS":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # config
    # ------------------------------------------------------------------
    def _load_config(self) -> dict[str, Any]:
        config = {
            "max_attempts": DEFAULT_MAX_ATTEMPTS,
            "default_timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        }
        if self.config_path.exists():
            try:
                stored = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(stored, dict):
                    config.update(stored)
            except (json.JSONDecodeError, OSError):
                pass
        return config

    def _usage_now(self) -> Any:
        """Reference clock for usage freshness.

        Config ``usage.now`` (an ISO-8601 string) pins a deterministic
        reference time so integration fixtures stay stable; absent it,
        real wall-clock UTC is used. Never fabricates usage.
        """
        pinned = (self.config.get("usage") or {}).get("now")
        return pinned if pinned else utc_now_iso()

    def _save_config(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(self.config, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    # status / health
    # ------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        counts = self.store.counts()
        by_state: dict[str, int] = {}
        for objective in self.store.list_objectives():
            by_state[objective.status.value] = (
                by_state.get(objective.status.value, 0) + 1
            )
        capabilities = self.store.list_capabilities()
        healthy = sum(
            1
            for c in capabilities
            if c.health in (HealthStatus.OK, HealthStatus.AVAILABLE)
        )
        return {
            "home": str(self.home),
            "db": str(self.db_path),
            "counts": counts,
            "objectives_by_state": by_state,
            "capabilities": len(capabilities),
            "capabilities_healthy": healthy,
        }

    def doctor(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        problems = self.store.integrity_ok()
        checks.append(
            {
                "name": "database_integrity",
                "ok": not problems,
                "detail": "ok" if not problems else "; ".join(problems),
            }
        )
        checks.append(
            {
                "name": "database_writable",
                "ok": self._check_writable(),
                "detail": str(self.db_path),
            }
        )
        capabilities = self.registry.discover()
        for capability in capabilities:
            health = self.registry.probe(capability)
            capability.health = health
            capability.readiness = readiness(
                self.registry.adapter_for(capability), health
            )
            capability.last_probe = utc_now_iso()
            self.store.save_capability(capability)
            checks.append(
                {
                    "name": f"capability:{capability.id}",
                    "ok": health != HealthStatus.DOWN,
                    "detail": (
                        f"adapter={capability.adapter} health={health.value} "
                        f"readiness={capability.readiness}"
                    ),
                }
            )
        adapters = sorted(self.registry.adapters.keys())
        checks.append(
            {
                "name": "adapter_boundaries",
                "ok": bool(adapters),
                "detail": f"registered adapters: {', '.join(adapters)}",
            }
        )
        return {"ok": all(c["ok"] for c in checks), "checks": checks}

    def _check_writable(self) -> bool:
        try:
            self.store.conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # objectives
    # ------------------------------------------------------------------
    def create_objective(
        self,
        title: str,
        description: str = "",
        *,
        priority: str = Priority.MEDIUM.value,
        operation: str | None = None,
        params: dict[str, Any] | None = None,
        constraints: list[str] | None = None,
        parent_id: str | None = None,
        parent: Objective | str | None = None,
        extra_context: dict[str, Any] | None = None,
    ) -> Objective:
        try:
            priority_value = Priority(str(priority).upper())
        except ValueError:
            raise AgentOSError(
                f"invalid priority {priority!r}; use LOW, MEDIUM, HIGH or CRITICAL"
            )
        if parent is not None:
            parent_ref = parent.id if isinstance(parent, Objective) else str(parent)
            if parent_id is not None and parent_id != parent_ref:
                raise AgentOSError(
                    "conflicting parent_id and parent; pass only one"
                )
            parent_id = parent_ref
        if parent_id is not None and self.store.get_objective(parent_id) is None:
            raise AgentOSError(f"unknown parent objective: {parent_id}")
        context: dict[str, Any] = dict(extra_context or {})
        if operation:
            context["operation"] = operation
        if params:
            context["params"] = dict(params)
        objective = Objective(
            id=new_id("obj"),
            title=title,
            description=description,
            status=ObjectiveState.CREATED,
            priority=priority_value,
            constraints=list(constraints or []),
            context=context,
            parent_id=parent_id,
        )
        self.store.save_objective(objective)
        self.bus.emit(
            EventType.OBJECTIVE_CREATED,
            objective_id=objective.id,
            payload={"title": title},
        )
        # A created objective with a known shape is immediately schedulable.
        objective.status = ObjectiveState.READY
        objective.mark_updated()
        self.store.save_objective(objective)
        self.bus.emit(EventType.OBJECTIVE_READY, objective_id=objective.id)
        return objective

    def get_objective(self, objective_id: str) -> Objective:
        objective = self.store.get_objective(objective_id)
        if objective is None:
            raise AgentOSError(f"unknown objective: {objective_id}")
        return objective

    def children(self, objective_id: str) -> list[Objective]:
        self.get_objective(objective_id)
        return self.store.list_objectives(parent_id=objective_id)

    def list_objectives(self, status: str | None = None) -> list[Objective]:
        if status is not None:
            try:
                ObjectiveState(status.upper())
            except ValueError:
                raise AgentOSError(f"invalid status {status!r}")
            return self.store.list_objectives(status=status.upper())
        return self.store.list_objectives()

    def cancel_objective(self, objective_id: str) -> Objective:
        objective = self.get_objective(objective_id)
        if objective.status in (ObjectiveState.COMPLETED, ObjectiveState.CANCELLED):
            return objective
        objective.status = ObjectiveState.CANCELLED
        objective.next_action = None
        objective.mark_updated()
        self.store.save_objective(objective)
        self.bus.emit(EventType.OBJECTIVE_CANCELLED, objective_id=objective.id)
        return objective

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------
    def run(
        self,
        objective_id: str,
        *,
        operation: str | None = None,
        params: dict[str, Any] | None = None,
        max_attempts: int | None = None,
        timeout_seconds: float | None = None,
        force: bool = False,
    ) -> Report:
        if params is not None:
            objective = self.get_objective(objective_id)
            objective.context = dict(objective.context or {})
            objective.context["params"] = dict(params)
            objective.mark_updated()
            self.store.save_objective(objective)
        return self.engine.run(
            objective_id,
            EngineOptions(
                max_attempts=int(
                    max_attempts if max_attempts is not None
                    else self.config.get("max_attempts", DEFAULT_MAX_ATTEMPTS)
                ),
                operation=operation,
                timeout_seconds=float(
                    timeout_seconds if timeout_seconds is not None
                    else self.config.get(
                        "default_timeout_seconds", DEFAULT_TIMEOUT_SECONDS
                    )
                ),
                force=force,
            ),
        )

    def verify(self, objective_id: str) -> VerificationResult:
        return self.engine.verify_latest(objective_id)

    def verify_project(
        self,
        project_path: str,
        stages: list[str] | None = None,
        objective_id: str | None = None,
    ) -> VerificationResult:
        """Run the staged verification pipeline against a project directory.

        Business logic lives here (stage-name validation + persistence);
        the CLI only formats the result. ``stages`` accepts VerifyStage
        names or values (e.g. "STATIC"/"static"); None runs all stages.
        """
        from agentos.verify import VERIFY_STAGES, Verifier

        if objective_id is not None:
            objective = self.get_objective(objective_id)
        else:
            objective = Objective(
                id=new_id("obj"),
                title=f"Verify {project_path}",
                description="ad-hoc staged project verification",
            )
        wanted = None
        if stages is not None:
            valid = {s.value: s for s in VERIFY_STAGES}
            valid.update({s.name: s for s in VERIFY_STAGES})
            wanted = []
            for name in stages:
                stage = valid.get(str(name).strip().lower()) or valid.get(
                    str(name).strip().upper()
                )
                if stage is None:
                    raise AgentOSError(
                        f"unknown verify stage {name!r}; "
                        f"valid: {sorted(s.value for s in VERIFY_STAGES)}"
                    )
                wanted.append(stage)
        result = Verifier().run_verify_pipeline(objective, project_path, stages=wanted)
        self.store.save_verification(result)
        return result

    def recover(
        self,
        objective_id: str,
        *,
        max_attempts: int | None = None,
        operation: str | None = None,
    ) -> Report:
        return self.engine.recover(
            objective_id,
            EngineOptions(
                max_attempts=int(
                    max_attempts if max_attempts is not None
                    else self.config.get("max_attempts", DEFAULT_MAX_ATTEMPTS)
                ),
                operation=operation,
                timeout_seconds=float(
                    self.config.get(
                        "default_timeout_seconds", DEFAULT_TIMEOUT_SECONDS
                    )
                ),
            ),
        )

    # ------------------------------------------------------------------
    # capabilities
    # ------------------------------------------------------------------
    def capabilities(self, refresh: bool = False) -> list[Capability]:
        return self.registry.discover(refresh_health=refresh)

    def agents(self) -> list[Capability]:
        """Capabilities of type agent/model. Empty until real ones exist."""
        return [
            c
            for c in self.registry.discover()
            if c.type in (CapabilityType.AGENT, CapabilityType.MODEL)
        ]

    def add_configured_cli(
        self,
        capability_id: str,
        name: str,
        command: str,
        description: str = "",
        verify_expected: str | None = None,
    ) -> Capability:
        return self.registry.register_configured_cli(
            capability_id=capability_id,
            name=name,
            command=command,
            description=description,
            verify_expected=verify_expected,
        )

    def remove_capability(self, capability_id: str) -> bool:
        capability = self.store.get_capability(capability_id)
        if capability is not None and capability.source == "native":
            raise AgentOSError(
                f"refusing to remove native capability {capability_id!r}"
            )
        return self.registry.remove(capability_id)

    # ------------------------------------------------------------------
    # inspection
    # ------------------------------------------------------------------
    def get_execution(self, execution_id: str) -> Execution:
        execution = self.store.get_execution(execution_id)
        if execution is None:
            raise AgentOSError(f"unknown execution: {execution_id}")
        return execution

    def list_executions(
        self, objective_id: str | None = None, limit: int = 50
    ) -> list[Execution]:
        if objective_id is not None:
            self.get_objective(objective_id)
        executions = self.store.list_executions(objective_id=objective_id)
        return executions[: max(0, limit)]

    def inspect(self, objective_id: str) -> dict[str, Any]:
        objective = self.get_objective(objective_id)
        executions = self.store.list_executions(objective_id=objective_id)
        executions.sort(key=lambda e: (e.started_at, e.attempt))
        verifications = self.store.list_verifications(objective_id=objective_id)
        failures = self.store.list_failures(objective_id=objective_id)
        events = self.bus.for_objective(objective_id)
        children = self.store.list_objectives(parent_id=objective_id)
        return {
            "objective": objective.to_dict(),
            "executions": [e.to_dict() for e in executions],
            "verifications": [v.to_dict() for v in verifications],
            "failures": [f.to_dict() for f in failures],
            "events": [e.to_dict() for e in events],
            "children": [c.to_dict() for c in children],
        }

    def events(
        self, objective_id: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        if objective_id is not None:
            self.get_objective(objective_id)
            events = self.bus.for_objective(objective_id, limit=limit)
        else:
            events = self.bus.recent(limit=limit)
        return [e.to_dict() for e in events]

    def verification_status(self, objective_id: str) -> VerificationStatus:
        return self.get_objective(objective_id).verification_status

    # ------------------------------------------------------------------
    # delegation: the consumer primitive
    # ------------------------------------------------------------------
    def delegate(
        self,
        title: str,
        description: str = "",
        *,
        operation: str | None = None,
        params: dict[str, Any] | None = None,
        constraints: list[str] | None = None,
        priority: str = Priority.MEDIUM.value,
        max_attempts: int | None = None,
        timeout_seconds: float | None = None,
    ) -> Report:
        """Express an objective and let AgentOS handle the orchestration.

        This is the call other projects use: create -> run -> report.
        """
        objective = self.create_objective(
            title,
            description=description,
            priority=priority,
            operation=operation,
            params=params,
            constraints=constraints,
        )
        return self.run(
            objective.id,
            max_attempts=max_attempts,
            timeout_seconds=timeout_seconds,
        )

    # ------------------------------------------------------------------
    # plans / learning / gaps / quality / policy
    # ------------------------------------------------------------------
    def plans(self, objective_id: str | None = None) -> list[dict[str, Any]]:
        if objective_id is not None:
            self.get_objective(objective_id)
        return [p.to_dict() for p in self.store.list_plans(objective_id)]

    def patterns(
        self, objective_class: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.store.list_patterns(objective_class, limit)]

    def gaps(self, objective_id: str | None = None) -> list[dict[str, Any]]:
        if objective_id is not None:
            self.get_objective(objective_id)
        return [g.to_dict() for g in self.store.list_gaps(objective_id)]

    def quality(self, objective_id: str) -> list[dict[str, Any]]:
        self.get_objective(objective_id)
        return [q.to_dict() for q in self.store.list_quality(objective_id)]

    def policy_view(self) -> dict[str, Any]:
        return self.policy.to_dict()

    def grant_approval(self, operation_class: str) -> dict[str, Any]:
        operation_class = str(operation_class)
        if operation_class not in self.policy.approvals_granted:
            self.policy.approvals_granted.append(operation_class)
            self.config["policy"] = self.policy.to_dict()
            self._save_config()
            self.registry = CapabilityRegistry(
                self.store, self.bus, config=self.config, policy=self.policy
            )
            self.engine = Engine(self.store, self.bus, self.registry)
        return self.policy.to_dict()

    def revoke_approval(self, operation_class: str) -> dict[str, Any]:
        if operation_class in self.policy.approvals_granted:
            self.policy.approvals_granted.remove(str(operation_class))
            self.config["policy"] = self.policy.to_dict()
            self._save_config()
            self.registry = CapabilityRegistry(
                self.store, self.bus, config=self.config, policy=self.policy
            )
            self.engine = Engine(self.store, self.bus, self.registry)
        return self.policy.to_dict()

    # ------------------------------------------------------------------
    # federation: executor cells, routing, jobs, telemetry
    # ------------------------------------------------------------------
    def _federation_config(self) -> dict[str, Any]:
        return dict(self.config.get("federation", {}) or {})

    def _probe_cell_health(self, adapter_name: str) -> str:
        capability = next(
            (c for c in self.registry.discover() if c.adapter == adapter_name),
            None,
        )
        if capability is None:
            return "UNAVAILABLE"
        health = self.registry.probe(capability)
        return health.value

    def _read_grokbot_posture(self) -> dict[str, Any]:
        """Measure the grokbot-office usage posture via its read-only mode."""
        adapter = self.registry.adapters.get("grokbot-office")
        if adapter is None:
            return {}
        request = ExecutionRequest(
            operation="grokbot-office.mode",
            mode=ExecutionMode.LIVE,
            authorization=[
                {
                    "kind": "federation_posture",
                    "grant": "live",
                    "detail": "read-only grokbot-office posture refresh",
                }
            ],
        )
        try:
            result = adapter.execute("grokbot-office.mode", request)
        except Exception:
            return {}
        if not result.ok:
            return {}
        text = "".join(
            str(result.output.get(key) or "")
            for key in ("stdout", "stderr")
        )
        band: str | None = None
        used: int | None = None
        match = re.search(r"mode:\s*([A-Z_]+)\s*\(\s*(\d+)%\s*used", text)
        if match:
            band = match.group(1)
            used = int(match.group(2))
        return {
            "band": band or "UNKNOWN",
            "used_pct": used,
            "measured_at": utc_now_iso(),
        }

    def _federation_cells(self, refresh: bool = False) -> list:
        """Executor cells: local tools (real) + grokbot-office (probed) +
        configured peers. Persisted; statuses are measured, never assumed."""
        cfg = self._federation_config()
        if not refresh:
            stored = self.store.list_federation_cells()
            if stored:
                return stored
        from agentos.federation import ExecutorCell

        cells: list[ExecutorCell] = []
        previous = {c.id: c for c in self.store.list_federation_cells()}

        shell_health = self._probe_cell_health("shell")
        shell_health = (
            shell_health if shell_health in ("OK", "AVAILABLE", "READY") else "UNAVAILABLE"
        )
        cells.append(
            make_executor_cell(
                id="local-tools",
                provider="shell",
                description="Local shell + filesystem + echo execution channels",
                capability_ids=["shell", "filesystem", "echo"],
                supported_operations=[
                    "shell.run",
                    "fs.read",
                    "fs.write",
                    "fs.exists",
                    "fs.list",
                    "echo",
                ],
                adapter="shell",
                health=shell_health,
                data_class_max=DataClass.HIGHLY_SENSITIVE.value,
                quality_ladder=QualityFloor.REVIEWED.value,
                latency_class=LatencyClass.INTERACTIVE.value,
                persistence=True,
                cost_hint={"per_run": 0, "note": "local channels"},
            )
        )

        grok_enabled = bool(
            (cfg.get("grokbot_office") or {}).get("enabled", True)
        )
        if grok_enabled:
            grok_health = self._probe_cell_health("grokbot-office")
            is_up = grok_health in ("OK", "AVAILABLE", "READY")
            usage = {}
            if is_up and (cfg.get("grokbot_office") or {}).get(
                "read_posture", True
            ):
                usage = self._read_grokbot_posture()
            cells.append(
                make_executor_cell(
                    id="grokbot-office",
                    provider="grokbot-office",
                    description=(
                        "GrokBot office control plane via a read-only bridge; "
                        "usage-governed, scarce (premium tier)."
                    ),
                    capability_ids=list(READ_ONLY_COMMANDS),
                    supported_operations=list(READ_ONLY_COMMANDS),
                    adapter="grokbot-office",
                    health=grok_health if is_up else "UNAVAILABLE",
                    data_class_max=DataClass.CONFIDENTIAL.value,
                    quality_ladder=QualityFloor.VERIFIED.value,
                    latency_class=LatencyClass.BATCH.value,
                    persistence=False,
                    cost_hint={
                        "per_run": 0,
                        "note": "GrokBot tokens are scarce; treated as premium",
                    },
                    usage=usage,
                    is_external=True,
                )
            )

        for peer in (cfg.get("peers") or []):
            if not isinstance(peer, dict) or not peer.get("id"):
                continue
            try:
                cells.append(ExecutorCell.from_dict(peer))
            except Exception:
                continue

        # Multi-cell federation: each entry is one independently authorized
        # environment, loaded as configured metadata only. Cells are never
        # probed or contacted here; health stays as configured (UNKNOWN
        # unless the operator recorded a verification). Credential-bearing
        # values are rejected by ExecutorCell itself.
        for definition in (cfg.get("cells") or []):
            if not isinstance(definition, dict) or not definition.get("id"):
                continue
            try:
                cells.append(ExecutorCell.from_dict(definition))
            except Exception:
                continue

        # Re-hydrate previously stored cells the rebuild does not cover
        # (e.g. approved MCP metadata cells): a refresh re-probes, it never
        # silently drops registered cells.
        known = {c.id for c in cells}
        for cell_id, stored_cell in previous.items():
            if cell_id not in known:
                cells.append(stored_cell)

        for cell in cells:
            self.store.save_federation_cell(cell)
        return sorted(cells, key=lambda c: c.id)

    def federation_executors(self, refresh: bool = False) -> list[dict[str, Any]]:
        cells = self._federation_cells(refresh=refresh)
        out: list[dict[str, Any]] = []
        for cell in cells:
            base = cell.to_dict()
            base["usable"] = cell.usable()
            base["band"] = usage_band(cell)
            base["premium"] = tier_premium(cell.tier)
            base["tier_name"] = tier_name(cell.tier)
            out.append(base)
        return out

    def federation_status(self) -> dict[str, Any]:
        cells = self._federation_cells(refresh=True)
        jobs = self.store.list_federation_jobs()
        results: list[dict[str, Any]] = []
        envelope_results: list[ResultEnvelope] = []
        for job in jobs:
            for item in self.store.list_federation_results(job_id=job["id"]):
                if item["result"]:
                    try:
                        envelope = ResultEnvelope.from_dict(item["result"])
                    except Exception:
                        continue
                    envelope_results.append(envelope)
                    results.append(item)
        telemetry = export_telemetry(cells, envelope_results)
        return {
            "executors": [c.id for c in cells],
            "cells_by_tier": {
                tier_name(t): n for t, n in Counter(c.tier for c in cells).items()
            },
            "cells_by_health": dict(Counter(c.health for c in cells)),
            "jobs": len(jobs),
            "results": len(results),
            "grokbot_office": next(
                (
                    {
                        "provider": c.provider,
                        "health": c.health,
                        "usable": c.usable(),
                        "band": usage_band(c),
                        "tier": tier_name(c.tier),
                    }
                    for c in cells
                    if c.id == "grokbot-office"
                ),
                None,
            ),
            "telemetry": telemetry,
        }

    # ------------------------------------------------------------------
    # federation: capabilities / matrix / distill / node
    # ------------------------------------------------------------------
    def federation_capabilities(self, refresh: bool = False) -> list[dict[str, Any]]:
        """Discoverable capability catalog (native + probed), deduped by id.

        Health is whatever the registry measured: ``refresh=True`` re-probes
        every registered capability now, otherwise the last persisted probe
        stands. Rows are sorted by id so output is deterministic.
        """
        seen: set[str] = set()
        rows: list[dict[str, Any]] = []
        for capability in self.registry.discover(refresh_health=refresh):
            if capability.id in seen:
                continue
            seen.add(capability.id)
            row = capability.to_dict()
            row["usable"] = capability.is_usable()
            rows.append(row)
        rows.sort(key=lambda row: row["id"])
        return rows

    def federation_matrix(self, refresh: bool = False) -> dict[str, Any]:
        """Executor x capability routing matrix.

        Rows come from the canonical capability matrix (``executors.py``),
        overlaid with *measured* health/availability from the registry's
        native catalog and the executor cells. Tier is the executor cell's
        tier when a cell exists for the executor, otherwise the canonical
        default tier. Every row is honest: health is probe- or store-derived,
        never assumed.
        """
        from agentos.executors import MATRIX_DIMENSIONS, matrix_rows
        from agentos.federation import default_tier

        cells = self._federation_cells(refresh=refresh)
        capabilities = self.registry.discover(refresh_health=refresh)
        health: dict[str, str] = {}
        availability: dict[str, str] = {}
        for capability in capabilities:
            health[capability.id] = capability.health.value
            availability[capability.id] = (
                "AVAILABLE" if capability.is_usable() else "UNAVAILABLE"
            )
        hints: dict[str, tuple[str, tuple[str, ...]]] = {
            "local-worker": ("shell", ("local-tools", "shell")),
            "opencode": ("opencode", ("opencode",)),
            "openhands": ("openhands", ("openhands",)),
            "hermes": ("hermes", ("hermes",)),
            "openclaw": ("openclaw", ("openclaw",)),
            "browser-harness": ("browser-harness", ("browser-harness",)),
            "grokbot-office": ("grokbot-office", ("grokbot-office",)),
        }
        resolved: dict[str, dict[str, Any]] = {}
        for executor, (provider, keys) in hints.items():
            cell = next(
                (
                    c
                    for c in cells
                    if c.id in keys or c.provider in keys or c.adapter in keys
                ),
                None,
            )
            cap_health = health.get(executor, "UNKNOWN")
            tier = cell.tier if cell is not None else default_tier(provider, [])
            resolved[executor] = {
                "cell": cell.id if cell is not None else None,
                "health": cell.health if cell is not None else cap_health,
                "availability": (
                    "AVAILABLE" if cell.usable() else "UNAVAILABLE"
                )
                if cell is not None
                else availability.get(
                    executor,
                    "AVAILABLE"
                    if cap_health not in ("DOWN", "UNAVAILABLE")
                    else "UNAVAILABLE",
                ),
                "tier": tier,
                "tier_name": tier_name(tier),
            }
        rows = matrix_rows(health=health, availability=availability)
        for row in rows:
            meta = resolved.get(row["executor"])
            if meta is None:
                continue
            row["health"] = meta["health"]
            row["availability"] = meta["availability"]
            row["tier"] = meta["tier"]
            row["tier_name"] = meta["tier_name"]
            row["usable"] = (
                row["availability"] == "AVAILABLE"
                and row["health"] not in ("DOWN", "UNAVAILABLE")
            )
            row["cell"] = meta["cell"]
        rows.sort(key=lambda row: (row["executor"], row["capability"]))
        executors_seen: list[str] = []
        for row in rows:
            if row["executor"] not in executors_seen:
                executors_seen.append(row["executor"])
        return {
            "dimensions": list(MATRIX_DIMENSIONS),
            "executors": executors_seen,
            "rows": rows,
        }

    def federation_distill(self, refresh: bool = False) -> dict[str, Any]:
        """Deterministic, evidence-based distilled context of the registry +
        federation, built by ``agentos.distill.distill_material``.

        The caller sees the exact distillation contract (facts, citations,
        uncertainties, compressedContext) plus how many source entries fed
        it. Pure and deterministic: identical state produces identical
        output.
        """
        from agentos.distill import distill_material

        capabilities = self.registry.discover(refresh_health=refresh)
        cells = self._federation_cells(refresh=refresh)
        entries: list[dict[str, Any]] = []
        for capability in sorted(capabilities, key=lambda c: c.id):
            entries.append(
                {
                    "source": "capability",
                    "ref": capability.id,
                    "text": (
                        f"{capability.name} is a {capability.type.value} "
                        f"capability with adapter={capability.adapter} "
                        f"health={capability.health.value} "
                        f"readiness={capability.readiness} source={capability.source} "
                        f"operations: {', '.join(capability.operations)}"
                    ),
                }
            )
        for cell in sorted(cells, key=lambda c: c.id):
            entries.append(
                {
                    "source": "executor_cell",
                    "ref": cell.id,
                    "text": (
                        f"{cell.provider} executor cell tier={tier_name(cell.tier)} "
                        f"health={cell.health} usable={cell.usable()} "
                        f"persistence={cell.persistence} "
                        f"supports: {', '.join(cell.supported_operations or cell.capability_ids)}"
                    ),
                }
            )
        distilled = distill_material(entries, max_facts=32)
        return {
            "entries": len(entries),
            "sources": sorted({e["source"] for e in entries}),
            "fact_count": len(distilled.facts),
            "distilled": distilled.to_dict(),
            "note": (
                "deterministic structural distillation over the live registry "
                f"and {len(cells)} executor cells; no model consumed"
            ),
        }

    def federation_nodes(self, refresh: bool = False) -> dict[str, Any]:
        """Node-state records for every configured federation cell.

        State comes from ``node.node_status_from_config`` (manual override
        wins; no override means UNKNOWN, surfaced honestly) and each cell is
        run through ``node.node_gate`` to report eligibility. ``refresh``
        reloads config and re-probes cell health/cells.
        """
        from agentos.node import (
            effective_node_state,
            node_gate,
            node_status_from_config,
        )

        if refresh:
            self.config = self._load_config()
        cells = self._federation_cells(refresh=refresh)
        node_status = node_status_from_config(self.config)
        node_state = effective_node_state(self.config)
        node = node_status.to_dict()
        node["state"] = node_state
        node["policy"] = (
            "manual override wins; UNKNOWN treats the laptop as reachable "
            "but is surfaced honestly"
        )
        records: list[dict[str, Any]] = []
        for cell in sorted(cells, key=lambda c: c.id):
            eligible, note = node_gate(cell.to_dict(), node_state)
            records.append(
                {
                    "node": cell.id,
                    "provider": cell.provider,
                    "adapter": cell.adapter,
                    "health": cell.health,
                    "tier": tier_name(cell.tier),
                    "persistent_remote": cell.persistent_remote,
                    "requires_online": cell.requires_online,
                    "gate": note,
                    "eligibility": "ELIGIBLE" if eligible else "INELIGIBLE",
                }
            )
        return {
            "node": node,
            "nodes": records,
            "count": len(records),
        }

    def federate_route(self, job_data: dict[str, Any]) -> dict[str, Any]:
        """Pure routing: no persistence, no execution. Returns envelope +
        decision (+ premium plan when the best cell is premium-gated)."""
        try:
            job = JobEnvelope.from_dict(job_data)
        except (ValueError, TypeError) as exc:
            raise AgentOSError(f"invalid federation job: {exc}")
        cells = self._federation_cells()
        decision = resolve_route(
            job, cells, approval_evidence=job_data.get("approvalEvidence")
        )
        plan = (
            plan_before_premium(job, decision, len(cells))
            if decision.chosen and decision.premiumGated
            else None
        )
        return {
            "job": job.to_dict(),
            "decision": decision.to_dict(),
            "plan": plan,
        }

    def federate_submit(self, job_data: dict[str, Any]) -> dict[str, Any]:
        """Persist + route a job; record a result.

        Side-effecting execution stays out of the federation boundary by
        default: jobs run SIMULATED/INSPECT, and LIVE is only reachable by
        the read-only grokbot-office bridge when approval evidence exists.
        Premium tiers are never auto-executed.
        """
        try:
            job = JobEnvelope.from_dict(job_data)
        except (ValueError, TypeError) as exc:
            raise AgentOSError(f"invalid federation job: {exc}")
        cells = self._federation_cells()
        decision = resolve_route(
            job, cells, approval_evidence=job_data.get("approvalEvidence")
        )
        self.store.save_federation_job(
            job, JobStatus.ROUTED.value, decision=decision.to_dict()
        )
        self.bus.emit(
            EventType.FEDERATION_JOB_ROUTED,
            payload={
                "job_id": job.id,
                "chosen": decision.chosen.id if decision.chosen else None,
                "tier": decision.tier,
            },
        )
        if decision.chosen is None:
            self.store.save_federation_job(
                job, JobStatus.BLOCKED.value, decision=decision.to_dict()
            )
            return {
                "job": job.to_dict(),
                "decision": decision.to_dict(),
                "result": None,
                "executed": False,
                "note": "no executor passes the routing gates",
            }
        if decision.premiumGated:
            plan = plan_before_premium(job, decision, len(cells))
            return {
                "job": job.to_dict(),
                "decision": decision.to_dict(),
                "plan": plan,
                "result": None,
                "executed": False,
                "note": "premium tier requires cheaper verified channels and approval; nothing executed",
            }
        result = self._execute_cell(decision.chosen, job, job_data)
        final_status = (
            JobStatus.COMPLETED.value
            if result.status == ResultStatus.COMPLETED.value
            else JobStatus.BLOCKED.value
        )
        self.store.save_federation_result(result)
        self.store.save_federation_job(
            job, final_status, decision=decision.to_dict(), result=result
        )
        self.bus.emit(
            EventType.FEDERATION_RESULT_RECEIVED,
            payload={
                "job_id": job.id,
                "executor": result.executor,
                "status": result.status,
            },
        )
        return {
            "job": job.to_dict(),
            "decision": decision.to_dict(),
            "result": result.to_dict(),
            "executed": True,
        }

    def _execute_cell(
        self,
        cell,
        job: JobEnvelope,
        job_data: dict[str, Any],
    ) -> ResultEnvelope:
        """Run the chosen cell through its adapter, conservatively.

        LIVE is refused for everything except the read-only grokbot-office
        bridge; ordinary side-effecting work belongs to the engine.
        Remote A2A cells execute over the v1.0 JSON-RPC surface with
        bounded timeouts; LIVE remote work needs approval evidence.
        """
        if cell.adapter == "a2a-remote":
            return self._execute_a2a_cell(cell, job, job_data)
        adapter = self.registry.adapters.get(cell.adapter)
        operation = (
            job.targetCapability
            if job.targetCapability != "any"
            else (cell.supported_operations or [""])[0]
        )
        capability = next(
            (
                c
                for c in self.registry.discover()
                if operation in c.operations or c.id == operation
            ),
            None,
        )
        if capability is not None:
            adapter = self.registry.adapters.get(capability.adapter) or adapter
        if adapter is None:
            return ResultEnvelope(
                jobId=job.id,
                executor=cell.id,
                status=ResultStatus.FAILED.value,
                blocker=f"no adapter registered for {operation}",
                completedAt=utc_now_iso(),
            )
        adapter_name = getattr(adapter, "name", "")
        authorization = list(job_data.get("approvalEvidence") or [])
        mode = ExecutionMode.SIMULATED
        if job.executionClass in (
            ExecutionClass.STANDARD.value,
            ExecutionClass.PREMIUM.value,
        ) and any(
            "live" in str(item.get("kind", "")).lower()
            or "approve" in str(item.get("grant", "")).lower()
            for item in authorization
        ):
            if adapter_name == "grokbot-office":
                mode = ExecutionMode.LIVE
            else:
                return ResultEnvelope(
                    jobId=job.id,
                    executor=cell.id,
                    status=ResultStatus.REFUSED.value,
                    blocker=(
                        "federation boundary executes only read-only bridges; "
                        "use engine run/delegate for side-effecting work"
                    ),
                    completedAt=utc_now_iso(),
                )
        params = dict(job_data.get("params") or {})
        request = ExecutionRequest(
            operation=operation,
            params=params,
            mode=mode,
            authorization=authorization,
        )
        try:
            adapter_result = adapter.execute(operation, request)
        except Exception as exc:
            return ResultEnvelope(
                jobId=job.id,
                executor=cell.id,
                status=ResultStatus.FAILED.value,
                blocker=f"{type(exc).__name__}: {exc}",
                completedAt=utc_now_iso(),
            )
        if adapter_result.ok:
            text = text_of(adapter_result.output)
            return ResultEnvelope(
                jobId=job.id,
                executor=cell.id,
                status=ResultStatus.COMPLETED.value,
                verifiedFacts=[text[:2000]] if text else [],
                action="none",
                confidence=0.9,
                completedAt=utc_now_iso(),
            )
        return ResultEnvelope(
            jobId=job.id,
            executor=cell.id,
            status=ResultStatus.FAILED.value,
            blocker=adapter_result.error or "execution failed",
            completedAt=utc_now_iso(),
        )

    def _execute_a2a_cell(
        self,
        cell,
        job: JobEnvelope,
        job_data: dict[str, Any],
    ) -> ResultEnvelope:
        """Execute one job on a remote A2A v1.0 peer (bounded, trust-gated)."""
        from agentos import a2a_v1
        from agentos.federation import trust_gate

        allowed, reason = trust_gate(cell, job)
        if not allowed:
            return ResultEnvelope(
                jobId=job.id,
                executor=cell.id,
                status=ResultStatus.REFUSED.value,
                blocker=f"trust gate refused remote execution: {reason}",
                completedAt=utc_now_iso(),
            )
        if job.executionClass == ExecutionClass.PREMIUM.value and not any(
            "live" in str(item.get("kind", "")).lower()
            or "approve" in str(item.get("grant", "")).lower()
            for item in (job_data.get("approvalEvidence") or [])
        ):
            return ResultEnvelope(
                jobId=job.id,
                executor=cell.id,
                status=ResultStatus.REFUSED.value,
                blocker="remote LIVE execution needs approval evidence",
                completedAt=utc_now_iso(),
            )
        base_url = str((cell.config or {}).get("base_url", "") or "")
        if not base_url:
            return ResultEnvelope(
                jobId=job.id,
                executor=cell.id,
                status=ResultStatus.FAILED.value,
                blocker="remote cell has no base_url",
                completedAt=utc_now_iso(),
            )
        timeout = min(float(job_data.get("timeout", 30.0) or 30.0), 120.0)
        try:
            card = a2a_v1.discover_card(base_url, timeout=min(timeout, 15.0))
            skill = (cell.supported_operations or [""])[0]
            task = a2a_v1.send_message(
                card, skill, job.deliverable, timeout=min(timeout, 15.0)
            )
            final = a2a_v1.wait_task(card, task.task_id, timeout=timeout)
            if final.state != "COMPLETED":
                return ResultEnvelope(
                    jobId=job.id,
                    executor=cell.id,
                    status=ResultStatus.FAILED.value,
                    blocker=f"remote task ended in state {final.state}",
                    completedAt=utc_now_iso(),
                )
            artifacts = a2a_v1.fetch_artifacts(
                card, task.task_id, timeout=min(timeout, 15.0)
            )
            return ResultEnvelope(
                jobId=job.id,
                executor=cell.id,
                status=ResultStatus.COMPLETED.value,
                verifiedFacts=[json.dumps(a, default=str)[:2000] for a in artifacts],
                action="none",
                confidence=0.9,
                completedAt=utc_now_iso(),
            )
        except Exception as exc:
            return ResultEnvelope(
                jobId=job.id,
                executor=cell.id,
                status=ResultStatus.FAILED.value,
                blocker=f"{type(exc).__name__}: {exc}",
                completedAt=utc_now_iso(),
            )

    def federation_jobs(
        self, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if status is not None:
            try:
                JobStatus(str(status).upper())
            except ValueError:
                raise AgentOSError(
                    f"invalid job status {status!r}; "
                    f"valid: {[s.value for s in JobStatus]}"
                )
        return self.store.list_federation_jobs(status=status, limit=limit)

    def federation_telemetry(self) -> dict[str, Any]:
        cells = self._federation_cells()
        results = [
            ResultEnvelope.from_dict(item["result"])
            for item in self.store.list_federation_results()
            if item["result"]
        ]
        return {
            "cells": [c.id for c in cells],
            **export_telemetry(cells, results),
        }

    def federation_packet(self, line: str) -> dict[str, Any]:
        """Parse a grokbot-office-compatible TASK/RESULT packet line."""
        from agentos.federation import parse_result_packet, parse_task_packet

        kind = str(line).strip().split("|", 1)[0].upper()
        if kind == "TASK":
            return {"kind": "TASK", **parse_task_packet(line)}
        if kind == "RESULT":
            return {"kind": "RESULT", **parse_result_packet(line)}
        raise AgentOSError("packet line must start with TASK| or RESULT|")

    # ------------------------------------------------------------------
    # MCP registry discovery + certification lifecycle.
    # Discovery never equals trust: candidates are inert metadata until a
    # human explicitly approves them, and approval registers an
    # *unavailable* cell (MCP client transport is DEFERRED), never a
    # live executor.
    # ------------------------------------------------------------------
    def _mcp_registry_base_url(self) -> str:
        cfg = self.config.get("mcp") or {}
        return str(cfg.get("registry_base_url", "") or "").strip().rstrip("/")

    def mcp_discover(
        self, query: str, limit: int = 25
    ) -> dict[str, Any]:
        from agentos import mcp_discovery

        base_url = self._mcp_registry_base_url()
        if not base_url:
            return {
                "status": "UNCONFIGURED",
                "detail": (
                    "no MCP registry configured; set "
                    "config.mcp.registry_base_url to enable discovery"
                ),
                "candidates": [],
            }
        try:
            servers = mcp_discovery.search_registry(base_url, query, limit=limit)
        except mcp_discovery.MCPRegistryError as exc:
            return {"status": "UNAVAILABLE", "detail": str(exc), "candidates": []}
        saved: list[dict[str, Any]] = []
        for server in servers:
            try:
                candidate = mcp_discovery.ingest_server(server, source=base_url)
            except mcp_discovery.MCPRegistryError:
                continue
            self.store.save_mcp_candidate(candidate)
            saved.append(candidate)
        return {"status": "OK", "detail": f"{len(saved)} candidates", "candidates": saved}

    def mcp_candidates(self) -> list[dict[str, Any]]:
        return self.store.list_mcp_candidates()

    def _mcp_get(self, candidate_id: str) -> dict[str, Any]:
        candidate = self.store.get_mcp_candidate(candidate_id)
        if candidate is None:
            raise AgentOSError(f"unknown MCP candidate {candidate_id!r}")
        return candidate

    def mcp_inspect(self, candidate_id: str) -> dict[str, Any]:
        from agentos import mcp_discovery

        candidate = self._mcp_get(candidate_id)
        if candidate.get("state") == "DISCOVERED":
            candidate = mcp_discovery.transition(candidate, "INSPECTED")
            self.store.save_mcp_candidate(candidate)
        return {"candidate": candidate}

    def mcp_test(self, candidate_id: str) -> dict[str, Any]:
        from agentos import mcp_discovery

        candidate = self._mcp_get(candidate_id)
        if candidate.get("state") == "INSPECTED":
            candidate = mcp_discovery.transition(candidate, "TESTING")
        elif candidate.get("state") != "TESTING":
            raise AgentOSError(
                f"candidate {candidate_id!r} must be INSPECTED before testing "
                f"(state={candidate.get('state')!r})"
            )
        candidate["test_result"] = mcp_discovery.certify_static(candidate)
        candidate = mcp_discovery.transition(candidate, "TESTED")
        self.store.save_mcp_candidate(candidate)
        return {"candidate": candidate}

    def mcp_approve(
        self, candidate_id: str, evidence: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        from agentos import mcp_discovery

        candidate = self._mcp_get(candidate_id)
        if candidate.get("state") != "TESTED":
            raise AgentOSError(
                f"candidate {candidate_id!r} must be TESTED before approval "
                f"(state={candidate.get('state')!r}); discovery never implies approval"
            )
        if not evidence:
            raise AgentOSError(
                f"approving {candidate_id!r} requires explicit approval evidence"
            )
        candidate = mcp_discovery.transition(candidate, "APPROVED")
        self.store.save_mcp_candidate(candidate)
        cell = make_executor_cell(
            id=f"mcp:{candidate['id']}",
            provider=f"mcp:{candidate['id']}",
            description=(
                f"approved MCP candidate {candidate['name']} "
                f"{candidate['version']} (metadata only; client transport DEFERRED)"
            ),
            capability_ids=[],
            supported_operations=[],
            adapter="mcp-client",
            health="UNCONFIGURED",
            tier=6,
            data_class_max=DataClass.INTERNAL.value,
            quality_ladder=QualityFloor.BEST_EFFORT.value,
            latency_class=LatencyClass.STANDARD.value,
            persistence=False,
            is_external=True,
            config={"trust": "APPROVED_EXTERNAL", "transport": "DEFERRED"},
        )
        cell.availability = False
        self.store.save_federation_cell(cell)
        return {"candidate": candidate, "cell": cell.to_dict()}

    def mcp_reject(self, candidate_id: str, reason: str = "") -> dict[str, Any]:
        from agentos import mcp_discovery

        candidate = self._mcp_get(candidate_id)
        candidate = mcp_discovery.transition(candidate, "REJECTED")
        if reason:
            candidate["reject_reason"] = str(reason)[:500]
        self.store.save_mcp_candidate(candidate)
        return {"candidate": candidate}

    def mcp_revoke(self, candidate_id: str, reason: str = "") -> dict[str, Any]:
        from agentos import mcp_discovery

        candidate = self._mcp_get(candidate_id)
        candidate = mcp_discovery.transition(candidate, "REVOKED")
        if reason:
            candidate["revoke_reason"] = str(reason)[:500]
        self.store.save_mcp_candidate(candidate)
        for cell in self.store.list_federation_cells():
            if cell.id == f"mcp:{candidate_id}":
                cell.availability = False
                cell.health = "DISABLED"
                self.store.save_federation_cell(cell)
        return {"candidate": candidate}

    # ------------------------------------------------------------------
    # A2A v1.0 remote interop: discovery, card, test, registration.
    # Every remote peer is UNTRUSTED by default and HS-capped; nothing
    # sensitive is ever exposed automatically.
    # ------------------------------------------------------------------
    def a2a_peers(self) -> list[dict[str, Any]]:
        return [
            cell.to_dict()
            for cell in self._federation_cells()
            if cell.provider == "a2a-remote" or cell.adapter == "a2a-remote"
        ]

    def a2a_card(self) -> dict[str, Any]:
        """AgentOS's own minimal read-only card (no server started)."""
        from agentos import a2a_v1

        exports = tuple(
            ((self.config.get("a2a") or {}).get("exports") or ["echo"])
        )
        return a2a_v1.agentos_card(exports=exports).to_dict()

    def a2a_discover_peer(self, base_url: str) -> dict[str, Any]:
        from agentos import a2a_v1

        card = a2a_v1.discover_card(base_url)
        return {"card": card.to_dict(), "trust": card.trust}

    def a2a_test(self, base_url: str, skill: str = "a2a.test") -> dict[str, Any]:
        """End-to-end remote check: discover -> submit -> status -> result.

        Uses a deterministic harmless payload only. Returns the artifact
        record or a recorded failure — never raises past the boundary.
        """
        from agentos import a2a_v1

        steps: list[str] = []
        try:
            card = a2a_v1.discover_card(base_url, timeout=10.0)
            steps.append("discover:ok")
            task = a2a_v1.send_message(
                card, skill, "agentos interop probe", timeout=10.0
            )
            steps.append(f"submit:ok:{task.task_id}")
            final = a2a_v1.wait_task(card, task.task_id, timeout=20.0)
            steps.append(f"status:{final.state}")
            if final.state != "COMPLETED":
                return {"ok": False, "steps": steps, "state": final.state}
            artifacts = a2a_v1.fetch_artifacts(card, task.task_id, timeout=10.0)
            steps.append(f"result:ok:{len(artifacts)}-artifacts")
            return {"ok": True, "steps": steps, "artifacts": artifacts}
        except Exception as exc:
            steps.append(f"failed:{type(exc).__name__}")
            return {"ok": False, "steps": steps, "error": str(exc)[:300]}

    def a2a_register_peer(
        self, base_url: str, evidence: list[dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Register a remote peer as an ExecutorCell (UNTRUSTED, HS-capped)."""
        from agentos import a2a_v1
        from agentos.federation import ExecutorCell

        card = a2a_v1.discover_card(base_url)
        if evidence:
            card = a2a_v1.approve_card(card, evidence)
        cell = ExecutorCell.from_dict(
            a2a_v1.peer_to_cell(card, base_url)
        )
        self.store.save_federation_cell(cell)
        return {"cell": cell.to_dict(), "card": card.to_dict()}

    # ------------------------------------------------------------------
    # capability graph: unified find/explain over every executor source
    # ------------------------------------------------------------------
    def capability_find(
        self, need: str, limit: int = 10
    ) -> dict[str, Any]:
        from agentos import capability_graph

        capabilities = self.registry.discover()
        cells = self._federation_cells()
        stats: dict[str, dict[str, int]] = {}
        for capability in capabilities:
            try:
                entry = self.store.capability_stats(capability.id)
            except Exception:
                entry = {}
            if entry:
                stats[capability.id] = entry
        candidates = capability_graph.rank_candidates(
            need, capabilities, cells, stats=stats, limit=limit
        )
        return {"need": need, "candidates": candidates, "count": len(candidates)}

    def capability_explain(self, capability_id: str) -> dict[str, Any]:
        capability = self.store.get_capability(capability_id)
        if capability is None:
            known = [c.id for c in self.registry.discover()]
            if capability_id not in known:
                raise AgentOSError(f"unknown capability {capability_id!r}")
            capability = next(c for c in self.registry.discover() if c.id == capability_id)
        cells = self._federation_cells()
        cell = next(
            (
                c
                for c in cells
                if capability_id in (c.supported_operations or [])
                or capability_id in (c.capability_ids or [])
            ),
            None,
        )
        try:
            history = self.store.capability_stats(capability_id)
        except Exception:
            history = {}
        return {
            "capability": capability.to_dict(),
            "executor_cell": cell.to_dict() if cell is not None else None,
            "history": history or {"runs": 0, "note": "no measured runs yet"},
            "approval": (
                "premium approval required"
                if cell is not None and int(cell.tier or 0) >= 4
                else "none"
            ),
        }

    # ------------------------------------------------------------------
    # benchmarks: measured executor records (never invented rankings)
    # ------------------------------------------------------------------
    def learning_benchmark(self) -> dict[str, Any]:
        from agentos.benchmarks import run_learning_benchmark

        return run_learning_benchmark(self)

    def benchmark_run(self, executor_id: str, workload: str) -> dict[str, Any]:
        """Run one benchmark. Only the zero-cost local worker executes;
        every other executor/workload records an honest SKIPPED."""
        from agentos import benchmarks as bench_mod
        from agentos.node import effective_node_state

        workload = str(workload or "").upper()
        if workload not in bench_mod.WORKLOAD_CLASSES:
            raise AgentOSError(
                f"unknown workload {workload!r}; "
                f"valid: {sorted(bench_mod.WORKLOAD_CLASSES)}"
            )
        node_state = effective_node_state(self.config)
        if executor_id == "local-worker" and workload in bench_mod.LOCAL_SAFE_WORKLOADS:
            record = bench_mod.run_benchmark(
                executor_id,
                workload,
                self._benchmark_execute,
                node_state=node_state,
            )
        else:
            record = bench_mod.skip_record(
                executor_id,
                workload,
                self._benchmark_skip_reason(executor_id, workload),
                node_state=node_state,
            )
        self.store.save_benchmark_run(record)
        return {"record": record}

    def _benchmark_execute(self, text: str) -> str:
        """Real deterministic local execution for safe workloads."""
        import hashlib

        if text.startswith("transform:"):
            _, payload, rounds = text.split(":", 2)
            digest = payload.encode()
            for _ in range(max(0, min(int(rounds), 100000))):
                digest = hashlib.sha256(digest).digest()
            return digest.hex()[:16]
        if text.startswith("classify:"):
            lowered = text.lower()
            if any(k in lowered for k in ("bug", "fix", "error", "urgent")):
                return "bug"
            if any(k in lowered for k in ("add", "create", "feature")):
                return "feature"
            if "doc" in lowered:
                return "docs"
            return "unknown"
        if text.startswith("list:"):
            _, path, pattern = text.split(":", 2)
            matches = sorted(
                p.name for p in Path(path).glob(pattern) if p.is_file()
            )
            if not matches:
                raise AgentOSError(f"no files match {pattern!r} in {path!r}")
            return "listing"
        raise AgentOSError(f"no local implementation for benchmark input {text[:40]!r}")

    def _benchmark_skip_reason(self, executor_id: str, workload: str) -> str:
        if executor_id != "local-worker":
            return (
                f"executor {executor_id!r} not benchmarked: model/paid/remote "
                "execution is out of scope for zero-cost benchmarks"
            )
        return (
            f"workload {workload} has no safe local implementation "
            "(browser/model/persistent execution unavailable here)"
        )

    def benchmark_report(
        self,
        executor: str | None = None,
        workload: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        from agentos import benchmarks as bench_mod

        records = self.store.list_benchmark_runs(
            executor=executor, workload=workload, limit=limit
        )
        return {
            "runs": len(records),
            "records": records,
            "by_executor": bench_mod.summarize(records),
        }

    # ------------------------------------------------------------------
    # durable execution (§11) + Temporal evaluation (§12)
    # ------------------------------------------------------------------
    def durable_status(self) -> dict[str, Any]:
        from agentos import durable as durable_mod

        jobs = self.store.list_federation_jobs(limit=200)
        durable_jobs = [j for j in jobs if (j.get("envelope") or {}).get("durable")]
        smoke = durable_mod.durable_smoke(self.store)
        return {
            "backend": "native",
            "detail": (
                "AgentOS native persistence (federation jobs table); "
                "used only for multi-step/resumable work, simple commands stay native"
            ),
            "durable_jobs": len(durable_jobs),
            "smoke": smoke,
            "temporal": durable_mod.temporal_status(),
        }

    # ------------------------------------------------------------------
    # OrgOS / PAIOS bridges (§21/§22): read-only, no competing hierarchy
    # ------------------------------------------------------------------
    def orgos_status(self) -> dict[str, Any]:
        from agentos import bridges as bridges_mod

        cfg = self.config.get("orgos") or {}
        return bridges_mod.orgos_view(cfg.get("snapshot_path"))

    def paios_snapshot(self) -> dict[str, Any]:
        from agentos import bridges as bridges_mod
        from agentos.models import utc_now_iso

        cells = self._federation_cells()
        jobs = self.store.list_federation_jobs(limit=100)
        capabilities = [
            c.to_dict() for c in self.registry.discover()
        ]
        approved_mcp = [
            c["id"]
            for c in self.store.list_mcp_candidates()
            if c.get("state") == "APPROVED"
        ]
        return bridges_mod.paios_snapshot(
            {
                "executors": [c.id for c in cells],
                "cells": [c.to_dict() for c in cells],
                "capabilities": capabilities,
                "jobs": [
                    {"id": j["id"], "status": j["status"]} for j in jobs
                ],
                "health": {c.id: c.health for c in cells},
                "usage_scarcity": {
                    c.id: (c.usage or {}).get("band", "UNKNOWN") for c in cells
                },
                "routing_decisions": [
                    (j.get("decision") or {}).get("chosen") for j in jobs[:20]
                ],
                "a2a_peers": [c.id for c in cells if c.provider == "a2a-remote"],
                "mcp_approved_tools": approved_mcp,
                "durable_backend": {"backend": "native"},
                "generated_at": utc_now_iso(),
            }
        )

    # ------------------------------------------------------------------
    # usage + scarcity status (§9)
    # ------------------------------------------------------------------
    def usage_status(self) -> dict[str, Any]:
        from agentos import scarcity as scarcity_mod

        cells = self._federation_cells()
        profiles = {
            cell.id: scarcity_mod.scarcity_profile(cell) for cell in cells
        }
        return {
            "cells": profiles,
            "reset_mode": scarcity_mod.normalize_reset_mode(
                (self.config.get("routing") or {}).get("reset_mode", "PRESERVE")
            ),
            "harvest": {
                "allowed": scarcity_mod.harvest_allowed(
                    (self.config.get("routing") or {}).get("harvest", {})
                )[0],
                "backlog_classes": list(scarcity_mod.HARVEST_BACKLOG_CLASSES),
            },
        }

    # ------------------------------------------------------------------
    # resource governor (usage snapshots, usage ledger, governor routing)
    # ------------------------------------------------------------------
    def usage_snapshots(self) -> dict[str, Any]:
        """Raw snapshot records stored so far (data as persisted)."""
        from agentos.resource_governor import snapshot_key

        records = self.store.list_usage_snapshots()
        return {
            "source_requested": (
                "DATA_ONLY: no network or paid usage lookups run from here"
            ),
            "snapshots": [
                {
                    "snapshot": r["snapshot"],
                    "updated_at": r["updated_at"],
                    "key": snapshot_key(r["snapshot"]),
                }
                for r in records
            ],
            "count": len(records),
        }

    def usage_resolve(
        self, provider: str | None = None, account_cell: str | None = None
    ) -> dict[str, Any]:
        from agentos.resource_governor import resolve_usage

        records = self.store.list_usage_snapshots()
        candidates: list[Any] = []
        for r in records:
            snap = r["snapshot"] or {}
            if provider and str(snap.get("provider") or "") != provider:
                continue
            if account_cell and str(snap.get("accountCell") or "") != account_cell:
                continue
            candidates.append(snap)
        resolved = resolve_usage(candidates, now=self._usage_now())
        listeners = {
            "intersite": (
                (self.config.get("governor") or {}).get("intersite_usage")
                or (self.config.get("usage") or {}).get("intersite_tail")
            ),
            "pointer_key": (
                (self.config.get("usage") or {})
                .get("provider_pointers", {})
                .get("grokbot", {})
                .get("usage_live")
            ),
        }
        return {
            "usage_resolution": resolved.to_dict(),
            "snapshot_count": len(candidates),
            "filter": {"provider": provider, "account_cell": account_cell},
            "listeners": listeners,
            "honesty_note": " ".join(resolved.warnings) if resolved.warnings else (
                "fresh official snapshot governs; unknown never defaults to 0 or unlimited"
            ),
        }

    def usage_import(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Import ONE usage snapshot; validated, never fabricated.

        ``allowed_external_offset_sources`` must be empty/absent (the AgentOS
        data layer accepts no externally-supplied "offset" values today).
        """
        from agentos.resource_governor import parse_snapshot

        if (snapshot or {}).get("allowed_external_offset_sources"):
            raise AgentOSError(
                "external offset sources are not accepted by this build"
            )
        try:
            parsed = parse_snapshot(snapshot or {}, now=self._usage_now())
        except ValueError as exc:
            raise AgentOSError(f"invalid usage snapshot: {exc}")
        self.store.save_usage_snapshot(parsed.to_dict())
        self.bus.emit(
            EventType.USAGE_SNAPSHOT_IMPORTED,
            payload={"provider": parsed.provider, "accountCell": parsed.accountCell},
        )
        return {
            "imported": parsed.to_dict(),
            "problems": [],
            "note": "snapshot is data only; freshness evaluates source + age",
        }

    def usage_refresh(self) -> dict[str, Any]:
        """Refresh usage from the ONLY reachable path: exported local files
        under an operator-configured data directory. No network paid calls."""
        from agentos.resource_governor import (
            GROK_USAGE_LIVE_ADDRESS,
            grok_address_to_path,
            read_snapshot_file,
        )

        data_dir = (self.config.get("usage") or {}).get("data_dir") or (
            self.config.get("usage") or {}
        ).get("local_data_dir")
        if data_dir:
            address = (self.config.get("usage") or {}).get(
                "grok_usage_live_address", GROK_USAGE_LIVE_ADDRESS
            )
            path = grok_address_to_path(address, data_dir)
            try:
                parsed = read_snapshot_file(path)
                imported = self.usage_import(parsed.to_dict())
            except (OSError, ValueError, TypeError) as exc:
                return {
                    "ok": False,
                    "error": f"refresh failed: {exc}",
                    "path": str(path),
                }
            return {"ok": True, "imported": imported, "path": str(path)}
        return {
            "ok": False,
            "error": "no usage.data_dir configured; nothing was refreshed",
            "path": None,
        }

    def usage_add(
        self,
        provider: str,
        category: str = "manual",
        account_cell: str = "",
        amount: float | None = None,
        unit: str = "",
        used_pct: float | None = None,
        refills_at: str | None = None,
        behavior: str = "RECORD",
        note: str = "",
    ) -> dict[str, Any]:
        """Add one usage-ledger entry. Only RECORD is supported today."""
        from agentos.usage_ledger import UsageEntry, save_entry

        if str(behavior or "").upper() != "RECORD":
            raise AgentOSError(f"unsupported behavior {behavior!r} (only RECORD)")
        try:
            entry = UsageEntry(
                provider=provider,
                category=category,
                accountCell=account_cell,
                amount=amount,
                unit=unit,
                usedPct=used_pct,
                refillsAt=refills_at,
                source="MANUAL",
                asOf=utc_now_iso(),
                note=note,
            )
            save_entry(self.store, entry)
        except ValueError as exc:
            raise AgentOSError(str(exc))
        self.bus.emit(
            EventType.USAGE_ENTRY_RECORDED,
            payload={"ledger_id": entry.id, "provider": provider},
        )
        return {"entry": entry.to_dict()}

    def usage_ledger(
        self, provider: str | None = None, category: str | None = None
    ) -> dict[str, Any]:
        from agentos.usage_ledger import ledger_summary

        records = self.store.list_usage_entries(
            provider=provider, category=category, limit=500
        )
        return {
            "entries": [
                {"id": r["id"], "entry": r["entry"], "created_at": r["created_at"]}
                for r in records
            ],
            "summary": ledger_summary(self.store),
            "filter": {"provider": provider, "category": category},
        }

    def governor_weights(self) -> dict[str, Any]:
        from agentos.resource_governor import (
            DEFAULT_GOVERNOR_WEIGHTS,
            governor_weights,
        )

        try:
            weights = governor_weights(self.config)
        except ValueError as exc:
            raise AgentOSError(str(exc))
        return {
            "weights": weights["weights"],
            "source": weights["weights_source"],
            "available_weights": sorted(DEFAULT_GOVERNOR_WEIGHTS),
            "note": (
                "weights only steer explicit routing; they never override "
                "safety gates"
            ),
        }

    def governor_route(self, job_data: dict[str, Any]) -> dict[str, Any]:
        """Governor-routed job: gates via resolve_route, then cost ranking.

        Persists a routing_outcome row (outcome PENDING) so verification can
        later record whether the choice actually worked.
        """
        from agentos.models import new_id as make_id
        from agentos.resource_governor import governor_route

        try:
            job = JobEnvelope.from_dict(job_data)
        except (ValueError, TypeError) as exc:
            raise AgentOSError(f"invalid federation job: {exc}")
        cells = self._federation_cells()
        stats = self.store.routing_outcome_stats()
        try:
            routed = governor_route(
                job, cells, stats=stats, config=self.config
            )
        except (ValueError, TypeError) as exc:
            raise AgentOSError(f"invalid governor route request: {exc}")
        chosen = routed.get("governed_choice")
        outcome_record = {
            "id": make_id("governor"),
            "job_id": routed["jobId"],
            "decision": routed,
            "cost": {
                "candidate_count": routed.get("candidate_count"),
                "governed_rationale": routed.get("governed_rationale"),
            },
            "chosen": chosen,
            "outcome": "PENDING",
        }
        self.store.save_routing_outcome(outcome_record)
        self.bus.emit(
            EventType.GOVERNOR_ROUTE_RECORDED,
            payload={"job_id": routed["jobId"], "chosen": chosen},
        )
        return {"job": job_data, "governed_route": routed, "outcome": outcome_record}

    def governor_outcomes(
        self, job_id: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        rows = self.store.list_routing_outcomes(job_id=job_id, limit=limit)
        return {
            "outcomes": rows,
            "summary": self.store.routing_outcome_stats(),
            "filter": {"job_id": job_id, "limit": limit},
        }

    def governor_outcome_summary(self) -> dict[str, Any]:
        stats = self.store.routing_outcome_stats()
        return {
            "executors": stats,
            "total_rows": sum(entry["runs"] for entry in stats.values()),
        }

    def governor_verify(
        self, job_id: str, status: str, evidence: list[str] | None = None
    ) -> dict[str, Any]:
        """Mark the latest governor-routed outcome for a job as verified/failed."""
        from agentos.task_verification import (
            TaskVerificationStatus,
            evidence_for_verification,
        )

        outcome = self.store.latest_routing_outcome(job_id)
        if outcome is None:
            raise AgentOSError(f"no routing outcome recorded for job {job_id!r}")
        state = str(status or "").upper()
        if state in ("VERIFIED", "COMPLETED", "SUCCESS", "RESOLVED", "PASS"):
            final_status = TaskVerificationStatus.VERIFIED.value
            event = "PASS"
        elif state in ("FAILED", "REFUSED", "BLOCKED", "FAILURE", "FAIL"):
            final_status = TaskVerificationStatus.FAILED.value
            event = "FAIL"
        else:
            raise AgentOSError(
                f"unsupported outcome {status!r} (use VERIFIED or FAILED)"
            )
        record = dict(outcome)
        record["outcome"] = "VERIFIED" if event == "PASS" else "FAILED"
        record["evidence"] = [str(e) for e in (evidence or [])][:50]
        self.store.save_routing_outcome(record)
        worker = record.get("chosen")
        verification = evidence_for_verification(
            job_id, final_status, worker, "", evidence or []
        )
        verification["worker"] = worker or ""
        verification["verifier"] = worker
        verification["transition"] = {
            "event": event,
            "allowed": True,
            "reason": "evidence accepted" if event == "PASS" else "evidence rejected",
        }
        self.store.save_job_verification(verification)
        return {"outcome": record, "verification": verification, "id": outcome["id"]}

    # ------------------------------------------------------------------
    # verification-first task model (§)
    # ------------------------------------------------------------------
    def governor_verify_record(
        self, job_id: str, status: str, evidence: list[str] | None = None
    ) -> dict[str, Any]:
        """Record a job verification directly (job_verifications table)."""
        from agentos.task_verification import (
            TaskVerificationStatus,
            evidence_for_verification,
        )

        outcome = self.store.latest_routing_outcome(job_id)
        worker = ((outcome or {}).get("decision") or {}).get("chosen")
        if not worker and outcome:
            worker = outcome.get("chosen")
        vtype = ""
        state = str(status or "").upper()
        if state == "PASS" or state in ("VERIFIED", "SUCCESS", "COMPLETED"):
            final_status = TaskVerificationStatus.VERIFIED.value
            event = "PASS"
            reason = "evidence accepted"
        elif state == "FAIL" or state in ("FAILED", "BLOCKED", "REFUSED"):
            final_status = TaskVerificationStatus.FAILED.value
            event = "FAIL"
            reason = "evidence rejected"
        else:
            raise AgentOSError(f"unsupported verification event {status!r}")
        record = evidence_for_verification(
            job_id,
            final_status,
            worker,
            vtype,
            evidence or [],
        )
        record["worker"] = worker or ""
        record["verifier"] = worker
        record["transition"] = {"event": event, "allowed": True, "reason": reason}
        self.store.save_job_verification(record)
        return {"verification": record}

    def supervisor_status(self) -> dict[str, Any]:
        """Honest status of the supervisor operating model in this runtime."""
        from agentos.supervisor import supervisor_status

        status = supervisor_status(self)
        self.bus.emit(
            EventType.SUPERVISOR_STATUS,
            payload={"verified_live": status["verified_live"]},
        )
        return status

    def routing_calibration(self) -> dict[str, Any]:
        from agentos import benchmarks as bench_mod

        weights = dict((self.config.get("routing") or {}).get("weights") or {})
        defaults = {
            "health": 1.0,
            "history": 1.0,
            "cost": 1.0,
            "latency": 1.0,
            "scarcity": 1.0,
            "benchmark_success": 1.0,
        }
        merged = {**defaults, **{k: float(v) for k, v in weights.items()}}
        records = self.store.list_benchmark_runs(limit=500)
        return {
            "weights": merged,
            "weights_source": (
                "operator config (routing.weights)"
                if weights
                else "built-in defaults (no operator override)"
            ),
            "benchmarks_by_executor": bench_mod.summarize(records),
            "policy": (
                "weights change only via explicit operator config; "
                "no opaque self-modification; scoring itself is unchanged "
                "until an operator enables calibration"
            ),
        }

    # ------------------------------------------------------------------
    # federation doctor/cells/health (§23/§24)
    # ------------------------------------------------------------------
    def federate_doctor(self) -> dict[str, Any]:
        """Federation health across PASS/WARN/UNCONFIGURED/UNAVAILABLE/FAIL.

        Optional executors/services never fail the fabric merely for being
        absent; FAIL is reserved for broken required invariants.
        """
        from agentos import bridges as bridges_mod
        from agentos import durable as durable_mod

        checks: list[dict[str, Any]] = []

        def check(name: str, category: str, detail: str) -> None:
            checks.append({"name": name, "category": category, "detail": detail})

        try:
            cells = self._federation_cells()
            check("cells", "PASS", f"{len(cells)} executor cells registered")
        except Exception as exc:
            return {
                "ok": False,
                "checks": [
                    {"name": "cells", "category": "FAIL", "detail": str(exc)}
                ],
            }
        grok = [c for c in cells if c.provider == "grokbot-office"]
        if grok:
            check(
                "grok-primary",
                "PASS" if grok[0].usable() else "WARN",
                f"health={grok[0].health} band={(grok[0].usage or {}).get('band', 'UNKNOWN')}",
            )
        else:
            check("grok-primary", "UNCONFIGURED", "grokbot-office cell not enabled")
        a2a = [c for c in cells if c.provider == "a2a-remote"]
        check(
            "a2a-remote",
            "PASS" if a2a else "UNCONFIGURED",
            f"{len(a2a)} remote peers registered" if a2a else "no remote peers",
        )
        if self._mcp_registry_base_url():
            check("mcp-registry", "PASS", "registry base URL configured")
        else:
            check("mcp-registry", "UNCONFIGURED", "config.mcp.registry_base_url unset")
        approved = [
            c for c in self.store.list_mcp_candidates() if c.get("state") == "APPROVED"
        ]
        check(
            "mcp-approved",
            "PASS" if approved else "UNCONFIGURED",
            f"{len(approved)} approved candidates" if approved else "none approved",
        )
        orgos = bridges_mod.orgos_view((self.config.get("orgos") or {}).get("snapshot_path"))
        check(
            "orgos",
            "PASS" if orgos.get("status") == "OK" else "UNCONFIGURED",
            orgos.get("detail", "snapshot loaded"),
        )
        temporal = durable_mod.temporal_status()
        check("temporal", "UNCONFIGURED", f"{temporal['status']}: stdlib-only build")
        jobs = self.store.list_federation_jobs(limit=5)
        check("federation-store", "PASS", f"{len(jobs)} recent jobs readable")
        failed = [c for c in checks if c["category"] == "FAIL"]
        return {"ok": not failed, "checks": checks}

    def federate_cells(self) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self._federation_cells()]

    def federate_health(self) -> dict[str, Any]:
        cells = self._federation_cells()
        by_health: dict[str, int] = {}
        for cell in cells:
            by_health[cell.health] = by_health.get(cell.health, 0) + 1
        return {
            "cells": len(cells),
            "usable": sum(1 for c in cells if c.usable()),
            "by_health": by_health,
            "categories": ["PASS", "WARN", "UNCONFIGURED", "UNAVAILABLE", "FAIL"],
        }

    def federate_route_explain(self, job_data: dict[str, Any]) -> dict[str, Any]:
        """Explainable routing (§15): task class, candidates, choice, why."""
        from agentos.federation import score_executor_cell
        from agentos.node import effective_node_state
        from agentos import scarcity as scarcity_mod

        try:
            job = JobEnvelope.from_dict(job_data)
        except (ValueError, TypeError) as exc:
            raise AgentOSError(f"invalid federation job: {exc}")
        cells = self._federation_cells()
        decision = resolve_route(
            job, cells, approval_evidence=job_data.get("approvalEvidence")
        )
        node_state = effective_node_state(self.config)
        ranked: list[dict[str, Any]] = []
        for cell in cells:
            score, parts = score_executor_cell(job, cell)
            if parts is None:
                ranked.append(
                    {
                        "executor": cell.id,
                        "eligible": False,
                        "score": 0.0,
                        "reasons": ["fails a hard routing gate"],
                    }
                )
                continue
            profile = scarcity_mod.scarcity_profile(cell)
            ranked.append(
                {
                    "executor": cell.id,
                    "eligible": True,
                    "score": round(score, 1),
                    "reasons": parts,
                    "tier": tier_name(cell.tier),
                    "scarcity": profile["quotaScarcity"],
                }
            )
        ranked.sort(key=lambda r: (not r["eligible"], -r["score"], r["executor"]))
        for candidate in ranked:
            history = self.store.get_executor_metrics(
                executor=candidate["executor"], limit=200
            )
            candidate["hardPolicy"] = {
                "eligible": bool(candidate["eligible"]),
                "score": float(candidate["score"]),
                "reasons": list(candidate.get("reasons") or []),
            }
            candidate["learnedModifiers"] = {
                "samples": len(history),
                "verified": sum(
                    1 for row in history if row.get("verification_result") == "VERIFIED"
                ),
                "retries": sum(int(row.get("retry_count") or 0) for row in history),
                "humanCorrections": sum(
                    int(row.get("human_correction") or 0) for row in history
                ),
                "averageLatencyMs": (
                    sum(int(row.get("latency_ms") or 0) for row in history) / len(history)
                    if history else None
                ),
            }
        alternatives = [a for a in decision.alternatives]
        target = job.targetCapability.lower()
        if any(k in target for k in ("inspect", "repo", "code.", "fs.", "file")):
            task_class = "repository inspection"
        elif "browser" in target:
            task_class = "browser automation"
        elif "grokbot" in target:
            task_class = "office orchestration"
        elif target in ("echo", "shell.run"):
            task_class = "deterministic execution"
        else:
            task_class = "general execution"
        chosen_profile = None
        if decision.chosen is not None:
            chosen_profile = scarcity_mod.scarcity_profile(decision.chosen)
        return {
            "job": job.to_dict(),
            "task_class": task_class,
            "required_capabilities": [job.targetCapability],
            "quality_floor": job.qualityFloor,
            "data_class": job.dataClass,
            "node_state": node_state,
            "candidates": ranked[:5],
            "chosen": decision.chosen.id if decision.chosen else None,
            "why": decision.rationale,
            "fallback": alternatives[0] if alternatives else None,
            "premium_scarce_required": bool(
                decision.premiumGated
                or (decision.chosen is not None and decision.chosen.tier >= 4)
            ),
            "approval_required": bool(
                decision.premiumGated or not decision.approved
            )
            and decision.chosen is not None
            and decision.chosen.tier >= 4,
            "chosen_scarcity": chosen_profile,
        }

    # ------------------------------------------------------------------
    # operational dashboard
    # ------------------------------------------------------------------
    def dashboard(self) -> dict[str, Any]:
        objectives = self.store.list_objectives()
        active = [
            o.to_dict()
            for o in objectives
            if o.status in (ObjectiveState.RUNNING, ObjectiveState.VERIFYING)
        ]
        stuck = [
            o.to_dict()
            for o in objectives
            if o.status in (ObjectiveState.FAILED, ObjectiveState.BLOCKED)
        ]
        capabilities = self.store.list_capabilities()
        plans = self.store.list_plans()
        latest_plan = plans[-1].to_dict() if plans else None
        recent_events = [e.to_dict() for e in self.bus.recent(limit=15)]
        approvals = [
            c
            for c in self.policy.approval_required_classes
            if c not in self.policy.approvals_granted
        ]
        return {
            "active_objectives": active,
            "stuck": stuck,
            "current_strategy": latest_plan,
            "capabilities": [
                {
                    "id": c.id,
                    "type": c.type.value,
                    "health": c.health.value,
                    "usable": c.is_usable(),
                    "operations": c.operations,
                }
                for c in capabilities
            ],
            "recent_events": [
                {"type": e["event_type"], "at": e["created_at"]} for e in recent_events
            ],
            "pending_approvals": approvals,
            "counts": self.store.counts(),
        }

    # ------------------------------------------------------------------
    # ecosystem scout: provider-neutral candidate ingestion + lifecycle.
    # Discovery never equals trust: a candidate is inert declared metadata
    # until explicitly APPROVED; approval records an inert catalog cell and
    # never downloads, installs, or executes — static certification only.
    # ------------------------------------------------------------------
    def _ecosystem_base_source(self) -> str:
        return str(
            (self.config.get("ecosystem") or {}).get("sheet_path") or ""
        ).strip()

    def ecosystem_discover(
        self, category: str, name: str, source: str, version: str = ""
    ) -> dict[str, Any]:
        from agentos import ecosystem_scout

        candidate = ecosystem_scout.ingest_source(
            category, name, source, version=version
        )
        self.store.save_ecosystem_candidate(candidate)
        return {"status": "OK", "candidate": candidate}

    def ecosystem_candidates(self) -> list[dict[str, Any]]:
        return self.store.list_ecosystem_candidates()

    def _ecosystem_get(self, candidate_id: str) -> dict[str, Any]:
        candidate = self.store.get_ecosystem_candidate(candidate_id)
        if candidate is None:
            raise AgentOSError(f"unknown ecosystem candidate {candidate_id!r}")
        return candidate

    def ecosystem_inspect(self, candidate_id: str) -> dict[str, Any]:
        from agentos import ecosystem_scout

        candidate = self._ecosystem_get(candidate_id)
        if candidate.get("state") == "DISCOVERED":
            candidate = ecosystem_scout.transition(candidate, "INSPECTED")
            self.store.save_ecosystem_candidate(candidate)
        return {"candidate": candidate}

    def ecosystem_test(self, candidate_id: str) -> dict[str, Any]:
        from agentos import ecosystem_scout

        candidate = self._ecosystem_get(candidate_id)
        if candidate.get("state") == "INSPECTED":
            candidate = ecosystem_scout.transition(candidate, "TESTING")
        elif candidate.get("state") != "TESTING":
            raise AgentOSError(
                f"candidate {candidate_id!r} must be INSPECTED before testing "
                f"(state={candidate.get('state')!r})"
            )
        candidate["test_result"] = ecosystem_scout.certify_static(candidate)
        candidate = ecosystem_scout.transition(candidate, "TESTED")
        self.store.save_ecosystem_candidate(candidate)
        return {"candidate": candidate}

    def ecosystem_approve(
        self, candidate_id: str, evidence: list[str] | None = None
    ) -> dict[str, Any]:
        from agentos import ecosystem_scout

        candidate = self._ecosystem_get(candidate_id)
        if candidate.get("state") != "TESTED":
            raise AgentOSError(
                f"candidate {candidate_id!r} must be TESTED before approval "
                f"(state={candidate.get('state')!r}); discovery never implies approval"
            )
        if not evidence:
            raise AgentOSError(
                f"approving {candidate_id!r} requires explicit approval evidence"
            )
        candidate["approval_evidence"] = sorted(set(evidence))
        candidate = ecosystem_scout.transition(candidate, "APPROVED")
        self.store.save_ecosystem_candidate(candidate)
        return {"candidate": candidate}

    def ecosystem_reject(self, candidate_id: str, reason: str = "") -> dict[str, Any]:
        from agentos import ecosystem_scout

        candidate = self._ecosystem_get(candidate_id)
        if str(candidate.get("state")) not in (
            "DISCOVERED",
            "INSPECTED",
            "TESTING",
            "TESTED",
        ):
            raise AgentOSError(
                f"candidate {candidate_id!r} is already terminal "
                f"(state={candidate.get('state')!r})"
            )
        candidate["rejection_reason"] = reason
        candidate = ecosystem_scout.transition(candidate, "REJECTED")
        self.store.save_ecosystem_candidate(candidate)
        return {"candidate": candidate}


    # ------------------------------------------------------------------
    # reusable workflow catalog: usage-led lifecycle, never re-plan.
    # A workflow earns its place by being reused; repeated reuse is the
    # only thing that upgrades VERIFIED -> USED_WIDELY. Registration is
    # inert metadata that never executes or installs anything.
    # ------------------------------------------------------------------
    def workflow_register(
        self,
        name: str,
        steps: list[str] | None = None,
        requested_permissions: list[str] | None = None,
        schedule_hint: str = "",
    ) -> dict[str, Any]:
        from agentos import reusable_workflows

        workflow = reusable_workflows.register(
            name, steps or [], requested_permissions=requested_permissions,
            schedule_hint=schedule_hint,
        )
        self.store.save_workflow_catalog(workflow)
        return {"workflow": workflow}

    def workflow_catalog(self) -> list[dict[str, Any]]:
        return self.store.list_workflow_catalog()

    def _workflow_get(self, workflow_id: str) -> dict[str, Any]:
        workflow = self.store.get_workflow_catalog(workflow_id)
        if workflow is None:
            raise AgentOSError(f"unknown workflow {workflow_id!r}")
        return workflow

    def workflow_use(self, workflow_id: str, found_useful: bool = True) -> dict[str, Any]:
        from agentos import reusable_workflows

        workflow = self._workflow_get(workflow_id)
        workflow = reusable_workflows.record_use(workflow, found_useful=found_useful)
        self.store.save_workflow_catalog(workflow)
        return {"workflow": workflow}

    def workflow_state(self, workflow_id: str, to_state: str) -> dict[str, Any]:
        from agentos import reusable_workflows

        workflow = self._workflow_get(workflow_id)
        workflow = reusable_workflows.transition(workflow, to_state)
        self.store.save_workflow_catalog(workflow)
        return {"workflow": workflow}


    # ------------------------------------------------------------------
    # low-usage efficiency kernel (shared IntelligenceCache + triage + team)
    # ------------------------------------------------------------------
    def efficiency_cache(self) -> "Any":
        """Shared provider-neutral IntelligenceCache over the durable store."""
        from agentos.intelligence_cache import IntelligenceCache

        return IntelligenceCache(
            self.store,
            self.config,
            project=str(
                (self.config.get("efficiency") or {}).get("project") or ""
            ),
            requester_scope=str(
                (self.config.get("efficiency") or {})
                .get("requester_scope", "CONFIDENTIAL")
            ),
        )

    def efficiency_triage(
        self,
        objective: str,
        *,
        target_capability: str = "",
        exact_cache_hit: bool = False,
        artifact_hit: bool = False,
        known_workflow: bool = False,
        provider: str = "",
    ) -> dict[str, Any]:
        """Cheap rules-first triage; Grok is a late escalation, never first."""
        from agentos.efficiency import triage

        request = {
            "objective": objective,
            "exact_cache_hit": exact_cache_hit,
            "artifact_hit": artifact_hit,
            "known_workflow": known_workflow,
            "target_capability": target_capability,
            "provider": provider,
        }
        if exact_cache_hit:
            self.store.bump_intelligence_counter("requests_total", 1)
        decision = triage(
            objective,
            exact_cache_hit=exact_cache_hit,
            artifact_hit=artifact_hit,
            known_workflow=known_workflow,
            target_capability=target_capability,
            provider=provider,
        )
        return {"request": request, "decision": decision.to_dict()}

    def efficiency_team(
        self,
        *,
        quality_floor: str = "",
        risk: str = "low",
        decomposable: bool = False,
        subtasks: int = 1,
        verification_burden: bool = False,
        latency_class: str = "interactive",
        security: str = "internal",
    ) -> dict[str, Any]:
        """Smallest capable team; defaults to a single worker, no fan-out."""
        from agentos.efficiency import plan_team

        plan = plan_team(
            quality_floor=quality_floor,
            risk=risk,
            decomposable=decomposable,
            independent_subtasks=int(subtasks),
            verification_burden=verification_burden,
            latency_class=latency_class,
            security=security,
        )
        return {"teamPlan": plan.to_dict()}

    def efficiency_fingerprint(self, task: dict[str, Any]) -> dict[str, Any]:
        """TaskFingerprint + dedupe check across all roles/workers."""
        from agentos.efficiency import TaskFingerprint

        fp = TaskFingerprint(
            intent=str(task.get("intent") or ""),
            input_refs=tuple(
                dict(r) for r in (task.get("input_refs") or [])
            ),
            output_schema=str(task.get("output_schema") or ""),
            constraints=tuple(str(c) for c in (task.get("constraints") or [])),
            role=str(task.get("role") or ""),
        )
        fingerprint = fp.fingerprint()
        existing = self.store.get_task_fingerprint(fingerprint)
        self.store.save_task_fingerprint(fp.to_dict())
        return {
            "fingerprint": fingerprint,
            "duplicate": bool(existing),
            "existing": existing,
        }

    def efficiency_delta(
        self,
        baseline: dict[str, str],
        current: dict[str, str],
        task: str,
        *,
        baseline_ref: str = "baseline@1",
        cache_scope: str = "",
    ) -> dict[str, Any]:
        """DeltaContext: baseline ref + changes + task (never full history)."""
        from agentos.efficiency import compute_delta

        delta = compute_delta(
            baseline,
            current,
            task,
            baseline_ref=baseline_ref,
            cache_scope=cache_scope,
        )
        return {"delta": delta.to_dict()}

    def efficiency_cache_get(
        self,
        intent: str,
        *,
        output_schema: str = "",
        project: str = "",
        semantic: bool = False,
    ) -> dict[str, Any]:
        cache = self.efficiency_cache()
        result = (
            cache.get_semantic(intent, output_schema=output_schema, project=project)
            if semantic
            else cache.get(intent, output_schema=output_schema, project=project)
        )
        return {"cache": result}

    def efficiency_cache_put(
        self,
        intent: str,
        *,
        category: str = "exact",
        output_schema: str = "",
        payload: dict[str, Any] | None = None,
        artifact_ref: str = "",
        security_scope: str = "INTERNAL",
        confidence: float = 1.0,
        source_refs: list[dict[str, Any]] | None = None,
        dependencies: dict[str, str] | None = None,
        provider: str = "",
        status: str = "FRESH",
        project: str = "",
        level: str = "L0_EXACT_RESULT",
    ) -> dict[str, Any]:
        cache = self.efficiency_cache()
        entry = cache.build_entry(
            intent,
            category=category,
            output_schema=output_schema,
            source_refs=source_refs,
            dependencies=dependencies,
            payload=payload,
            artifact_ref=artifact_ref,
            security_scope=security_scope,
            confidence=confidence,
            provider=provider,
            status=status,
            project=project,
            level=level,
        )
        cache.put(entry)
        return {"cache": entry.to_dict()}

    def efficiency_invalidate(
        self, dependencies: dict[str, str], *, also_exact: bool = False
    ) -> dict[str, Any]:
        """Narrow dependency invalidation (never project-wide blast radius)."""
        return self.efficiency_cache().invalidate(
            dependencies, also_exact=also_exact
        )

    def efficiency_stats(self) -> dict[str, Any]:
        from agentos.intelligence_cache import (
            DEFAULT_TTL_SECONDS,
            LAYERS,
            CATEGORIES,
        )

        cache = self.efficiency_cache()
        stats = cache.stats()
        stats["categories"] = list(CATEGORIES)
        stats["layers"] = list(LAYERS)
        stats["default_ttl_seconds"] = dict(DEFAULT_TTL_SECONDS)
        return {"efficiency": stats}

    def efficiency_worker(
        self,
        objective: str,
        *,
        target_capability: str = "",
        context_refs: list[str] | None = None,
        constraints: list[str] | None = None,
        output_schema: str = "",
        provider: str = "",
    ) -> dict[str, Any]:
        """Canonical worker request: OBJECTIVE + CONTEXT_REFS + DELTA +
        CONSTRAINTS + OUTPUT_SCHEMA. Returns an envelope-shaped decision —
        the engine executes; this method never fabricates a result."""
        from agentos.efficiency import TaskFingerprint, triage

        task = {
            "intent": objective,
            "input_refs": [
                {"ref": r, "hash": ""} for r in (context_refs or [])
            ],
            "output_schema": output_schema,
            "constraints": list(constraints or []),
        }
        fp = TaskFingerprint(
            intent=objective,
            input_refs=tuple(task["input_refs"]),
            output_schema=output_schema,
            constraints=tuple(constraints or []),
        )
        decision = triage(
            objective,
            target_capability=target_capability,
            provider=provider,
        )
        if decision.resolution.value in ("CACHE_RETURN", "ARTIFACT_RETURN"):
            self.store.bump_intelligence_counter("requests_total", 1)
        if decision.resolution.value == "GROKBOT":
            self.store.bump_intelligence_counter("grok_routed", 1)
        return {
            "status": "SCHEDULED",
            "taskId": fp.fingerprint(),
            "decision": decision.to_dict(),
            "contextRefs": list(context_refs or []),
            "constraints": list(constraints or []),
            "outputSchema": output_schema,
            "targetCapability": target_capability,
            "provider": provider,
            "result": None,
            "confidence": None,
            "evidenceRefs": [],
            "artifactRefs": [],
            "changes": [],
            "blockers": [],
            "next": decision.resolution.value,
        }

    # =====================================================================
    # LOW-USAGE LEARNING EXECUTION SYSTEM
    # =====================================================================

    def low_usage_metrics(self) -> LowUsageMetrics:
        traces = self.store.list_job_traces(limit=10000)
        counters = self.store.intelligence_counters()
        verifications = [trace for trace in traces if trace.get("verification")]
        accepted = sum(
            1 for trace in verifications
            if (trace.get("verification") or {}).get("result") == "ACCEPT"
        )
        team_sizes = [
            int((trace.get("teamPlan") or {}).get("size") or 0)
            for trace in traces
            if trace.get("teamPlan")
        ]
        workflows = self.store.list_workflows()
        autonomy_rows = self.store.conn.execute(
            "SELECT state FROM autonomy_records"
        ).fetchall()
        promoted = sum(
            1 for row in autonomy_rows
            if str(row["state"]) not in ("DISCOVERED", "OBSERVE")
        )
        return LowUsageMetrics(
            requests_total=int(counters.get("requests_total", 0)),
            zero_agent_resolutions=sum(
                1 for trace in traces
                if str(trace.get("executor") or "").startswith(("intelligence_cache", "system."))
            ),
            cache_hits=sum(
                1 for trace in traces
                if (trace.get("cacheDecision") or {}).get("status") == "HIT"
            ),
            artifact_reuse=int(counters.get("artifact_reuse", 0)),
            workflow_uses=sum(int(workflow.metrics.get("uses", 0)) for workflow in workflows),
            skill_uses=int(counters.get("skill_uses", 0)),
            model_calls=sum(int(trace.get("modelCalls") or 0) for trace in traces),
            premium_calls=sum(int(trace.get("premiumCalls") or 0) for trace in traces),
            grok_calls=sum(int(trace.get("grokCalls") or 0) for trace in traces),
            average_team_size=(sum(team_sizes) / len(team_sizes)) if team_sizes else 1.0,
            verification_required_rate=(len(verifications) / len(traces)) if traces else 0.0,
            verification_pass_rate=(accepted / len(verifications)) if verifications else 0.0,
            retries=sum(len(trace.get("retries") or []) for trace in traces),
            duplicate_tasks_prevented=int(counters.get("duplicate_tasks_prevented", 0)),
            human_escalations=int(counters.get("human_escalation", 0)),
            lessons_created=len(self.store.list_lessons()),
            lessons_adopted=len(self.store.list_lessons(status=LessonStatus.ADOPTED)),
            workflow_compilations=sum(
                1 for workflow in workflows
                if workflow.status != WorkflowStatus.DRAFT
            ),
            autonomy_promotions=promoted,
            incident_clusters=len(self.store.list_incidents()),
        )

    # Verification Engine
    def verification_start(self, task_id: str, task_class: str,
                           result: dict[str, Any]) -> list[VerificationRecord]:
        return self.verification_engine.start_verification(task_id, task_class, result)

    def verification_run(self, record: VerificationRecord,
                         executor_fn: callable) -> VerificationRecord:
        return self.verification_engine.run_verification(record, executor_fn)

    def verification_evaluate(self, task_id: str, records: list[VerificationRecord],
                              attempt: int) -> tuple[str, str | None]:
        outcome, reason = self.verification_engine.evaluate_outcomes(task_id, records, attempt)
        return outcome.value, reason

    def verification_evaluate_stored(self, task_id: str, attempt: int) -> tuple[str, str | None]:
        return self.verification_evaluate(
            task_id,
            self.store.list_verification_records(task_id),
            attempt,
        )

    def verification_record_result(
        self,
        record_id: str,
        success: bool,
        confidence: float,
        failure_reason: str,
    ) -> VerificationRecord:
        record = self.store.get_verification_record(record_id)
        if record is None:
            raise AgentOSError(f"unknown verification record: {record_id}")
        record.confidence = max(0.0, min(1.0, float(confidence)))
        record.result = (
            VerificationOutcome.ACCEPT
            if success and record.confidence >= 0.7
            else VerificationOutcome.RETRY_SAME_WORKER
        )
        record.failure_reason = None if success else failure_reason or "verification failed"
        record.recommendation = "accept" if record.result == VerificationOutcome.ACCEPT else "retry_or_escalate"
        record.completed_at = utc_now_iso()
        self.store.save_verification_record(record)
        return record

    # Coach
    def coach_analyze(self, task_id: str, task_class: str,
                      execution_history: list[dict[str, Any]]) -> list[LessonCandidate]:
        return self.coach.analyze_execution(task_id, task_class, execution_history)

    def coach_routine_success(self, task_id: str, task_class: str,
                              execution_history: list[dict[str, Any]]) -> list[LessonCandidate]:
        return self.coach.maybe_coach_routine_success(task_id, task_class, execution_history)

    def lesson_promote(self, lesson_id: str, status: LessonStatus) -> bool:
        return self.coach.promote_lesson(lesson_id, status)

    def lessons_for_task(self, task_class: str) -> list[LessonCandidate]:
        return self.coach.get_lessons_for_task_class(task_class)

    def adopted_lessons(self) -> list[LessonCandidate]:
        return self.coach.get_adopted_lessons()

    # Workflow Compiler
    def workflow_compile(self, task_class: str,
                         executions: list[dict[str, Any]] | None = None) -> WorkflowDefinition | None:
        if executions:
            return self.workflow_compiler.compile_from_history(task_class, executions)
        return self.workflow_compiler.try_compile(task_class)

    def workflow_promote(self, workflow_id: str, status: WorkflowStatus) -> bool:
        return self.workflow_compiler.promote_workflow(workflow_id, status)

    def workflow_advance_maturity(self, workflow_id: str) -> bool:
        return self.workflow_compiler.advance_maturity(workflow_id)

    def workflow_record_use(self, workflow_id: str, success: bool) -> None:
        self.workflow_compiler.record_workflow_use(workflow_id, success)

    def workflow_match(self, task_class: str, inputs: dict[str, Any]) -> tuple[WorkflowDefinition | None, float, str]:
        workflow = self.workflow_compiler.store.find_workflow(task_class, inputs)
        if workflow:
            return workflow, 1.0, "eligible workflow preconditions met"
        return None, 0.0, "no match"

    def workflow_stats(self, task_class: str | None = None) -> dict[str, Any]:
        return self.workflow_compiler.get_workflow_stats(task_class)

    # Skill Model
    def skill_execute(self, skill_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
        return self.skill_model.execute_skill(skill_id, inputs)

    def skill_recommend(self, task_class: str, context: dict[str, Any]):
        return self.skill_model.recommend_skill(task_class, context)

    def skill_get(self, skill_id: str):
        return self.skill_model.get_skill(skill_id)

    def skill_list(self):
        return self.skill_model.list_skills()

    def skill_create(self, skill: SkillDefinition):
        self.skill_model.create_skill(skill)

    # Role Classifier
    def role_classify(self, role_id: str) -> tuple[str, dict[str, Any]]:
        decision, factors = self.role_classifier.evaluate_role(role_id)
        return decision.value, factors.to_dict()

    # Autonomy Model
    def autonomy_record_success(self, entity_type: str, entity_id: str,
                                verification_passed: bool = True):
        return self.autonomy_model.record_success(entity_type, entity_id, verification_passed)

    def autonomy_record_failure(self, entity_type: str, entity_id: str):
        return self.autonomy_model.record_failure(entity_type, entity_id)

    def autonomy_record_security(self, entity_type: str, entity_id: str):
        return self.autonomy_model.record_security_incident(entity_type, entity_id)

    def autonomy_record_correction(self, entity_type: str, entity_id: str):
        return self.autonomy_model.record_human_correction(entity_type, entity_id)

    def autonomy_record_scope_violation(self, entity_type: str, entity_id: str):
        return self.autonomy_model.record_scope_violation(entity_type, entity_id)

    def autonomy_can_act(self, entity_type: str, entity_id: str,
                         action_scope: str = "default") -> tuple[bool, str]:
        return self.autonomy_model.can_act_autonomously(entity_type, entity_id, action_scope)

    def autonomy_promote(self, entity_type: str, entity_id: str,
                         target_state: str) -> dict[str, Any] | None:
        from agentos.models import AutonomyState
        record = self.autonomy_model.promote_to(entity_type, entity_id, AutonomyState(target_state))
        return record.to_dict() if record else None

    # Fleet Monitor
    def fleet_process_signal(self, signal: dict[str, Any]) -> list[dict[str, Any]]:
        from agentos.fleet_monitor import FleetSignal
        signal_obj = FleetSignal(**signal)
        return self.fleet_monitor.process_signal(signal_obj)

    def fleet_health(self) -> dict[str, Any]:
        return self.fleet_monitor.get_fleet_health()

    def fleet_incidents(self) -> list[dict[str, Any]]:
        incidents = self.fleet_monitor.get_active_incidents()
        return [i.to_dict() for i in incidents]

    def fleet_resolve_incident(self, incident_id: str, resolution: str,
                               lesson_ref: str | None = None) -> bool:
        return self.fleet_monitor.resolve_incident(incident_id, resolution, lesson_ref)

    # Learned Routing
    def routing_best_executor(self, task_class: str,
                              candidates: list[str]) -> tuple[str | None, dict[str, Any]]:
        return self.learned_router.get_best_executor(task_class, candidates)

    def routing_explain(self, job_id: str) -> dict[str, Any] | None:
        return self.learned_router.get_routing_explanation(job_id)

    # Routines
    def routine_run(self, routine_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return self.routine_engine.run_routine(routine_id, params)

    def routine_list(self, enabled_only: bool = True):
        return self.routine_engine.list_routines(enabled_only)

    def routine_enable(self, routine_id: str, enabled: bool = True) -> bool:
        return self.routine_engine.enable_routine(routine_id, enabled)

    def record_human_attention(self, kind: str, reason: str) -> dict[str, Any]:
        normalized = str(reason or "").upper()
        if normalized not in HUMAN_ATTENTION_REASONS:
            raise AgentOSError(
                "human attention requires a valid reason: "
                + ", ".join(HUMAN_ATTENTION_REASONS)
            )
        self.store.bump_intelligence_counter(f"human_{kind}", 1)
        self.store.bump_intelligence_counter("human_attention_total", 1)
        return {"kind": kind, "reason": normalized, "status": "RECORDED"}

    def human_attention_metrics(self) -> dict[str, Any]:
        counters = self.store.intelligence_counters()
        return {
            "humanQuestions": int(counters.get("human_question", 0)),
            "approvalRequests": int(counters.get("human_approval_request", 0)),
            "avoidableInterruptions": int(counters.get("human_avoidable_interruption", 0)),
            "manualCorrections": int(counters.get("human_manual_correction", 0)),
            "humanEscalations": int(counters.get("human_escalation", 0)),
            "total": int(counters.get("human_attention_total", 0)),
        }

    # Backup & Reconstruction
    def backup_create(self, output_dir: str | None = None) -> str:
        return self.backup_manager.create_backup(output_dir)

    def backup_inspect(self, archive_path: str) -> dict[str, Any]:
        return self.backup_manager.inspect(archive_path)

    def backup_restore(
        self, archive_path: str, verify: bool = True, dry_run: bool = False
    ) -> dict[str, Any]:
        if dry_run:
            return self.backup_manager.restore_dry_run(archive_path)
        manifest = self.backup_manager.restore_backup(archive_path, verify)
        self.__init__(self.home)
        return manifest.to_dict()

    def backup_reconstruct_workforce(self, backup_dir: str) -> dict[str, Any]:
        return self.backup_manager.reconstruct_workforce(backup_dir)

    # Persistent Bot Governor
    def bot_evaluate(self, role_id: str) -> dict[str, Any]:
        plan = self.bot_governor.evaluate_virtual_role(role_id)
        return {
            "role_id": plan.role_id,
            "decision": plan.decision.value,
            "factors": plan.factors.to_dict(),
            "estimated_cost": plan.estimated_cost,
            "required_resources": plan.required_resources,
            "timeline": plan.timeline,
            "approval_required": plan.approval_required,
        }

    def bot_approve(self, role_id: str, approved: bool, approver: str = "") -> dict[str, Any] | None:
        plan = self.bot_governor.approve_materialization(role_id, approved, approver)
        return plan.to_dict() if plan else None

    def bot_pending(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.bot_governor.get_pending_approvals()]
