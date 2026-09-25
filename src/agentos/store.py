"""Lightweight sqlite persistence for AgentOS state.

The store survives process restarts. It uses a single embedded sqlite database
with explicit schema; no external infrastructure is required.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from agentos.federation import ExecutorCell, JobEnvelope, ResultEnvelope
from agentos.policies import redact_secrets, redact_value
from agentos.models import (
    Capability,
    Event,
    Execution,
    ExecutionPlan,
    Failure,
    GapAnalysis,
    IncidentRecord,
    LessonCandidate,
    LessonStatus,
    LowUsageMetrics,
    Objective,
    Pattern,
    QualityEvaluation,
    SkillDefinition,
    VerificationRecord,
    VerificationResult,
    WorkflowDefinition,
    WorkflowStatus,
    new_id,
    utc_now_iso,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS objectives (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'MEDIUM',
    status TEXT NOT NULL DEFAULT 'CREATED',
    constraints_json TEXT NOT NULL DEFAULT '[]',
    context_json TEXT NOT NULL DEFAULT '{}',
    parent_id TEXT,
    parents_data_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    strategy TEXT,
    result_json TEXT,
    verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    next_action TEXT,
    failure_json TEXT
);

CREATE TABLE IF NOT EXISTS capabilities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    operations_json TEXT NOT NULL DEFAULT '[]',
    adapter TEXT NOT NULL DEFAULT '',
    availability INTEGER NOT NULL DEFAULT 1,
    health TEXT NOT NULL DEFAULT 'UNKNOWN',
    source TEXT NOT NULL DEFAULT 'native',
    inputs_json TEXT NOT NULL DEFAULT '{}',
    outputs_json TEXT NOT NULL DEFAULT '{}',
    cost_json TEXT,
    latency_json TEXT,
    permissions_json TEXT,
    environment_json TEXT,
    persistence_json TEXT,
    constraints_json TEXT,
    verification_json TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    readiness TEXT NOT NULL DEFAULT 'UNKNOWN',
    last_probe TEXT,
    last_success TEXT,
    last_failed TEXT,
    execution_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    avg_duration_ms REAL,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS executions (
    id TEXT PRIMARY KEY,
    objective_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'REQUESTED',
    request_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    error_json TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    mode TEXT NOT NULL DEFAULT 'simulated',
    files_changed_json TEXT NOT NULL DEFAULT '[]',
    git_diff_summary TEXT NOT NULL DEFAULT '',
    cost_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    cost_amount_json TEXT NOT NULL DEFAULT '"UNKNOWN"',
    parent_objective_id TEXT,
    authorization_json TEXT NOT NULL DEFAULT '[]',
    result_summary TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    objective_id TEXT,
    execution_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS verification_results (
    id TEXT PRIMARY KEY,
    objective_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    verified INTEGER NOT NULL,
    method TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS failures (
    id TEXT PRIMARY KEY,
    objective_id TEXT NOT NULL,
    execution_id TEXT,
    failure_type TEXT NOT NULL,
    detail TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 1,
    recovered INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_objectives_status ON objectives(status);
CREATE INDEX IF NOT EXISTS idx_executions_objective ON executions(objective_id);
CREATE INDEX IF NOT EXISTS idx_events_objective ON events(objective_id);
CREATE INDEX IF NOT EXISTS idx_failures_objective ON failures(objective_id);

CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    objective_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    steps_json TEXT NOT NULL DEFAULT '[]',
    rationale TEXT NOT NULL DEFAULT '',
    roles_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patterns (
    id TEXT PRIMARY KEY,
    objective_class TEXT NOT NULL DEFAULT '',
    operation TEXT NOT NULL DEFAULT '',
    strategy TEXT NOT NULL DEFAULT '',
    capability_id TEXT NOT NULL DEFAULT '',
    verified INTEGER NOT NULL DEFAULT 0,
    failure_type TEXT,
    recovery TEXT,
    duration_ms INTEGER,
    lesson TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gaps (
    id TEXT PRIMARY KEY,
    objective_id TEXT NOT NULL,
    operation TEXT NOT NULL DEFAULT '',
    required_capability TEXT NOT NULL DEFAULT '',
    why_required TEXT NOT NULL DEFAULT '',
    alternatives_json TEXT NOT NULL DEFAULT '[]',
    missing_interface TEXT NOT NULL DEFAULT '',
    implementation_paths_json TEXT NOT NULL DEFAULT '[]',
    risk TEXT NOT NULL DEFAULT '',
    verification_plan TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quality (
    id TEXT PRIMARY KEY,
    objective_id TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    grade TEXT NOT NULL DEFAULT 'needs_review',
    findings_json TEXT NOT NULL DEFAULT '[]',
    evaluator TEXT NOT NULL DEFAULT 'heuristic',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plans_objective ON plans(objective_id);
CREATE INDEX IF NOT EXISTS idx_patterns_class ON patterns(objective_class);
CREATE INDEX IF NOT EXISTS idx_gaps_objective ON gaps(objective_id);
CREATE INDEX IF NOT EXISTS idx_quality_objective ON quality(objective_id);

CREATE TABLE IF NOT EXISTS federation_jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'QUEUED',
    envelope_json TEXT NOT NULL DEFAULT '{}',
    decision_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS federation_results (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS federation_cells (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT '',
    cell_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_federation_jobs_status ON federation_jobs(status);
CREATE INDEX IF NOT EXISTS idx_federation_results_job ON federation_results(job_id);

CREATE TABLE IF NOT EXISTS result_cache (
    cache_key TEXT PRIMARY KEY,
    envelope_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    ttl_seconds INTEGER NOT NULL DEFAULT 0,
    hits INTEGER NOT NULL DEFAULT 0,
    usage_class TEXT NOT NULL DEFAULT 'cached',
    stability_class TEXT NOT NULL DEFAULT 'dynamic'
);

CREATE INDEX IF NOT EXISTS idx_result_cache_created_at ON result_cache(created_at);

CREATE TABLE IF NOT EXISTS mcp_candidates (
    id TEXT PRIMARY KEY,
    candidate_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mcp_candidates_updated ON mcp_candidates(updated_at);

CREATE TABLE IF NOT EXISTS ecosystem_candidates (
    id TEXT PRIMARY KEY,
    candidate_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ecosystem_candidates_updated ON ecosystem_candidates(updated_at);

CREATE TABLE IF NOT EXISTS workflow_catalog (
    id TEXT PRIMARY KEY,
    workflow_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workflow_catalog_updated ON workflow_catalog(updated_at);

CREATE TABLE IF NOT EXISTS benchmark_runs (
    id TEXT PRIMARY KEY,
    executor TEXT NOT NULL DEFAULT '',
    workload TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_benchmark_runs_executor ON benchmark_runs(executor);

CREATE TABLE IF NOT EXISTS usage_snapshots (
    id TEXT PRIMARY KEY,
    snapshot_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_ledger (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'manual',
    account_cell TEXT,
    entry_json TEXT NOT NULL DEFAULT '{}',
    as_of TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_ledger_provider ON usage_ledger(provider);

CREATE TABLE IF NOT EXISTS routing_outcomes (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL DEFAULT '',
    decision_json TEXT NOT NULL DEFAULT '{}',
    cost_json TEXT NOT NULL DEFAULT '{}',
    chosen TEXT,
    outcome TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_routing_outcomes_job ON routing_outcomes(job_id);

CREATE TABLE IF NOT EXISTS job_verifications (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    worker TEXT NOT NULL DEFAULT '',
    verifier TEXT,
    verification_type TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'UNVERIFIED',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_verifications_job ON job_verifications(job_id);

CREATE TABLE IF NOT EXISTS intelligence_cache (
    cache_key TEXT PRIMARY KEY,
    entry_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0,
    ttl_seconds INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_intelligence_cache_created ON intelligence_cache(created_at);

CREATE TABLE IF NOT EXISTS task_fingerprints (
    fingerprint TEXT PRIMARY KEY,
    task_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_fingerprints_created ON task_fingerprints(created_at);

CREATE TABLE IF NOT EXISTS efficiency_counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS verification_records (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    verification_type TEXT NOT NULL,
    verifier TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    result TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    failure_reason TEXT,
    recommendation TEXT
);

CREATE INDEX IF NOT EXISTS idx_verification_records_task ON verification_records(task_id);

CREATE TABLE IF NOT EXISTS lessons (
    id TEXT PRIMARY KEY,
    task_class TEXT NOT NULL,
    scope TEXT NOT NULL,
    symptom TEXT NOT NULL,
    root_cause TEXT NOT NULL,
    lesson TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.0,
    recommended_change TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'CANDIDATE',
    created_at TEXT NOT NULL,
    last_validated_at TEXT,
    times_observed INTEGER NOT NULL DEFAULT 1,
    times_applied INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_lessons_task_class ON lessons(task_class);
CREATE INDEX IF NOT EXISTS idx_lessons_status ON lessons(status);

CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    task_class TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    inputs_json TEXT NOT NULL DEFAULT '{}',
    preconditions_json TEXT NOT NULL DEFAULT '[]',
    steps_json TEXT NOT NULL DEFAULT '[]',
    capability_requirements_json TEXT NOT NULL DEFAULT '[]',
    preferred_executors_json TEXT NOT NULL DEFAULT '[]',
    fallback_executors_json TEXT NOT NULL DEFAULT '[]',
    cache_policy TEXT NOT NULL DEFAULT 'exact',
    approval_policy TEXT NOT NULL DEFAULT 'none',
    verification_policy TEXT NOT NULL DEFAULT 'test',
    outputs_json TEXT NOT NULL DEFAULT '{}',
    success_evidence_json TEXT NOT NULL DEFAULT '[]',
    failure_handling_json TEXT NOT NULL DEFAULT '{}',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'DRAFT',
    maturity TEXT NOT NULL DEFAULT 'FIRST_RUN',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workflows_task_class ON workflows(task_class);
CREATE INDEX IF NOT EXISTS idx_workflows_status ON workflows(status);

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    objective TEXT NOT NULL,
    inputs_json TEXT NOT NULL DEFAULT '{}',
    constraints_json TEXT NOT NULL DEFAULT '[]',
    decision_rules_json TEXT NOT NULL DEFAULT '[]',
    tools_json TEXT NOT NULL DEFAULT '[]',
    outputs_json TEXT NOT NULL DEFAULT '{}',
    verification TEXT NOT NULL DEFAULT '',
    approval_boundary TEXT NOT NULL DEFAULT '',
    failure_handling_json TEXT NOT NULL DEFAULT '{}',
    context_refs_json TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skills_objective ON skills(objective);

CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    affected_jobs_json TEXT NOT NULL DEFAULT '[]',
    affected_executors_json TEXT NOT NULL DEFAULT '[]',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    probable_cause TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'OPEN',
    mitigation TEXT NOT NULL DEFAULT '',
    resolution TEXT NOT NULL DEFAULT '',
    lesson_ref TEXT
);

CREATE INDEX IF NOT EXISTS idx_incidents_signature ON incidents(signature);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);

CREATE TABLE IF NOT EXISTS fleet_suppressions (
    job_id TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executor_metrics (
    id TEXT PRIMARY KEY,
    executor TEXT NOT NULL,
    runtime TEXT NOT NULL DEFAULT '',
    model_provider TEXT NOT NULL DEFAULT '',
    task_class TEXT NOT NULL DEFAULT '',
    job_id TEXT NOT NULL DEFAULT '',
    success INTEGER NOT NULL DEFAULT 0,
    verification_result TEXT NOT NULL DEFAULT 'UNVERIFIED',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    resource_cost TEXT NOT NULL DEFAULT 'UNKNOWN',
    human_correction INTEGER NOT NULL DEFAULT 0,
    context_size INTEGER NOT NULL DEFAULT 0,
    final_outcome TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_executor_metrics_executor ON executor_metrics(executor);
CREATE INDEX IF NOT EXISTS idx_executor_metrics_task_class ON executor_metrics(task_class);

CREATE TABLE IF NOT EXISTS autonomy_records (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'DISCOVERED',
    successful_verified_tasks INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    security_incidents INTEGER NOT NULL DEFAULT 0,
    human_corrections INTEGER NOT NULL DEFAULT 0,
    scope_violations INTEGER NOT NULL DEFAULT 0,
    verification_pass_rate REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_autonomy_records_entity ON autonomy_records(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS routines (
    id TEXT PRIMARY KEY,
    trigger TEXT NOT NULL,
    cheap_detector TEXT NOT NULL,
    change_condition TEXT NOT NULL DEFAULT '',
    workflow_id TEXT,
    early_exit TEXT NOT NULL DEFAULT 'NO_ACTION',
    report_policy TEXT NOT NULL DEFAULT 'delta',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_routines_trigger ON routines(trigger);

CREATE TABLE IF NOT EXISTS routine_baselines (
    routine_id TEXT PRIMARY KEY,
    baseline_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backup_metadata (
    id TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backup_metadata_created ON backup_metadata(created_at);

CREATE TABLE IF NOT EXISTS job_traces (
    objective_id TEXT PRIMARY KEY,
    trace_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS job_trace_history (
    id TEXT PRIMARY KEY,
    objective_id TEXT NOT NULL,
    trace_json TEXT NOT NULL,
    archived_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_job_traces_updated ON job_traces(updated_at);

CREATE TABLE IF NOT EXISTS persistence_decisions (
    id TEXT PRIMARY KEY,
    role_id TEXT NOT NULL,
    task_frequency INTEGER NOT NULL DEFAULT 0,
    persistent_context_value REAL NOT NULL DEFAULT 0.0,
    computer_affinity REAL NOT NULL DEFAULT 0.0,
    browser_session_affinity REAL NOT NULL DEFAULT 0.0,
    unique_permissions INTEGER NOT NULL DEFAULT 0,
    supervisory_value REAL NOT NULL DEFAULT 0.0,
    workflow_repeatability REAL NOT NULL DEFAULT 0.0,
    can_skill_replace INTEGER NOT NULL DEFAULT 0,
    can_workflow_replace INTEGER NOT NULL DEFAULT 0,
    can_ephemeral_worker_replace INTEGER NOT NULL DEFAULT 0,
    measured_usage_cost TEXT NOT NULL DEFAULT 'UNKNOWN',
    decision TEXT NOT NULL,
    approval_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED',
    approved_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_persistence_decisions_role ON persistence_decisions(role_id);
"""


def _dumps(value: Any) -> str:
    return json.dumps(redact_value(value), default=str)


def _redact_text(value: Any) -> Any:
    if value is None:
        return None
    return redact_secrets(str(value))


class Store:
    """Owns the sqlite connection and row <-> model translation."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._all_connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.Lock()
        self._create_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        with self._connections_lock:
            self._all_connections.add(conn)
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        """One connection per thread (sqlite objects are thread-affine).

        The API server, parallel plan execution and tests all touch the
        store from worker threads; each gets its own connection to the
        same database file (WAL mode keeps this consistent).
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def _create_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        # Migrate pre-existing databases that lack parents_data_json.
        try:
            self.conn.execute(
                "ALTER TABLE objectives ADD COLUMN parents_data_json "
                "TEXT NOT NULL DEFAULT '{}'"
            )
            self.conn.commit()
        except Exception:
            pass
        # Migrate pre-existing databases that lack the readiness ledger.
        for column_ddl in (
            "readiness TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "last_probe TEXT",
            "last_success TEXT",
            "last_failed TEXT",
            "execution_count INTEGER NOT NULL DEFAULT 0",
            "success_count INTEGER NOT NULL DEFAULT 0",
            "failure_count INTEGER NOT NULL DEFAULT 0",
            "avg_duration_ms REAL",
            "last_error TEXT",
        ):
            name = column_ddl.split(" ", 1)[0]
            try:
                self.conn.execute(
                    f"ALTER TABLE capabilities ADD COLUMN {column_ddl}"
                )
                self.conn.commit()
            except Exception:
                # Column already exists — verify it is really there so a
                # half-migrated database fails loudly instead of silently.
                cols = [
                    r[1]
                    for r in self.conn.execute(
                        "PRAGMA table_info(capabilities)"
                    ).fetchall()
                ]
                if name not in cols:
                    raise
        # Migrate pre-existing databases that lack the execution record fields.
        for column_ddl in (
            "mode TEXT NOT NULL DEFAULT 'simulated'",
            "files_changed_json TEXT NOT NULL DEFAULT '[]'",
            "git_diff_summary TEXT NOT NULL DEFAULT ''",
            "cost_status TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "cost_amount_json TEXT NOT NULL DEFAULT '\"UNKNOWN\"'",
            "parent_objective_id TEXT",
            "authorization_json TEXT NOT NULL DEFAULT '[]'",
            "result_summary TEXT NOT NULL DEFAULT ''",
        ):
            name = column_ddl.split(" ", 1)[0]
            try:
                self.conn.execute(
                    f"ALTER TABLE executions ADD COLUMN {column_ddl}"
                )
                self.conn.commit()
            except Exception:
                # Column already exists — verify it is really there so a
                # half-migrated database fails loudly instead of silently.
                cols = [
                    r[1]
                    for r in self.conn.execute(
                        "PRAGMA table_info(executions)"
                    ).fetchall()
                ]
                if name not in cols:
                    raise
        for column_ddl in (
            "job_id TEXT NOT NULL DEFAULT ''",
        ):
            name = column_ddl.split(" ", 1)[0]
            try:
                self.conn.execute(
                    f"ALTER TABLE executor_metrics ADD COLUMN {column_ddl}"
                )
                self.conn.commit()
            except Exception:
                cols = [
                    r[1]
                    for r in self.conn.execute(
                        "PRAGMA table_info(executor_metrics)"
                    ).fetchall()
                ]
                if name not in cols:
                    raise
        for column_ddl in (
            "approval_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED'",
            "approved_by TEXT NOT NULL DEFAULT ''",
        ):
            name = column_ddl.split(" ", 1)[0]
            try:
                self.conn.execute(
                    f"ALTER TABLE persistence_decisions ADD COLUMN {column_ddl}"
                )
                self.conn.commit()
            except Exception:
                cols = [
                    r[1]
                    for r in self.conn.execute(
                        "PRAGMA table_info(persistence_decisions)"
                    ).fetchall()
                ]
                if name not in cols:
                    raise

    def close(self) -> None:
        with self._connections_lock:
            connections = list(self._all_connections)
            self._all_connections.clear()
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        if hasattr(self._local, "conn"):
            del self._local.conn

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # objectives
    # ------------------------------------------------------------------
    def save_objective(self, objective: Objective) -> None:
        self.conn.execute(
            """
            INSERT INTO objectives (
                id, title, description, priority, status, constraints_json,
                context_json, parent_id, parents_data_json, created_at, updated_at, strategy,
                result_json, verification_status, evidence_json, next_action,
                failure_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                priority=excluded.priority,
                status=excluded.status,
                constraints_json=excluded.constraints_json,
                context_json=excluded.context_json,
                parent_id=excluded.parent_id,
                parents_data_json=excluded.parents_data_json,
                updated_at=excluded.updated_at,
                strategy=excluded.strategy,
                result_json=excluded.result_json,
                verification_status=excluded.verification_status,
                evidence_json=excluded.evidence_json,
                next_action=excluded.next_action,
                failure_json=excluded.failure_json
            """,
            (
                objective.id,
                redact_secrets(objective.title),
                redact_secrets(objective.description),
                objective.priority.value,
                objective.status.value,
                _dumps(objective.constraints),
                _dumps(objective.context),
                objective.parent_id,
                _dumps(objective.parents_data),
                objective.created_at,
                objective.updated_at,
                objective.strategy,
                _dumps(objective.result) if objective.result is not None else None,
                objective.verification_status.value,
                _dumps([e.to_dict() for e in objective.evidence]),
                redact_secrets(objective.next_action),
                _dumps(objective.failure) if objective.failure is not None else None,
            ),
        )
        self.conn.commit()

    def get_objective(self, objective_id: str) -> Objective | None:
        row = self.conn.execute(
            "SELECT * FROM objectives WHERE id=?", (objective_id,)
        ).fetchone()
        return self._objective_from_row(row) if row else None

    def list_objectives(
        self, status: str | None = None, parent_id: str | None = None
    ) -> list[Objective]:
        sql = "SELECT * FROM objectives"
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if parent_id is not None:
            clauses.append("parent_id=?")
            params.append(parent_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._objective_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # capabilities
    # ------------------------------------------------------------------
    def save_capability(self, capability: Capability) -> None:
        self.conn.execute(
            """
            INSERT INTO capabilities (
                id, name, type, description, operations_json, adapter,
                availability, health, source, inputs_json, outputs_json,
                cost_json, latency_json, permissions_json, environment_json,
                persistence_json, constraints_json, verification_json, config_json,
                readiness, last_probe, last_success, last_failed,
                execution_count, success_count, failure_count,
                avg_duration_ms, last_error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                type=excluded.type,
                description=excluded.description,
                operations_json=excluded.operations_json,
                adapter=excluded.adapter,
                availability=excluded.availability,
                health=excluded.health,
                source=excluded.source,
                inputs_json=excluded.inputs_json,
                outputs_json=excluded.outputs_json,
                cost_json=excluded.cost_json,
                latency_json=excluded.latency_json,
                permissions_json=excluded.permissions_json,
                environment_json=excluded.environment_json,
                persistence_json=excluded.persistence_json,
                constraints_json=excluded.constraints_json,
                verification_json=excluded.verification_json,
                config_json=excluded.config_json,
                readiness=excluded.readiness,
                last_probe=excluded.last_probe,
                last_success=excluded.last_success,
                last_failed=excluded.last_failed,
                execution_count=excluded.execution_count,
                success_count=excluded.success_count,
                failure_count=excluded.failure_count,
                avg_duration_ms=excluded.avg_duration_ms,
                last_error=excluded.last_error
            """,
            (
                capability.id,
                _redact_text(capability.name),
                capability.type.value,
                _redact_text(capability.description),
                _dumps(capability.operations),
                _redact_text(capability.adapter),
                int(capability.availability),
                capability.health.value,
                _redact_text(capability.source),
                _dumps(capability.inputs),
                _dumps(capability.outputs),
                _dumps(capability.cost) if capability.cost is not None else None,
                _dumps(capability.latency) if capability.latency is not None else None,
                (
                    _dumps(capability.permissions)
                    if capability.permissions is not None
                    else None
                ),
                (
                    _dumps(capability.environment)
                    if capability.environment is not None
                    else None
                ),
                (
                    _dumps(capability.persistence)
                    if capability.persistence is not None
                    else None
                ),
                (
                    _dumps(capability.constraints)
                    if capability.constraints is not None
                    else None
                ),
                _dumps(capability.verification or {}),
                _dumps(capability.config),
                capability.readiness,
                capability.last_probe,
                capability.last_success,
                capability.last_failed,
                capability.execution_count,
                capability.success_count,
                capability.failure_count,
                capability.avg_duration_ms,
                redact_secrets(capability.last_error),
            ),
        )
        self.conn.commit()

    def get_capability(self, capability_id: str) -> Capability | None:
        row = self.conn.execute(
            "SELECT * FROM capabilities WHERE id=?", (capability_id,)
        ).fetchone()
        return self._capability_from_row(row) if row else None

    def list_capabilities(self) -> list[Capability]:
        rows = self.conn.execute("SELECT * FROM capabilities ORDER BY id").fetchall()
        return [self._capability_from_row(r) for r in rows]

    def delete_capability(self, capability_id: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM capabilities WHERE id=?", (capability_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # executions
    # ------------------------------------------------------------------
    def save_execution(self, execution: Execution) -> None:
        self.conn.execute(
            """
            INSERT INTO executions (
                id, objective_id, capability_id, operation, status,
                request_json, output_json, error_json, attempt, started_at,
                finished_at, evidence_json, mode, files_changed_json,
                git_diff_summary, cost_status, cost_amount_json,
                parent_objective_id, authorization_json, result_summary
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                output_json=excluded.output_json,
                error_json=excluded.error_json,
                attempt=excluded.attempt,
                finished_at=excluded.finished_at,
                evidence_json=excluded.evidence_json,
                mode=excluded.mode,
                files_changed_json=excluded.files_changed_json,
                git_diff_summary=excluded.git_diff_summary,
                cost_status=excluded.cost_status,
                cost_amount_json=excluded.cost_amount_json,
                parent_objective_id=excluded.parent_objective_id,
                authorization_json=excluded.authorization_json,
                result_summary=excluded.result_summary
            """,
            (
                execution.id,
                execution.objective_id,
                execution.capability_id,
                execution.operation,
                execution.status.value,
                _dumps(execution.request),
                _dumps(execution.output),
                _dumps(execution.error) if execution.error is not None else None,
                execution.attempt,
                execution.started_at,
                execution.finished_at,
                _dumps([e.to_dict() for e in execution.evidence]),
                execution.mode,
                _dumps(execution.files_changed),
                execution.git_diff_summary,
                execution.cost_status,
                _dumps(execution.cost_amount),
                execution.parent_objective_id,
                _dumps(execution.authorization),
                redact_secrets(execution.result_summary),
            ),
        )
        self.conn.commit()

    def get_execution(self, execution_id: str) -> Execution | None:
        row = self.conn.execute(
            "SELECT * FROM executions WHERE id=?", (execution_id,)
        ).fetchone()
        return self._execution_from_row(row) if row else None

    def list_executions(self, objective_id: str | None = None) -> list[Execution]:
        sql = "SELECT * FROM executions"
        params: list[Any] = []
        if objective_id:
            sql += " WHERE objective_id=?"
            params.append(objective_id)
        sql += " ORDER BY started_at DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._execution_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def save_event(self, event: Event) -> None:
        self.conn.execute(
            """
            INSERT INTO events (
                id, event_type, objective_id, execution_id, payload_json, created_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                event.id,
                event.event_type.value,
                event.objective_id,
                event.execution_id,
                _dumps(event.payload),
                event.created_at,
            ),
        )
        self.conn.commit()

    def save_events(self, events: Iterable[Event]) -> None:
        for event in events:
            self.save_event(event)

    def list_events(
        self, objective_id: str | None = None, limit: int = 200
    ) -> list[Event]:
        sql = "SELECT * FROM events"
        params: list[Any] = []
        if objective_id:
            sql += " WHERE objective_id=?"
            params.append(objective_id)
        sql += " ORDER BY created_at ASC, id ASC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [self._event_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # verification results
    # ------------------------------------------------------------------
    def save_verification(self, result: VerificationResult) -> None:
        self.conn.execute(
            """
            INSERT INTO verification_results (
                id, objective_id, execution_id, verified, method,
                evidence_json, created_at
            ) VALUES (?,?,?,?,?,?,?)
            """,
            (
                result.id,
                result.objective_id,
                result.execution_id,
                int(result.verified),
                result.method,
                _dumps([e.to_dict() for e in result.evidence]),
                result.created_at,
            ),
        )
        self.conn.commit()

    def list_verifications(self, objective_id: str | None = None) -> list[VerificationResult]:
        sql = "SELECT * FROM verification_results"
        params: list[Any] = []
        if objective_id:
            sql += " WHERE objective_id=?"
            params.append(objective_id)
        sql += " ORDER BY created_at ASC"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._verification_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # failures
    # ------------------------------------------------------------------
    def save_failure(self, failure: Failure) -> None:
        self.conn.execute(
            """
            INSERT INTO failures (
                id, objective_id, execution_id, failure_type, detail,
                attempt, recovered, created_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                failure.id,
                failure.objective_id,
                failure.execution_id,
                failure.failure_type,
                redact_secrets(failure.detail),
                failure.attempt,
                int(failure.recovered),
                failure.created_at,
            ),
        )
        self.conn.commit()

    def mark_failures_recovered(self, objective_id: str) -> int:
        cur = self.conn.execute(
            "UPDATE failures SET recovered=1 WHERE objective_id=?", (objective_id,)
        )
        self.conn.commit()
        return cur.rowcount

    def list_failures(self, objective_id: str | None = None) -> list[Failure]:
        sql = "SELECT * FROM failures"
        params: list[Any] = []
        if objective_id:
            sql += " WHERE objective_id=?"
            params.append(objective_id)
        sql += " ORDER BY created_at ASC"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._failure_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # plans
    # ------------------------------------------------------------------
    def save_plan(self, plan: ExecutionPlan) -> None:
        import json as _json

        self.conn.execute(
            """
            INSERT INTO plans (
                id, objective_id, strategy, steps_json, rationale,
                roles_json, created_at
            ) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                strategy=excluded.strategy,
                steps_json=excluded.steps_json,
                rationale=excluded.rationale,
                roles_json=excluded.roles_json
            """,
            (
                plan.id,
                plan.objective_id,
                plan.strategy.value,
                _json.dumps([s.to_dict() for s in plan.steps], default=str),
                plan.rationale,
                _json.dumps([r.value for r in plan.roles]),
                plan.created_at,
            ),
        )
        self.conn.commit()

    def list_plans(self, objective_id: str | None = None) -> list[ExecutionPlan]:
        sql = "SELECT * FROM plans"
        params: list[Any] = []
        if objective_id:
            sql += " WHERE objective_id=?"
            params.append(objective_id)
        sql += " ORDER BY created_at ASC"
        rows = self.conn.execute(sql, params).fetchall()
        plans: list[ExecutionPlan] = []
        for row in rows:
            plans.append(
                ExecutionPlan.from_dict(
                    {
                        "id": row["id"],
                        "objective_id": row["objective_id"],
                        "strategy": row["strategy"],
                        "steps": self._loads(row["steps_json"], []),
                        "rationale": row["rationale"],
                        "roles": self._loads(row["roles_json"], []),
                        "created_at": row["created_at"],
                    }
                )
            )
        return plans

    # ------------------------------------------------------------------
    # patterns (learning layer)
    # ------------------------------------------------------------------
    def save_pattern(self, pattern: Pattern) -> None:
        self.conn.execute(
            """
            INSERT INTO patterns (
                id, objective_class, operation, strategy, capability_id,
                verified, failure_type, recovery, duration_ms, lesson,
                created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                pattern.id,
                _redact_text(pattern.objective_class),
                _redact_text(pattern.operation),
                _redact_text(pattern.strategy),
                _redact_text(pattern.capability_id),
                int(pattern.verified),
                _redact_text(pattern.failure_type),
                _redact_text(pattern.recovery),
                pattern.duration_ms,
                _redact_text(pattern.lesson),
                pattern.created_at,
            ),
        )
        self.conn.commit()

    def list_patterns(
        self, objective_class: str | None = None, limit: int = 500
    ) -> list[Pattern]:
        sql = "SELECT * FROM patterns"
        params: list[Any] = []
        if objective_class:
            sql += " WHERE objective_class=?"
            params.append(objective_class)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [
            Pattern.from_dict(
                {
                    "id": r["id"],
                    "objective_class": r["objective_class"],
                    "operation": r["operation"],
                    "strategy": r["strategy"],
                    "capability_id": r["capability_id"],
                    "verified": r["verified"],
                    "failure_type": r["failure_type"],
                    "recovery": r["recovery"],
                    "duration_ms": r["duration_ms"],
                    "lesson": r["lesson"],
                    "created_at": r["created_at"],
                }
            )
            for r in rows
        ]

    def capability_stats(self, operation: str) -> dict[str, dict[str, int]]:
        rows = self.conn.execute(
            """
            SELECT capability_id, verified, COUNT(*) AS n FROM patterns
            WHERE operation=? GROUP BY capability_id, verified
            """,
            (operation,),
        ).fetchall()
        stats: dict[str, dict[str, int]] = {}
        for row in rows:
            entry = stats.setdefault(row["capability_id"], {"runs": 0, "verified": 0})
            entry["runs"] += int(row["n"])
            if row["verified"]:
                entry["verified"] += int(row["n"])
        return stats

    # ------------------------------------------------------------------
    # gaps
    # ------------------------------------------------------------------
    def save_gap(self, gap: GapAnalysis) -> None:
        self.conn.execute(
            """
            INSERT INTO gaps (
                id, objective_id, operation, required_capability,
                why_required, alternatives_json, missing_interface,
                implementation_paths_json, risk, verification_plan, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                required_capability=excluded.required_capability,
                why_required=excluded.why_required,
                alternatives_json=excluded.alternatives_json,
                missing_interface=excluded.missing_interface,
                implementation_paths_json=excluded.implementation_paths_json,
                risk=excluded.risk,
                verification_plan=excluded.verification_plan
            """,
            (
                gap.id,
                gap.objective_id,
                _redact_text(gap.operation),
                _redact_text(gap.required_capability),
                _redact_text(gap.why_required),
                _dumps(gap.alternatives),
                _redact_text(gap.missing_interface),
                _dumps(gap.implementation_paths),
                _redact_text(gap.risk),
                _redact_text(gap.verification_plan),
                gap.created_at,
            ),
        )
        self.conn.commit()

    def list_gaps(self, objective_id: str | None = None) -> list[GapAnalysis]:
        sql = "SELECT * FROM gaps"
        params: list[Any] = []
        if objective_id:
            sql += " WHERE objective_id=?"
            params.append(objective_id)
        sql += " ORDER BY created_at DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [
            GapAnalysis.from_dict(
                {
                    "id": r["id"],
                    "objective_id": r["objective_id"],
                    "operation": r["operation"],
                    "required_capability": r["required_capability"],
                    "why_required": r["why_required"],
                    "alternatives": self._loads(r["alternatives_json"], []),
                    "missing_interface": r["missing_interface"],
                    "implementation_paths": self._loads(
                        r["implementation_paths_json"], []
                    ),
                    "risk": r["risk"],
                    "verification_plan": r["verification_plan"],
                    "created_at": r["created_at"],
                }
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # quality evaluations
    # ------------------------------------------------------------------
    def save_quality(self, evaluation: QualityEvaluation) -> None:
        self.conn.execute(
            """
            INSERT INTO quality (
                id, objective_id, execution_id, score, grade,
                findings_json, evaluator, created_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                evaluation.id,
                evaluation.objective_id,
                evaluation.execution_id,
                evaluation.score,
                _redact_text(evaluation.grade),
                _dumps(evaluation.findings),
                _redact_text(evaluation.evaluator),
                evaluation.created_at,
            ),
        )
        self.conn.commit()

    def list_quality(self, objective_id: str | None = None) -> list[QualityEvaluation]:
        sql = "SELECT * FROM quality"
        params: list[Any] = []
        if objective_id:
            sql += " WHERE objective_id=?"
            params.append(objective_id)
        sql += " ORDER BY created_at ASC"
        rows = self.conn.execute(sql, params).fetchall()
        return [
            QualityEvaluation.from_dict(
                {
                    "id": r["id"],
                    "objective_id": r["objective_id"],
                    "execution_id": r["execution_id"],
                    "score": r["score"],
                    "grade": r["grade"],
                    "findings": self._loads(r["findings_json"], []),
                    "evaluator": r["evaluator"],
                    "created_at": r["created_at"],
                }
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # federation
    # ------------------------------------------------------------------
    def save_federation_job(
        self,
        job: JobEnvelope,
        status: str,
        decision: dict[str, Any] | None = None,
        result: ResultEnvelope | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO federation_jobs (
                id, status, envelope_json, decision_json, result_json, created_at
            ) VALUES (?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                envelope_json=excluded.envelope_json,
                decision_json=excluded.decision_json,
                result_json=excluded.result_json
            """,
            (
                job.id,
                status,
                _dumps(job.to_dict()),
                _dumps(decision or {}),
                _dumps(result.to_dict()) if result is not None else None,
                job.createdAt,
            ),
        )
        self.conn.commit()

    def get_federation_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM federation_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "status": row["status"],
            "envelope": self._loads(row["envelope_json"], {}),
            "decision": self._loads(row["decision_json"], {}),
            "result": self._loads(row["result_json"], None),
            "created_at": row["created_at"],
        }

    def list_federation_jobs(
        self, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM federation_jobs"
        params: list[Any] = []
        if status:
            sql += " WHERE status=?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(0, limit))
        rows = self.conn.execute(sql, params).fetchall()
        return [
            {
                "id": r["id"],
                "status": r["status"],
                "envelope": self._loads(r["envelope_json"], {}),
                "decision": self._loads(r["decision_json"], {}),
                "result": self._loads(r["result_json"], None),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def save_federation_result(self, result: ResultEnvelope) -> None:
        self.conn.execute(
            """
            INSERT INTO federation_results (
                id, job_id, result_json, created_at
            ) VALUES (?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                job_id=excluded.job_id,
                result_json=excluded.result_json
            """,
            (
                f"{result.jobId}-{result.executor}".replace("/", "_"),
                result.jobId,
                _dumps(result.to_dict()),
                result.completedAt or result.startedAt,
            ),
        )
        self.conn.commit()

    def list_federation_results(
        self, job_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM federation_results"
        params: list[Any] = []
        if job_id:
            sql += " WHERE job_id=?"
            params.append(job_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(0, limit))
        rows = self.conn.execute(sql, params).fetchall()
        return [
            {
                "id": r["id"],
                "job_id": r["job_id"],
                "result": self._loads(r["result_json"], {}),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def save_federation_cell(self, cell: ExecutorCell) -> None:
        self.conn.execute(
            """
            INSERT INTO federation_cells (id, provider, cell_json, updated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                provider=excluded.provider,
                cell_json=excluded.cell_json,
                updated_at=excluded.updated_at
            """,
            (cell.id, cell.provider, _dumps(cell.to_dict()), utc_now_iso()),
        )
        self.conn.commit()

    def list_federation_cells(self) -> list[ExecutorCell]:
        rows = self.conn.execute(
            "SELECT * FROM federation_cells ORDER BY id"
        ).fetchall()
        return [
            ExecutorCell.from_dict(self._loads(r["cell_json"], {})) for r in rows
        ]

    # ------------------------------------------------------------------
    # MCP candidates (inert metadata; discovery never implies trust)
    # ------------------------------------------------------------------
    def save_mcp_candidate(self, candidate: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO mcp_candidates (id, candidate_json, updated_at)
            VALUES (?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                candidate_json=excluded.candidate_json,
                updated_at=excluded.updated_at
            """,
            (
                str(candidate["id"]),
                _dumps(candidate),
                utc_now_iso(),
            ),
        )
        self.conn.commit()

    def get_mcp_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM mcp_candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        if row is None:
            return None
        return self._loads(row["candidate_json"], {})

    def list_mcp_candidates(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM mcp_candidates ORDER BY updated_at DESC"
        ).fetchall()
        return [self._loads(r["candidate_json"], {}) for r in rows]

    # ------------------------------------------------------------------
    # ecosystem-scout candidates (inert discovery metadata; lifecycle in
    # ecosystem_scout.transition — never an executor)
    # ------------------------------------------------------------------
    def save_ecosystem_candidate(self, candidate: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO ecosystem_candidates (id, candidate_json, updated_at)
            VALUES (?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                candidate_json=excluded.candidate_json,
                updated_at=excluded.updated_at
            """,
            (
                str(candidate["id"]),
                _dumps(candidate),
                utc_now_iso(),
            ),
        )
        self.conn.commit()

    def get_ecosystem_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM ecosystem_candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        if row is None:
            return None
        return self._loads(row["candidate_json"], {})

    def list_ecosystem_candidates(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM ecosystem_candidates ORDER BY updated_at DESC"
        ).fetchall()
        return [self._loads(r["candidate_json"], {}) for r in rows]

    # ------------------------------------------------------------------
    # reusable workflow catalog (usage-led records; upgrade only on reuse
    # floors in reusable_workflows.transition — never reheard as certified
    # without the honest no-sandbox note)
    # ------------------------------------------------------------------
    def save_workflow_catalog(self, workflow: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO workflow_catalog (id, workflow_json, updated_at)
            VALUES (?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                workflow_json=excluded.workflow_json,
                updated_at=excluded.updated_at
            """,
            (
                str(workflow["id"]),
                _dumps(workflow),
                utc_now_iso(),
            ),
        )
        self.conn.commit()

    def get_workflow_catalog(self, workflow_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM workflow_catalog WHERE id=?", (workflow_id,)
        ).fetchone()
        if row is None:
            return None
        return self._loads(row["workflow_json"], {})

    def list_workflow_catalog(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM workflow_catalog ORDER BY updated_at DESC"
        ).fetchall()
        return [self._loads(r["workflow_json"], {}) for r in rows]

    # ------------------------------------------------------------------
    # benchmark runs (measured records only; reports never rank)
    # ------------------------------------------------------------------
    def save_benchmark_run(self, record: dict[str, Any]) -> None:
        from agentos.models import new_id, utc_now_iso

        record_id = str(record.get("id") or new_id("bench"))
        record = dict(record)
        record["id"] = record_id
        self.conn.execute(
            """
            INSERT INTO benchmark_runs (id, executor, workload, result_json, created_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                executor=excluded.executor,
                workload=excluded.workload,
                result_json=excluded.result_json,
                created_at=excluded.created_at
            """,
            (
                record_id,
                _redact_text(record.get("executor", "")),
                _redact_text(record.get("workload", "")),
                _dumps(record),
                utc_now_iso(),
            ),
        )
        self.conn.commit()

    def list_benchmark_runs(
        self, executor: str | None = None, workload: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM benchmark_runs"
        clauses: list[str] = []
        params: list[Any] = []
        if executor:
            clauses.append("executor=?")
            params.append(executor)
        if workload:
            clauses.append("workload=?")
            params.append(str(workload).upper())
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(0, int(limit)))
        rows = self.conn.execute(sql, params).fetchall()
        return [self._loads(r["result_json"], {}) for r in rows]

    # ------------------------------------------------------------------
    # result cache (durable, conservative results-store)
    # ------------------------------------------------------------------
    def save_result_cache_entry(
        self,
        cache_key: str,
        envelope_json: str,
        ttl_seconds: int,
        usage_class: str,
        stability_class: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO result_cache (
                cache_key, envelope_json, created_at, ttl_seconds, hits,
                usage_class, stability_class
            ) VALUES (?,?,?,?,0,?,?)
            ON CONFLICT(cache_key) DO UPDATE SET
                envelope_json=excluded.envelope_json,
                created_at=excluded.created_at,
                ttl_seconds=excluded.ttl_seconds,
                usage_class=excluded.usage_class,
                stability_class=excluded.stability_class,
                hits=excluded.hits
            """,
            (
                cache_key,
                envelope_json,
                utc_now_iso(),
                int(ttl_seconds),
                usage_class,
                stability_class,
            ),
        )
        self.conn.commit()

    def get_result_cache_entry(self, cache_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM result_cache WHERE cache_key=?", (cache_key,)
        ).fetchone()
        return self._result_cache_entry_from_row(row) if row else None

    def touch_result_cache_entry(self, cache_key: str) -> bool:
        cur = self.conn.execute(
            "UPDATE result_cache SET hits=hits+1, created_at=? WHERE cache_key=?",
            (utc_now_iso(), cache_key),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def prune_result_cache(
        self, now_iso: str | None = None, limit: int = 1000
    ) -> int:
        """Delete expired result-cache entries, bounded to at most ``limit``
        removals per call. Entries with ttl_seconds <= 0 never expire."""

        now = now_iso or utc_now_iso()
        rows = self.conn.execute(
            "SELECT cache_key, created_at, ttl_seconds FROM result_cache "
            "ORDER BY created_at ASC"
        ).fetchall()
        expired: list[str] = []
        for row in rows:
            if self._result_cache_expired(
                row["created_at"], row["ttl_seconds"], now
            ):
                expired.append(row["cache_key"])
                if len(expired) >= int(limit):
                    break
        if not expired:
            return 0
        placeholders = ",".join("?" for _ in expired)
        self.conn.execute(
            f"DELETE FROM result_cache WHERE cache_key IN ({placeholders})",
            expired,
        )
        self.conn.commit()
        return len(expired)

    def list_result_cache_entries(
        self, limit: int = 100
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM result_cache ORDER BY created_at DESC LIMIT ?",
            (max(0, limit),),
        ).fetchall()
        return [self._result_cache_entry_from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # resource governor: usage snapshots (validated usage, never fabricated)
    # ------------------------------------------------------------------
    def save_usage_snapshot(self, snapshot: dict[str, Any]) -> None:
        provider = str(snapshot.get("provider", "unknown") or "unknown")
        account = str(snapshot.get("accountCell", "") or "")
        row_id = f"{provider}|{account or 'default'}"
        self.conn.execute(
            """
            INSERT INTO usage_snapshots (id, snapshot_json, updated_at)
            VALUES (?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                snapshot_json=excluded.snapshot_json,
                updated_at=excluded.updated_at
            """,
            (row_id, _dumps(snapshot), utc_now_iso()),
        )
        self.conn.commit()

    def list_usage_snapshots(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM usage_snapshots ORDER BY updated_at DESC"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "snapshot": self._loads(r["snapshot_json"], {}),
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # usage ledger (data in, data out — never generated)
    # ------------------------------------------------------------------
    def save_usage_entry(self, entry: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO usage_ledger (
                id, provider, category, account_cell, entry_json, as_of, created_at
            ) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                provider=excluded.provider,
                category=excluded.category,
                account_cell=excluded.account_cell,
                entry_json=excluded.entry_json,
                as_of=excluded.as_of,
                created_at=excluded.created_at
            """,
            (
                str(entry["id"]),
                str(entry.get("provider", "unknown") or "unknown"),
                str(entry.get("category", "manual") or "manual"),
                str(entry.get("accountCell") or ""),
                _dumps(entry),
                entry.get("asOf"),
                str(entry.get("createdAt") or utc_now_iso()),
            ),
        )
        self.conn.commit()

    def list_usage_entries(
        self,
        provider: str | None = None,
        category: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM usage_ledger"
        clauses: list[str] = []
        params: list[Any] = []
        if provider:
            clauses.append("provider=?")
            params.append(provider)
        if category:
            clauses.append("category=?")
            params.append(category)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(0, int(limit)))
        rows = self.conn.execute(sql, params).fetchall()
        return [
            {
                "id": r["id"],
                "entry": self._loads(r["entry_json"], {}),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # routing outcomes (the governor's recorded decisions + results)
    # ------------------------------------------------------------------
    def save_routing_outcome(self, record: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO routing_outcomes (
                id, job_id, decision_json, cost_json, chosen, outcome, created_at
            ) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                job_id=excluded.job_id,
                decision_json=excluded.decision_json,
                cost_json=excluded.cost_json,
                chosen=excluded.chosen,
                outcome=excluded.outcome,
                created_at=excluded.created_at
            """,
            (
                _redact_text(str(record["id"])),
                _redact_text(str(record.get("job_id", "") or "")),
                _dumps(record.get("decision") or {}),
                _dumps(record.get("cost") or {}),
                _redact_text(record.get("chosen")),
                _redact_text(str(record.get("outcome", "PENDING") or "PENDING")),
                _redact_text(str(record.get("created_at") or utc_now_iso())),
            ),
        )
        self.conn.commit()

    def get_routing_outcome(self, outcome_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM routing_outcomes WHERE id=?", (outcome_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "job_id": row["job_id"],
            "decision": self._loads(row["decision_json"], {}),
            "cost": self._loads(row["cost_json"], {}),
            "chosen": row["chosen"],
            "outcome": row["outcome"],
            "created_at": row["created_at"],
        }

    def latest_routing_outcome(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM routing_outcomes WHERE job_id=? ORDER BY created_at "
            "DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "job_id": row["job_id"],
            "decision": self._loads(row["decision_json"], {}),
            "cost": self._loads(row["cost_json"], {}),
            "chosen": row["chosen"],
            "outcome": row["outcome"],
            "created_at": row["created_at"],
        }

    def list_routing_outcomes(
        self, job_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM routing_outcomes"
        params: list[Any] = []
        if job_id:
            sql += " WHERE job_id=?"
            params.append(job_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(0, int(limit)))
        rows = self.conn.execute(sql, params).fetchall()
        return [
            {
                "id": r["id"],
                "job_id": r["job_id"],
                "decision": self._loads(r["decision_json"], {}),
                "cost": self._loads(r["cost_json"], {}),
                "chosen": r["chosen"],
                "outcome": r["outcome"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def routing_outcome_stats(self) -> dict[str, dict[str, int]]:
        """verified-outcome counts per chosen executor (for governor learning).

        Only rows whose ``outcome`` is recorded count; nothing is invented.
        """
        stats: dict[str, dict[str, int]] = {}
        rows = self.conn.execute(
            "SELECT chosen, outcome FROM routing_outcomes WHERE chosen IS NOT NULL"
        ).fetchall()
        for row in rows:
            entry = stats.setdefault(
                str(row["chosen"]), {"runs": 0, "verified": 0, "failed": 0}
            )
            entry["runs"] += 1
            outcome = str(row["outcome"] or "").upper()
            if outcome in ("VERIFIED", "COMPLETED", "SUCCESS", "RESOLVED"):
                entry["verified"] += 1
            elif outcome in ("FAILED", "REFUSED", "BLOCKED", "FAILURE"):
                entry["failed"] += 1
        return stats

    # ------------------------------------------------------------------
    # job verifications (verification-first task model)
    # ------------------------------------------------------------------
    def save_job_verification(self, record: dict[str, Any]) -> None:
        from agentos.models import new_id
        from agentos.task_verification import TaskVerificationStatus

        record_id = str(record.get("id") or new_id("vjob"))
        self.conn.execute(
            """
            INSERT INTO job_verifications (
                id, job_id, worker, verifier, verification_type, status,
                evidence_json, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                job_id=excluded.job_id,
                worker=excluded.worker,
                verifier=excluded.verifier,
                verification_type=excluded.verification_type,
                status=excluded.status,
                evidence_json=excluded.evidence_json,
                updated_at=excluded.updated_at
            """,
            (
                _redact_text(record_id),
                _redact_text(record.get("job_id") or ""),
                _redact_text(record.get("worker") or ""),
                _redact_text(record.get("verifier")),
                _redact_text(record.get("verification_type") or ""),
                _redact_text(record.get("status") or TaskVerificationStatus.UNVERIFIED.value),
                _dumps(record.get("evidence") or []),
                str(record.get("created_at") or utc_now_iso()),
                str(record.get("updated_at") or utc_now_iso()),
            ),
        )
        self.conn.commit()
        return record_id

    def get_job_verification(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM job_verifications WHERE job_id=? ORDER BY updated_at "
            "DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return self._job_verification_from_row(row)

    def list_job_verifications(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM job_verifications ORDER BY updated_at DESC LIMIT ?",
            (max(0, int(limit)),),
        ).fetchall()
        return [self._job_verification_from_row(r) for r in rows]

    def _job_verification_from_row(self, row: Any) -> dict[str, Any]:
        return {
            "id": row["id"],
            "job_id": row["job_id"],
            "worker": row["worker"],
            "verifier": row["verifier"],
            "verification_type": row["verification_type"],
            "status": row["status"],
            "evidence": self._loads(row["evidence_json"], []),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # ------------------------------------------------------------------
    # misc
    # ------------------------------------------------------------------
    def counts(self) -> dict[str, int]:
        def _count(table: str) -> int:
            row = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            return int(row["n"])

        return {
            "objectives": _count("objectives"),
            "executions": _count("executions"),
            "events": _count("events"),
            "capabilities": _count("capabilities"),
            "verifications": _count("verification_results"),
            "failures": _count("failures"),
            "plans": _count("plans"),
            "patterns": _count("patterns"),
            "gaps": _count("gaps"),
            "quality": _count("quality"),
            "federation_jobs": _count("federation_jobs"),
            "federation_results": _count("federation_results"),
            "federation_cells": _count("federation_cells"),
            "usage_snapshots": _count("usage_snapshots"),
            "usage_ledger": _count("usage_ledger"),
            "routing_outcomes": _count("routing_outcomes"),
            "job_verifications": _count("job_verifications"),
        }

    def integrity_ok(self) -> list[str]:
        problems: list[str] = []
        for table in (
            "objectives",
            "capabilities",
            "executions",
            "events",
            "verification_results",
            "failures",
            "usage_snapshots",
            "usage_ledger",
            "routing_outcomes",
            "job_verifications",
        ):
            row = self.conn.execute(f"PRAGMA integrity_check").fetchone()
            if row and str(row[0]) != "ok":
                problems.append(f"{table}: {row[0]}")
        return problems

    # ------------------------------------------------------------------
    # row helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _loads(value: str | None, default: Any) -> Any:
        if value is None:
            return default
        if not isinstance(value, str):
            raise ValueError("persisted JSON field has an invalid type")
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("persisted JSON field is invalid") from exc

    @classmethod
    def _objective_from_row(cls, row: sqlite3.Row) -> Objective:
        try:
            parents_data = cls._loads(row["parents_data_json"], {})
        except (IndexError, KeyError):
            parents_data = {}
        return Objective.from_dict(
            {
                "id": row["id"],
                "title": row["title"],
                "description": row["description"],
                "priority": row["priority"],
                "status": row["status"],
                "constraints": cls._loads(row["constraints_json"], []),
                "context": cls._loads(row["context_json"], {}),
                "parent_id": row["parent_id"],
                "parents_data": parents_data,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "strategy": row["strategy"],
                "result": cls._loads(row["result_json"], None),
                "verification_status": row["verification_status"],
                "evidence": cls._loads(row["evidence_json"], []),
                "next_action": row["next_action"],
                "failure": cls._loads(row["failure_json"], None),
            }
        )

    @classmethod
    def _capability_from_row(cls, row: sqlite3.Row) -> Capability:
        return Capability.from_dict(
            {
                "id": row["id"],
                "name": row["name"],
                "type": row["type"],
                "description": row["description"],
                "operations": cls._loads(row["operations_json"], []),
                "adapter": row["adapter"],
                "availability": bool(row["availability"]),
                "health": row["health"],
                "source": row["source"],
                "inputs": cls._loads(row["inputs_json"], {}),
                "outputs": cls._loads(row["outputs_json"], {}),
                "cost": cls._loads(row["cost_json"], None),
                "latency": cls._loads(row["latency_json"], None),
                "permissions": cls._loads(row["permissions_json"], None),
                "environment": cls._loads(row["environment_json"], None),
                "persistence": cls._loads(row["persistence_json"], None),
                "constraints": cls._loads(row["constraints_json"], None),
                "verification": cls._loads(row["verification_json"], {}),
                "config": cls._loads(row["config_json"], {}),
                "readiness": (
                    row["readiness"] if "readiness" in row.keys() else "UNKNOWN"
                ),
                "last_probe": row["last_probe"] if "last_probe" in row.keys() else None,
                "last_success": (
                    row["last_success"] if "last_success" in row.keys() else None
                ),
                "last_failed": (
                    row["last_failed"] if "last_failed" in row.keys() else None
                ),
                "execution_count": (
                    row["execution_count"] if "execution_count" in row.keys() else 0
                ),
                "success_count": (
                    row["success_count"] if "success_count" in row.keys() else 0
                ),
                "failure_count": (
                    row["failure_count"] if "failure_count" in row.keys() else 0
                ),
                "avg_duration_ms": (
                    row["avg_duration_ms"] if "avg_duration_ms" in row.keys() else None
                ),
                "last_error": row["last_error"] if "last_error" in row.keys() else None,
            }
        )

    @classmethod
    def _execution_from_row(cls, row: sqlite3.Row) -> Execution:
        keys = row.keys()
        return Execution.from_dict(
            {
                "id": row["id"],
                "objective_id": row["objective_id"],
                "capability_id": row["capability_id"],
                "operation": row["operation"],
                "status": row["status"],
                "request": cls._loads(row["request_json"], {}),
                "output": cls._loads(row["output_json"], {}),
                "error": cls._loads(row["error_json"], None),
                "attempt": row["attempt"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "evidence": cls._loads(row["evidence_json"], []),
                "mode": row["mode"] if "mode" in keys else "simulated",
                "files_changed": (
                    cls._loads(row["files_changed_json"], [])
                    if "files_changed_json" in keys
                    else []
                ),
                "git_diff_summary": (
                    row["git_diff_summary"] if "git_diff_summary" in keys else ""
                ),
                "cost_status": (
                    row["cost_status"] if "cost_status" in keys else "UNKNOWN"
                ),
                "cost_amount": (
                    cls._loads(row["cost_amount_json"], "UNKNOWN")
                    if "cost_amount_json" in keys
                    else "UNKNOWN"
                ),
                "parent_objective_id": (
                    row["parent_objective_id"]
                    if "parent_objective_id" in keys
                    else None
                ),
                "authorization": (
                    cls._loads(row["authorization_json"], [])
                    if "authorization_json" in keys
                    else []
                ),
                "result_summary": (
                    row["result_summary"] if "result_summary" in keys else ""
                ),
            }
        )

    @classmethod
    def _event_from_row(cls, row: sqlite3.Row) -> Event:
        return Event.from_dict(
            {
                "id": row["id"],
                "event_type": row["event_type"],
                "objective_id": row["objective_id"],
                "execution_id": row["execution_id"],
                "payload": cls._loads(row["payload_json"], {}),
                "created_at": row["created_at"],
            }
        )

    @classmethod
    def _verification_from_row(cls, row: sqlite3.Row) -> VerificationResult:
        return VerificationResult.from_dict(
            {
                "id": row["id"],
                "objective_id": row["objective_id"],
                "execution_id": row["execution_id"],
                "verified": row["verified"],
                "method": row["method"],
                "evidence": cls._loads(row["evidence_json"], []),
                "created_at": row["created_at"],
            }
        )

    @classmethod
    def _failure_from_row(cls, row: sqlite3.Row) -> Failure:
        return Failure.from_dict(
            {
                "id": row["id"],
                "objective_id": row["objective_id"],
                "execution_id": row["execution_id"],
                "failure_type": row["failure_type"],
                "detail": row["detail"],
                "attempt": row["attempt"],
                "recovered": bool(row["recovered"]),
                "created_at": row["created_at"],
            }
        )

    @classmethod
    def _result_cache_entry_from_row(cls, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "cache_key": row["cache_key"],
            "envelope": cls._loads(row["envelope_json"], {}),
            "envelope_json": row["envelope_json"],
            "created_at": row["created_at"],
            "ttl_seconds": row["ttl_seconds"],
            "hits": row["hits"],
            "usage_class": row["usage_class"],
            "stability_class": row["stability_class"],
        }

    @classmethod
    def _result_cache_expired(
        cls, created_at: str | None, ttl_seconds: int, now_iso: str
    ) -> bool:
        """An entry is expired only when we can positively compute that its
        TTL has elapsed; unparseable timestamps or ttl <= 0 keep it."""
        try:
            ttl = int(ttl_seconds)
        except (TypeError, ValueError):
            return False
        if ttl <= 0:
            return False
        try:
            created = datetime.fromisoformat(str(created_at))
            now = datetime.fromisoformat(str(now_iso))
        except (TypeError, ValueError):
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now - created).total_seconds() >= float(ttl)

    # ------------------------------------------------------------------
    # intelligence cache (shared low-usage efficiency kernel store)
    # ------------------------------------------------------------------
    def save_intelligence_entry(self, entry: dict[str, Any]) -> None:
        cache_key = str(entry.get("cacheKey") or "")
        self.conn.execute(
            """
            INSERT INTO intelligence_cache (cache_key, entry_json, created_at, hits, ttl_seconds)
            VALUES (?,?,?,0,?)
            ON CONFLICT(cache_key) DO UPDATE SET
                entry_json=excluded.entry_json,
                created_at=excluded.created_at,
                ttl_seconds=excluded.ttl_seconds
            """,
            (cache_key, _dumps(entry), utc_now_iso(), int(entry.get("ttl") or 0)),
        )
        self.conn.commit()

    def get_intelligence_entry(self, cache_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT entry_json FROM intelligence_cache WHERE cache_key=?", (cache_key,)
        ).fetchone()
        return self._loads(row["entry_json"], None) if row else None

    def list_intelligence_entries(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT entry_json FROM intelligence_cache ORDER BY created_at DESC LIMIT ?",
            (max(0, int(limit)),),
        ).fetchall()
        return [self._loads(r["entry_json"], {}) for r in rows]

    def count_intelligence_entries(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM intelligence_cache"
        ).fetchone()
        return int(row["n"])

    def save_task_fingerprint(self, task: dict[str, Any]) -> None:
        fingerprint = str(task.get("fingerprint") or "")
        if not fingerprint:
            return
        self.conn.execute(
            """
            INSERT INTO task_fingerprints (fingerprint, task_json, created_at)
            VALUES (?,?,?)
            ON CONFLICT(fingerprint) DO UPDATE SET task_json=excluded.task_json
            """,
            (fingerprint, _dumps(task), utc_now_iso()),
        )
        self.conn.commit()

    def get_task_fingerprint(self, fingerprint: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT task_json FROM task_fingerprints WHERE fingerprint=?",
            (fingerprint,),
        ).fetchone()
        return self._loads(row["task_json"], None) if row else None

    def list_task_fingerprints(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT task_json FROM task_fingerprints ORDER BY created_at DESC LIMIT ?",
            (max(0, int(limit)),),
        ).fetchall()
        return [self._loads(r["task_json"], {}) for r in rows]

    def bump_intelligence_counter(self, name: str, amount: int = 1) -> None:
        self.conn.execute(
            """
            INSERT INTO efficiency_counters (name, value) VALUES (?, ?)
            ON CONFLICT(name) DO UPDATE SET value = value + ?
            """,
            (name, int(amount), int(amount)),
        )
        self.conn.commit()

    def intelligence_counters(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT name, value FROM efficiency_counters"
        ).fetchall()
        return {r["name"]: int(r["value"]) for r in rows}

    # Verification Records
    def save_verification_record(self, record: VerificationRecord) -> None:
        self.conn.execute(
            """
            INSERT INTO verification_records (id, task_id, verification_type, verifier,
                evidence_refs_json, result, confidence, started_at, completed_at,
                failure_reason, recommendation)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                completed_at=excluded.completed_at,
                result=excluded.result,
                confidence=excluded.confidence,
                failure_reason=excluded.failure_reason,
                recommendation=excluded.recommendation
            """,
            (
                _redact_text(record.id),
                _redact_text(record.task_id),
                record.verification_type.value,
                _redact_text(record.verifier),
                _dumps(record.evidence_refs),
                record.result.value,
                record.confidence,
                record.started_at,
                record.completed_at,
                redact_secrets(record.failure_reason)
                if record.failure_reason is not None
                else None,
                redact_secrets(record.recommendation)
                if record.recommendation is not None
                else None,
            ),
        )
        self.conn.commit()

    def get_verification_record(self, record_id: str) -> VerificationRecord | None:
        row = self.conn.execute(
            "SELECT * FROM verification_records WHERE id=?", (record_id,)
        ).fetchone()
        return VerificationRecord.from_dict(dict(row)) if row else None

    def list_verification_records(self, task_id: str, limit: int = 100) -> list[VerificationRecord]:
        rows = self.conn.execute(
            "SELECT * FROM verification_records WHERE task_id=? ORDER BY started_at DESC LIMIT ?",
            (task_id, max(0, int(limit))),
        ).fetchall()
        return [VerificationRecord.from_dict(dict(r)) for r in rows]

    # Lessons
    def save_lesson(self, lesson: LessonCandidate) -> None:
        self.conn.execute(
            """
            INSERT INTO lessons (id, task_class, scope, symptom, root_cause, lesson,
                evidence_refs_json, confidence, recommended_change, status,
                created_at, last_validated_at, times_observed, times_applied)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                confidence=excluded.confidence,
                last_validated_at=excluded.last_validated_at,
                times_observed=excluded.times_observed,
                times_applied=excluded.times_applied
            """,
            (
                lesson.id,
                _redact_text(lesson.task_class),
                _redact_text(lesson.scope),
                _redact_text(lesson.symptom),
                _redact_text(lesson.root_cause),
                _redact_text(lesson.lesson),
                _dumps(lesson.evidence_refs),
                lesson.confidence,
                _redact_text(lesson.recommended_change),
                lesson.status.value,
                lesson.created_at,
                lesson.last_validated_at,
                lesson.times_observed,
                lesson.times_applied,
            ),
        )
        self.conn.commit()

    def get_lesson(self, lesson_id: str) -> LessonCandidate | None:
        row = self.conn.execute(
            "SELECT * FROM lessons WHERE id=?", (lesson_id,)
        ).fetchone()
        return LessonCandidate.from_dict(dict(row)) if row else None

    def list_lessons(self, task_class: str | None = None, status: LessonStatus | None = None,
                     limit: int = 200) -> list[LessonCandidate]:
        query = "SELECT * FROM lessons"
        params = []
        conditions = []
        if task_class:
            conditions.append("task_class=?")
            params.append(task_class)
        if status:
            conditions.append("status=?")
            params.append(status.value)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(0, int(limit)))
        rows = self.conn.execute(query, params).fetchall()
        return [LessonCandidate.from_dict(dict(r)) for r in rows]

    def promote_lesson(self, lesson_id: str, new_status: LessonStatus) -> bool:
        row = self.conn.execute(
            "UPDATE lessons SET status=?, last_validated_at=? WHERE id=?",
            (new_status.value, utc_now_iso(), lesson_id)
        )
        self.conn.commit()
        return row.rowcount > 0

    # Workflows
    def save_workflow(self, workflow: WorkflowDefinition) -> None:
        self.conn.execute(
            """
            INSERT INTO workflows (id, name, task_class, version, inputs_json,
                preconditions_json, steps_json, capability_requirements_json,
                preferred_executors_json, fallback_executors_json, cache_policy,
                approval_policy, verification_policy, outputs_json,
                success_evidence_json, failure_handling_json, metrics_json,
                status, maturity, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                task_class=excluded.task_class,
                version=excluded.version,
                inputs_json=excluded.inputs_json,
                preconditions_json=excluded.preconditions_json,
                steps_json=excluded.steps_json,
                capability_requirements_json=excluded.capability_requirements_json,
                preferred_executors_json=excluded.preferred_executors_json,
                fallback_executors_json=excluded.fallback_executors_json,
                cache_policy=excluded.cache_policy,
                approval_policy=excluded.approval_policy,
                verification_policy=excluded.verification_policy,
                outputs_json=excluded.outputs_json,
                success_evidence_json=excluded.success_evidence_json,
                failure_handling_json=excluded.failure_handling_json,
                metrics_json=excluded.metrics_json,
                status=excluded.status,
                maturity=excluded.maturity,
                updated_at=excluded.updated_at
            """,
            (
                workflow.id,
                _redact_text(workflow.name),
                _redact_text(workflow.task_class),
                workflow.version,
                _dumps(workflow.inputs),
                _dumps(workflow.preconditions),
                _dumps(workflow.steps),
                _dumps(workflow.capability_requirements),
                _dumps(workflow.preferred_executors),
                _dumps(workflow.fallback_executors),
                _redact_text(workflow.cache_policy),
                _redact_text(workflow.approval_policy),
                _redact_text(workflow.verification_policy),
                _dumps(workflow.outputs),
                _dumps(workflow.success_evidence),
                _dumps(workflow.failure_handling),
                _dumps(workflow.metrics),
                workflow.status.value,
                workflow.maturity.value,
                workflow.created_at,
                workflow.updated_at,
            ),
        )
        self.conn.commit()

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition | None:
        row = self.conn.execute(
            "SELECT * FROM workflows WHERE id=?", (workflow_id,)
        ).fetchone()
        return WorkflowDefinition.from_dict(dict(row)) if row else None

    def list_workflows(self, task_class: str | None = None,
                       status: WorkflowStatus | None = None, limit: int = 200) -> list[WorkflowDefinition]:
        query = "SELECT * FROM workflows"
        params = []
        conditions = []
        if task_class:
            conditions.append("task_class=?")
            params.append(task_class)
        if status:
            conditions.append("status=?")
            params.append(status.value)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(0, int(limit)))
        rows = self.conn.execute(query, params).fetchall()
        return [WorkflowDefinition.from_dict(dict(r)) for r in rows]

    def find_workflow(self, task_class: str, inputs: dict[str, Any]) -> WorkflowDefinition | None:
        """Find eligible workflow for task class with valid preconditions."""
        candidates = [
            workflow
            for status in (
                WorkflowStatus.OBSERVED,
                WorkflowStatus.VERIFIED,
                WorkflowStatus.CERTIFIED,
            )
            for workflow in self.list_workflows(task_class=task_class, status=status)
        ]
        for w in candidates:
            preconditions_ok = all(
                self._check_precondition(p, inputs) for p in w.preconditions
            )
            if preconditions_ok:
                return w
        return None

    def _check_precondition(self, precondition: str, inputs: dict[str, Any]) -> bool:
        """Simple precondition check - extendable."""
        if precondition == "cache_available":
            return True
        if precondition.startswith("input:"):
            key = precondition.split(":", 1)[1]
            return key in inputs
        if precondition.startswith("capability_ready:"):
            capability_id = precondition.split(":", 1)[1]
            capability = self.get_capability(capability_id)
            return capability is not None and capability.is_usable()
        return False

    # Skills
    def save_skill(self, skill: SkillDefinition) -> None:
        self.conn.execute(
            """
            INSERT INTO skills (id, objective, inputs_json, constraints_json,
                decision_rules_json, tools_json, outputs_json, verification,
                approval_boundary, failure_handling_json, context_refs_json,
                version, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                objective=excluded.objective,
                inputs_json=excluded.inputs_json,
                constraints_json=excluded.constraints_json,
                decision_rules_json=excluded.decision_rules_json,
                tools_json=excluded.tools_json,
                outputs_json=excluded.outputs_json,
                verification=excluded.verification,
                approval_boundary=excluded.approval_boundary,
                failure_handling_json=excluded.failure_handling_json,
                context_refs_json=excluded.context_refs_json,
                version=excluded.version,
                updated_at=excluded.updated_at
            """,
            (
                skill.id,
                _redact_text(skill.objective),
                _dumps(skill.inputs),
                _dumps(skill.constraints),
                _dumps(skill.decision_rules),
                _dumps(skill.tools),
                _dumps(skill.outputs),
                _redact_text(skill.verification),
                _redact_text(skill.approval_boundary),
                _dumps(skill.failure_handling),
                _dumps(skill.context_refs),
                skill.version,
                skill.created_at,
                skill.updated_at,
            ),
        )
        self.conn.commit()

    def get_skill(self, skill_id: str) -> SkillDefinition | None:
        row = self.conn.execute(
            "SELECT * FROM skills WHERE id=?", (skill_id,)
        ).fetchone()
        return SkillDefinition.from_dict(dict(row)) if row else None

    def list_skills(self, objective: str | None = None, limit: int = 200) -> list[SkillDefinition]:
        query = "SELECT * FROM skills"
        params = []
        if objective:
            query += " WHERE objective=?"
            params.append(objective)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(0, int(limit)))
        rows = self.conn.execute(query, params).fetchall()
        return [SkillDefinition.from_dict(dict(r)) for r in rows]

    # Incidents
    def save_incident(self, incident: IncidentRecord) -> None:
        self.conn.execute(
            """
            INSERT INTO incidents (id, signature, affected_jobs_json,
                affected_executors_json, first_seen, last_seen, probable_cause,
                confidence, status, mitigation, resolution, lesson_ref)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                signature=excluded.signature,
                affected_jobs_json=excluded.affected_jobs_json,
                affected_executors_json=excluded.affected_executors_json,
                first_seen=excluded.first_seen,
                last_seen=excluded.last_seen,
                probable_cause=excluded.probable_cause,
                confidence=excluded.confidence,
                status=excluded.status,
                mitigation=excluded.mitigation,
                resolution=excluded.resolution,
                lesson_ref=excluded.lesson_ref
            """,
            (
                incident.id,
                _redact_text(incident.signature),
                _dumps(incident.affected_jobs),
                _dumps(incident.affected_executors),
                incident.first_seen,
                incident.last_seen,
                _redact_text(incident.probable_cause),
                incident.confidence,
                _redact_text(incident.status),
                _redact_text(incident.mitigation),
                _redact_text(incident.resolution),
                _redact_text(incident.lesson_ref),
            ),
        )
        self.conn.commit()

    def suppress_fleet_jobs(self, incident: IncidentRecord) -> int:
        count = 0
        for job_id in sorted(set(incident.affected_jobs)):
            self.conn.execute(
                "INSERT INTO fleet_suppressions (job_id, incident_id, created_at) "
                "VALUES (?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET "
                "incident_id=excluded.incident_id, created_at=excluded.created_at",
                (str(job_id), incident.id, utc_now_iso()),
            )
            count += 1
        self.conn.commit()
        return count

    def is_fleet_job_suppressed(self, job_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM fleet_suppressions WHERE job_id=?",
            (str(job_id),),
        ).fetchone()
        return row is not None

    def clear_fleet_suppressions(self, incident_id: str) -> int:
        cursor = self.conn.execute(
            "DELETE FROM fleet_suppressions WHERE incident_id=?",
            (str(incident_id),),
        )
        self.conn.commit()
        return cursor.rowcount

    def get_incident(self, incident_id: str) -> IncidentRecord | None:
        row = self.conn.execute(
            "SELECT * FROM incidents WHERE id=?", (incident_id,)
        ).fetchone()
        return IncidentRecord.from_dict(dict(row)) if row else None

    def find_incident_by_signature(self, signature: str) -> IncidentRecord | None:
        row = self.conn.execute(
            "SELECT * FROM incidents WHERE signature=?", (signature,)
        ).fetchone()
        return IncidentRecord.from_dict(dict(row)) if row else None

    def list_incidents(self, status: str | None = None, limit: int = 200) -> list[IncidentRecord]:
        query = "SELECT * FROM incidents"
        params = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY last_seen DESC LIMIT ?"
        params.append(max(0, int(limit)))
        rows = self.conn.execute(query, params).fetchall()
        return [IncidentRecord.from_dict(dict(r)) for r in rows]

    # Executor Metrics
    def record_executor_metric(self, metric: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO executor_metrics (id, executor, runtime, model_provider,
                task_class, job_id, success, verification_result, latency_ms, retry_count,
                resource_cost, human_correction, context_size, final_outcome, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                _redact_text(metric.get("id") or new_id("metric")),
                _redact_text(metric.get("executor", "")),
                _redact_text(metric.get("runtime", "")),
                _redact_text(metric.get("model_provider", "")),
                _redact_text(metric.get("task_class", "")),
                _redact_text(metric.get("job_id", "")),
                int(metric.get("success", 0)),
                _redact_text(metric.get("verification_result", "UNVERIFIED")),
                int(metric.get("latency_ms", 0)),
                int(metric.get("retry_count", 0)),
                _redact_text(metric.get("resource_cost", "UNKNOWN")),
                int(metric.get("human_correction", 0)),
                int(metric.get("context_size", 0)),
                _redact_text(metric.get("final_outcome", "")),
                utc_now_iso(),
            ),
        )
        self.conn.commit()

    def get_executor_metrics(self, executor: str | None = None,
                             task_class: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        query = "SELECT * FROM executor_metrics"
        params = []
        conditions = []
        if executor:
            conditions.append("executor=?")
            params.append(executor)
        if task_class:
            conditions.append("task_class=?")
            params.append(task_class)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(0, int(limit)))
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # Autonomy Records
    def save_autonomy_record(self, record: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO autonomy_records (id, entity_type, entity_id, state,
                successful_verified_tasks, failures, security_incidents,
                human_corrections, scope_violations, verification_pass_rate,
                created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                state=excluded.state,
                successful_verified_tasks=excluded.successful_verified_tasks,
                failures=excluded.failures,
                security_incidents=excluded.security_incidents,
                human_corrections=excluded.human_corrections,
                scope_violations=excluded.scope_violations,
                verification_pass_rate=excluded.verification_pass_rate,
                updated_at=excluded.updated_at
            """,
            (
                record.get("id") or new_id("autonomy"),
                record.get("entity_type", ""),
                record.get("entity_id", ""),
                record.get("state", "DISCOVERED"),
                int(record.get("successful_verified_tasks", 0)),
                int(record.get("failures", 0)),
                int(record.get("security_incidents", 0)),
                int(record.get("human_corrections", 0)),
                int(record.get("scope_violations", 0)),
                float(record.get("verification_pass_rate", 0.0)),
                record.get("created_at") or utc_now_iso(),
                utc_now_iso(),
            ),
        )
        self.conn.commit()

    def get_autonomy_record(self, entity_type: str, entity_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM autonomy_records WHERE entity_type=? AND entity_id=?",
            (entity_type, entity_id)
        ).fetchone()
        return dict(row) if row else None

    # Routines
    def save_routine(self, routine: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO routines (id, trigger, cheap_detector, change_condition,
                workflow_id, early_exit, report_policy, enabled, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                change_condition=excluded.change_condition,
                workflow_id=excluded.workflow_id,
                early_exit=excluded.early_exit,
                report_policy=excluded.report_policy,
                enabled=excluded.enabled,
                updated_at=excluded.updated_at
            """,
            (
                _redact_text(routine.get("id") or new_id("routine")),
                _redact_text(routine.get("trigger", "")),
                _redact_text(routine.get("cheap_detector", "")),
                _redact_text(routine.get("change_condition", "")),
                _redact_text(routine.get("workflow_id")),
                _redact_text(routine.get("early_exit", "NO_ACTION")),
                _redact_text(routine.get("report_policy", "delta")),
                int(routine.get("enabled", 1)),
                routine.get("created_at") or utc_now_iso(),
                utc_now_iso(),
            ),
        )
        self.conn.commit()

    def save_routine_baseline(self, routine_id: str, baseline: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO routine_baselines (routine_id, baseline_json, updated_at) "
            "VALUES (?,?,?) ON CONFLICT(routine_id) DO UPDATE SET "
            "baseline_json=excluded.baseline_json, updated_at=excluded.updated_at",
            (str(routine_id), _dumps(baseline), utc_now_iso()),
        )
        self.conn.commit()

    def get_routine_baseline(self, routine_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT baseline_json FROM routine_baselines WHERE routine_id=?",
            (str(routine_id),),
        ).fetchone()
        return self._loads(row["baseline_json"], {}) if row else {}

    def list_routines(self, enabled_only: bool = True, limit: int = 200) -> list[dict[str, Any]]:
        query = "SELECT * FROM routines"
        params = []
        if enabled_only:
            query += " WHERE enabled=1"
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(0, int(limit)))
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # Backup
    def save_backup_metadata(self, backup: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO backup_metadata (id, version, payload_json, created_at)
            VALUES (?,?,?,?)
            """,
            (
                _redact_text(backup.get("id") or new_id("backup")),
                _redact_text(backup.get("version", "1")),
                _dumps(backup.get("payload", {})),
                backup.get("created_at") or utc_now_iso(),
            ),
        )
        self.conn.commit()

    def save_job_trace(self, trace: dict[str, Any]) -> None:
        objective_id = str(trace.get("taskId") or trace.get("objective_id") or "")
        if not objective_id:
            raise ValueError("job trace requires taskId")
        now = utc_now_iso()
        existing = self.conn.execute(
            "SELECT trace_json FROM job_traces WHERE objective_id=?",
            (objective_id,),
        ).fetchone()
        if existing is not None:
            previous = self._loads(existing["trace_json"], {}) or {}
            previous_run = str(previous.get("runId") or "")
            current_run = str(trace.get("runId") or "")
            if previous and previous_run != current_run:
                self.conn.execute(
                    "INSERT INTO job_trace_history (id, objective_id, trace_json, archived_at) "
                    "VALUES (?,?,?,?)",
                    (new_id("trace_history"), objective_id, existing["trace_json"], now),
                )
        self.conn.execute(
            """
            INSERT INTO job_traces (objective_id, trace_json, created_at, updated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(objective_id) DO UPDATE SET
                trace_json=excluded.trace_json,
                updated_at=excluded.updated_at
            """,
            (objective_id, _dumps(trace), now, now),
        )
        self.conn.commit()

    def list_job_trace_history(self, objective_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT trace_json FROM job_trace_history WHERE objective_id=? "
            "ORDER BY archived_at DESC LIMIT ?",
            (objective_id, max(0, int(limit))),
        ).fetchall()
        return [self._loads(row["trace_json"], {}) for row in rows]

    def get_job_trace(self, objective_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT trace_json FROM job_traces WHERE objective_id=?",
            (objective_id,),
        ).fetchone()
        return self._loads(row["trace_json"], None) if row else None

    def list_job_traces(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT trace_json FROM job_traces ORDER BY updated_at DESC LIMIT ?",
            (max(0, int(limit)),),
        ).fetchall()
        return [self._loads(row["trace_json"], {}) for row in rows]

    # Persistence Decisions
    def save_persistence_decision(self, decision: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO persistence_decisions (id, role_id, task_frequency,
                persistent_context_value, computer_affinity, browser_session_affinity,
                unique_permissions, supervisory_value, workflow_repeatability,
                can_skill_replace, can_workflow_replace, can_ephemeral_worker_replace,
                measured_usage_cost, decision, approval_status, approved_by, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                decision.get("id") or new_id("persist"),
                decision.get("role_id", ""),
                int(decision.get("task_frequency", 0)),
                float(decision.get("persistent_context_value", 0.0)),
                float(decision.get("computer_affinity", 0.0)),
                float(decision.get("browser_session_affinity", 0.0)),
                int(decision.get("unique_permissions", 0)),
                float(decision.get("supervisory_value", 0.0)),
                float(decision.get("workflow_repeatability", 0.0)),
                int(decision.get("can_skill_replace", 0)),
                int(decision.get("can_workflow_replace", 0)),
                int(decision.get("can_ephemeral_worker_replace", 0)),
                decision.get("measured_usage_cost", "UNKNOWN"),
                decision.get("decision", "KEEP_VIRTUAL"),
                decision.get("approval_status", "NOT_REQUIRED"),
                decision.get("approved_by", ""),
                utc_now_iso(),
            ),
        )
        self.conn.commit()