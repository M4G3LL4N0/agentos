"""The AgentOS execution engine: the fundamental control loop.

    objective -> inspect state -> discover capabilities -> plan (router:
    strategy + steps + rationale) -> select execution strategy
    (capability+adapter) -> delegate/execute (sequential/parallel/iterative)
    -> verify (incl. independent verification) -> evaluate quality ->
    persist -> recover (bounded, multi-strategy) -> learn (patterns) ->
    determine next action

The engine is provider-neutral: it never references a specific provider.
All provider-specific behaviour lives behind adapters. Autonomy means
reason -> act -> verify -> decide, with explicit termination: stop when
complete, blocked, unsafe, or requiring human judgment.
"""

from __future__ import annotations

import ast
import concurrent.futures
import json
import math
import operator
import re
import time
from dataclasses import dataclass, field
from typing import Any

from agentos.adapters.base import AdapterResult, ExecutionMode, ExecutionRequest
from agentos.decompose import objective_is_unblocked
from agentos.events import EventBus
from agentos.models import (
    Evidence,
    Execution,
    ExecutionPlan,
    ExecutionStatus,
    EventType,
    Failure,
    GapAnalysis,
    Objective,
    ObjectiveState,
    Pattern,
    PlanStep,
    QualityEvaluation,
    Report,
    Strategy,
    VerificationResult,
    VerificationRecord,
    VerificationOutcome,
    VerificationType,
    VerificationStatus,
    cost_status,
    new_id,
    normalize_result,
    utc_now_iso,
)
from agentos.efficiency import TaskFingerprint, plan_team, triage
from agentos.intelligence_cache import IntelligenceCache, intcache_key, looks_secret, content_hash
from agentos.verification_engine import VerificationEngine
from agentos.workflow_compiler import WorkflowCompiler
from agentos.coach import Coach
from agentos.learned_routing import LearnedRouter
from agentos.fleet_monitor import FleetMonitor
from agentos.routines import RoutineEngine
from agentos.registry import CapabilityRegistry
from agentos.router import Router
from agentos.policies import redact_secrets, redact_value
from agentos.safety import require_execution_authority
from agentos.store import Store
from agentos.verify import (
    Verifier,
    backoff_delay_seconds,
    classify_failure,
    evaluate_quality,
    retryable,
    suggest_next_action,
)

MAX_PLAN_STEPS = 10
MAX_TIMEOUT_SECONDS = 3600.0
MAX_REVISIONS = 2
MAX_PARALLEL_WORKERS = 4

#: Hard ceiling on recovery attempts for any single ``bounded_recover``
#: call. The effective budget is ``min(max_attempts, ABSOLUTE_CAP)`` with
#: a floor of 1, so recovery is always bounded and can never loop forever
#: no matter what ``max_attempts`` a caller passes.
ABSOLUTE_CAP = 5


def _flatten_files_changed(value: Any) -> list[str]:
    """Flatten the Task 4 change keys into the Execution.files_changed list.

    The engine attaches a ``files_changed`` dict
    (added/modified/deleted/produced/pre_existing) to output; the record
    field is a flat list of paths this run touched (pre-existing files
    are excluded — they were already there).
    """
    if isinstance(value, dict):
        names: list[str] = []
        for key in ("added", "modified", "deleted", "produced"):
            items = value.get(key) or []
            if isinstance(items, (list, tuple, set, frozenset)):
                names.extend(str(item) for item in items)
        return sorted(set(names))
    if isinstance(value, (list, tuple, set, frozenset)):
        return sorted({str(item) for item in value})
    return []


def _execution_summary(
    execution: Execution, result: AdapterResult, capability: Any
) -> str:
    """One short human line describing an attempt (never fabricated costs)."""
    capability_id = getattr(capability, "id", None) or "unknown"
    if result.ok:
        exit_code = (
            execution.output.get("exit_code")
            if isinstance(execution.output, dict)
            else None
        )
        suffix = f" (exit {exit_code})" if exit_code is not None else ""
        return f"{execution.operation} via {capability_id}: ok{suffix}"
    error = result.error or ""
    if not error and isinstance(execution.error, dict):
        error = str(execution.error.get("message") or "")
    return f"{execution.operation} via {capability_id}: failed: {str(error)[:120]}"


class AgentOSError(Exception):
    """Base error for AgentOS control-plane failures."""


@dataclass
class EngineOptions:
    """Tunables for a single engine pass."""

    max_attempts: int = 3
    operation: str | None = None
    timeout_seconds: float = 120.0
    force: bool = False
    # SIMULATED default: LIVE requires explicit authorization (see safety.py).
    mode: ExecutionMode = ExecutionMode.SIMULATED
    authorization: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.max_attempts = max(1, min(int(self.max_attempts), ABSOLUTE_CAP))
        if not math.isfinite(float(self.timeout_seconds)) or float(self.timeout_seconds) <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        self.timeout_seconds = min(float(self.timeout_seconds), MAX_TIMEOUT_SECONDS)


@dataclass
class StepOutcome:
    execution: Execution | None
    verification: VerificationResult | None
    failure_type: str | None
    attempts: int = 0


@dataclass
class RecoveryOutcome:
    """Outcome of one bounded, class-dependent recovery decision.

    Fields: the action taken (``reject`` / ``blocked`` / ``backoff`` /
    ``retry`` / ``correct`` / ``report``), the attempts used (always
    ``<= min(max_attempts, ABSOLUTE_CAP)``), and the terminal state the
    objective is left in.
    """

    action: str
    attempts_used: int
    terminal_state: ObjectiveState
    failure_type: str = "unknown"
    detail: str = ""
    backoff_seconds: list[float] = field(default_factory=list)

    @property
    def action_taken(self) -> str:
        """Alias for ``action`` (the action taken)."""
        return self.action

    @property
    def attempts(self) -> int:
        """Alias for ``attempts_used``."""
        return self.attempts_used


def _missing_credential_label(error_text: str) -> str:
    """Name the missing credential in generic terms (never a secret value)."""
    text = (error_text or "").lower()
    if "api key" in text or "apikey" in text or "api_key" in text:
        return "missing API key"
    if "token" in text:
        return "missing auth token"
    if "credential" in text:
        return "missing credentials"
    if "login" in text or "sign in" in text or "sign-in" in text:
        return "missing login session"
    return "missing authentication"


def _missing_binary_label(error_text: str) -> str:
    """Name the missing executable (a binary name, never a secret)."""
    text = (error_text or "").strip()
    head = text.split(":", 1)[0].strip() if ":" in text else ""
    if head and len(head) <= 40 and "\n" not in head:
        return f"missing executable {head!r}"
    return "missing executable"


def bounded_recover(
    objective: Objective,
    adapter_result: AdapterResult,
    max_attempts: int = 3,
) -> RecoveryOutcome:
    """Decide class-dependent recovery for a failed adapter result.

    Pure decision function: computes the action, the bounded attempt
    budget (``min(max_attempts, ABSOLUTE_CAP)``), and the terminal state
    without executing anything and without sleeping — backoff delays are
    computed via ``backoff_delay_seconds`` and returned for the caller to
    record, never slept here. ``Engine.bounded_recover`` wraps this with
    persistence (every attempt saved, events emitted).
    """
    error_text = adapter_result.error or ""
    exit_code = adapter_result.exit_code
    if exit_code is None and isinstance(adapter_result.output, dict):
        raw_code = adapter_result.output.get("exit_code")
        if isinstance(raw_code, bool):
            pass
        elif isinstance(raw_code, int):
            exit_code = raw_code
    failure_type = classify_failure(error_text or None, exit_code)
    cap = max(1, min(int(max_attempts), ABSOLUTE_CAP))
    redacted = redact_secrets(error_text)[:500] if error_text else "no error text"

    if failure_type in ("AUTH_REQUIRED", "auth"):
        label = _missing_credential_label(error_text)
        return RecoveryOutcome(
            action="blocked",
            attempts_used=1,
            terminal_state=ObjectiveState.BLOCKED,
            failure_type=failure_type,
            # Names what's missing in generic terms; never echoes the raw
            # error text because it may carry a secret value verbatim.
            detail=(
                f"{label}: provide the credential/config (values are never "
                "logged here), then run recover."
            ),
        )
    if failure_type == "BINARY_MISSING":
        label = _missing_binary_label(error_text)
        return RecoveryOutcome(
            action="blocked",
            attempts_used=1,
            terminal_state=ObjectiveState.BLOCKED,
            failure_type=failure_type,
            detail=f"{label}: install or configure it, then run recover.",
        )
    if failure_type == "CONFIGURATION_ERROR":
        return RecoveryOutcome(
            action="blocked",
            attempts_used=1,
            terminal_state=ObjectiveState.BLOCKED,
            failure_type=failure_type,
            detail=(
                "invalid configuration: fix the configuration (values are "
                "never logged here), then run recover."
            ),
        )
    if failure_type == "POLICY_BLOCK":
        return RecoveryOutcome(
            action="reject",
            attempts_used=1,
            terminal_state=ObjectiveState.BLOCKED,
            failure_type=failure_type,
            detail=(
                "rejected by policy: no bypass, no retry. Grant authority via "
                f"policy configuration, then run recover. Evidence: {redacted}"
            ),
        )
    if failure_type in ("RATE_LIMIT", "rate_limit"):
        delays = [backoff_delay_seconds(i) for i in range(1, cap + 1)]
        return RecoveryOutcome(
            action="backoff",
            attempts_used=cap,
            terminal_state=ObjectiveState.FAILED,
            failure_type=failure_type,
            detail=(
                f"rate limited: bounded backoff schedule "
                f"{', '.join(f'{d:g}s' for d in delays)} (computed, not "
                "slept); wait out the window, then run recover. "
                f"Evidence: {redacted}"
            ),
            backoff_seconds=delays,
        )
    if failure_type in ("TIMEOUT", "timeout"):
        return RecoveryOutcome(
            action="retry",
            attempts_used=cap,
            terminal_state=ObjectiveState.FAILED,
            failure_type=failure_type,
            detail=(
                f"timed out: safe bounded retry budget {cap} (hard-capped, "
                "never infinite); inspect failure evidence, then run recover "
                f"with a corrected strategy. Evidence: {redacted}"
            ),
        )
    if failure_type in ("TEST_FAILURE", "BUILD_FAILURE", "TYPECHECK_FAILURE"):
        return RecoveryOutcome(
            action="correct",
            attempts_used=1,
            terminal_state=ObjectiveState.FAILED,
            failure_type=failure_type,
            detail=(
                "returned to agent with corrected instruction: fix the "
                "failing check from the evidence, then run recover. "
                f"Evidence: {redacted}"
            ),
        )
    if failure_type == "CONFLICT":
        return RecoveryOutcome(
            action="report",
            attempts_used=1,
            terminal_state=ObjectiveState.FAILED,
            failure_type=failure_type,
            detail=(
                "conflict reported: no auto-merge, no destructive resolution. "
                f"Resolve manually, then run recover. Evidence: {redacted}"
            ),
        )
    if retryable(failure_type):
        return RecoveryOutcome(
            action="retry",
            attempts_used=cap,
            terminal_state=ObjectiveState.FAILED,
            failure_type=failure_type,
            detail=(
                f"bounded retry budget {cap} (hard-capped, never infinite); "
                "inspect failure evidence, then run recover with a corrected "
                f"strategy. Evidence: {redacted}"
            ),
        )
    return RecoveryOutcome(
        action="report",
        attempts_used=1,
        terminal_state=ObjectiveState.FAILED,
        failure_type=failure_type,
        detail=(
            "not retryable: reported without further execution; fix inputs/"
            f"constraints, then run recover. Evidence: {redacted}"
        ),
    )


class Engine:
    """Coordinates the AgentOS execution loop for one objective at a time."""

    def __init__(self, store: Store, bus: EventBus, registry: CapabilityRegistry) -> None:
        self.store = store
        self.bus = bus
        self.registry = registry
        self.router = Router(store)
        self.verifier = Verifier()
        self.verification_records = VerificationEngine(store)
        self.workflow_compiler = WorkflowCompiler(store)
        self.coach = Coach(store)
        self.learned_router = LearnedRouter(store)
        self.fleet_monitor = FleetMonitor(store)
        self.routine_engine = RoutineEngine(store, self._run_routine_workflow)

    def _trace_value(self, value: Any) -> Any:
        return redact_value(value)

    def _save_trace(self, trace: dict[str, Any]) -> None:
        self.store.save_job_trace(self._trace_value(trace))

    def _new_trace(self, objective: Objective) -> dict[str, Any]:
        context = objective.context or {}
        params = dict(context.get("params") or {})
        refs = []
        for key in ("project_path", "source_ref", "artifact_ref"):
            value = params.get(key) or context.get(key)
            if value:
                refs.append(str(value))
        fingerprint = TaskFingerprint(
            intent=f"{objective.title}\n{objective.description}".strip(),
            input_refs=tuple({"ref": ref, "hash": content_hash(params)} for ref in refs),
            output_schema=str(context.get("output_schema") or ""),
            constraints=tuple(objective.constraints),
        ).fingerprint()
        return {
            "taskId": objective.id,
            "runId": new_id("run"),
            "normalizedTask": objective.title.strip(),
            "fingerprint": fingerprint,
            "cacheDecision": {"status": "PENDING"},
            "workflowDecision": {"status": "NOT_MATCHED"},
            "triage": {},
            "teamPlan": {},
            "route": {"candidates": [], "chosenExecutor": None, "reason": ""},
            "contextRefs": refs,
            "request": dict(params),
            "artifactRefs": list(context.get("artifactRefs") or []),
            "executor": None,
            "runtime": None,
            "model": None,
            "result": None,
            "evidence": [],
            "verification": {},
            "failures": [],
            "retries": [],
            "escalations": [],
            "coachDecision": {"decision": "PENDING"},
            "lessonRefs": [],
            "workflowChanges": [],
            "cacheUpdates": [],
            "usage": {},
            "finalStatus": objective.status.value,
            "workersInvoked": 0,
            "modelCalls": 0,
            "premiumCalls": 0,
            "grokCalls": 0,
        }

    def _cache_intent(self, objective: Objective, operation: str) -> str:
        context = objective.context or {}
        params = context.get("params") or {}
        behavior_context = {
            str(key): content_hash(value)
            for key, value in sorted(context.items())
            if key != "params"
        }
        policy = self.registry.policy.to_dict()
        canonical = json.dumps(
            {
                "params": params,
                "constraints": list(objective.constraints),
                "context": behavior_context,
                "policy": policy,
            },
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        return f"{objective.title}\n{objective.description}\n{operation}\n{canonical}"

    def _cache_for(self, objective: Objective) -> IntelligenceCache:
        return IntelligenceCache(
            self.store,
            None,
            project=str(objective.context.get("project") or ""),
            requester_scope=str(objective.context.get("securityScope") or "PUBLIC"),
        )

    def _workflow_approval_valid(
        self,
        evidence: Any,
        operation: str,
        workflow_id: str = "",
    ) -> bool:
        if not isinstance(evidence, dict) or evidence.get("approved") is not True:
            return False
        if not str(evidence.get("approvalId") or "").strip():
            return False
        approved_operation = str(evidence.get("operation") or "")
        if approved_operation and approved_operation != operation:
            return False
        approved_workflow = str(evidence.get("workflowId") or "")
        if workflow_id and approved_workflow and approved_workflow != workflow_id:
            return False
        return True

    def _eligible_workflow(self, objective: Objective, operation: str) -> Any | None:
        task_class = str(objective.context.get("taskClass") or operation)
        params = dict(objective.context.get("params") or {})
        for workflow in self.store.list_workflows(task_class=task_class):
            if workflow.status.value not in ("OBSERVED", "VERIFIED", "CERTIFIED"):
                continue
            if not workflow.steps:
                continue
            if any(key not in params for key in (workflow.inputs or {})):
                continue
            if any(
                self.store.get_capability(capability_id) is None
                or not self.store.get_capability(capability_id).is_usable()
                for capability_id in (workflow.capability_requirements or [])
            ):
                continue
            eligible = True
            for precondition in workflow.preconditions:
                if precondition == "cache_available":
                    continue
                if precondition.startswith("capability_ready:"):
                    capability_id = precondition.split(":", 1)[1]
                    capability = self.store.get_capability(capability_id)
                    if capability is None or not capability.is_usable():
                        eligible = False
                        break
                elif precondition.startswith("input:"):
                    key = precondition.split(":", 1)[1]
                    if key not in params:
                        eligible = False
                        break
                else:
                    eligible = False
                    break
            if workflow.approval_policy not in ("", "none", "auto"):
                if not self._workflow_approval_valid(
                    objective.context.get("approvalEvidence"),
                    operation,
                    workflow.id,
                ):
                    eligible = False
            if eligible:
                return workflow
        return None

    def _plan_from_workflow(
        self, objective: Objective, workflow: Any, operation: str
    ) -> ExecutionPlan:
        steps = [
            PlanStep.from_dict(
                {
                    "id": f"workflow-{index}",
                    "kind": step.get("kind", "operation"),
                    "operation": step.get("operation", operation),
                    "capability_id": step.get("capability_id"),
                    "params": dict(step.get("params") or {}),
                    "depends_on": list(step.get("depends_on") or []),
                }
            )
            for index, step in enumerate(workflow.steps, start=1)
        ]
        return ExecutionPlan(
            id=new_id("plan"),
            objective_id=objective.id,
            strategy=Strategy.SEQUENTIAL,
            steps=steps,
            rationale=f"reuse workflow {workflow.id} version {workflow.version}",
        )

    def _block_oversized_plan(
        self,
        objective: Objective,
        trace: dict[str, Any],
        plan: ExecutionPlan,
    ) -> Report:
        reason = (
            f"execution plan step count {len(plan.steps)} exceeds "
            f"maximum {MAX_PLAN_STEPS}"
        )
        if trace.get("workflowDecision", {}).get("workflowId"):
            trace["workflowDecision"] = {
                **trace["workflowDecision"],
                "status": "REJECTED",
                "reason": reason,
            }
        else:
            trace["planDecision"] = {"status": "REJECTED", "reason": reason}
        trace["finalStatus"] = ObjectiveState.BLOCKED.value
        self._save_trace(trace)
        objective.status = ObjectiveState.BLOCKED
        objective.verification_status = VerificationStatus.UNVERIFIED
        objective.next_action = "split the workflow into bounded plans"
        objective.failure = {
            "type": "plan_too_large",
            "detail": reason,
            "step_count": len(plan.steps),
            "maximum_steps": MAX_PLAN_STEPS,
        }
        objective.mark_updated()
        self.store.save_objective(objective)
        self.bus.emit(
            EventType.OBJECTIVE_FAILED,
            objective_id=objective.id,
            payload={"blocked": True, "reason": reason},
        )
        return Report(
            objective_id=objective.id,
            state=ObjectiveState.BLOCKED,
            verification_status=VerificationStatus.UNVERIFIED,
            next_action=objective.next_action,
            failure=dict(objective.failure or {}),
        )

    def _safe_arithmetic(self, text: str) -> int | float | None:
        expression = re.sub(r"^\s*compute\s+", "", str(text or ""), flags=re.IGNORECASE)
        if not re.fullmatch(r"[0-9+\-*/(). ]+", expression):
            return None
        operators = {
            ast.Add: operator.add,
            ast.Sub: operator.sub,
            ast.Mult: operator.mul,
            ast.Div: operator.truediv,
        }
        def evaluate(node: ast.AST) -> int | float:
            if isinstance(node, ast.Expression):
                return evaluate(node.body)
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                return node.value
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = evaluate(node.operand)
                return value if isinstance(node.op, ast.UAdd) else -value
            if isinstance(node, ast.BinOp) and type(node.op) in operators:
                return operators[type(node.op)](evaluate(node.left), evaluate(node.right))
            raise ValueError("unsupported arithmetic expression")
        try:
            value = evaluate(ast.parse(expression, mode="eval"))
        except (SyntaxError, TypeError, ValueError, ZeroDivisionError):
            return None
        return int(value) if isinstance(value, float) and value.is_integer() else value

    def _verification_type(self, objective: Objective, operation: str) -> VerificationType:
        task_type = str(objective.context.get("taskType") or operation)
        if "research" in task_type.lower():
            return VerificationType.SOURCE
        if "database" in task_type.lower() or operation.startswith("db."):
            return VerificationType.DATABASE
        if "api" in task_type.lower():
            return VerificationType.API
        if operation.startswith("deterministic"):
            return VerificationType.BUILD
        return VerificationType.TEST

    def _record_advanced_verification(
        self,
        objective: Objective,
        execution: Execution,
        verification: VerificationResult,
        attempt: int = 1,
    ) -> tuple[list[VerificationRecord], VerificationOutcome, str | None]:
        task_class = str(objective.context.get("taskClass") or "default")
        policy = self.verification_records.policies.get(
            task_class,
            self.verification_records.policies["default"],
        )
        records = self.verification_records.start_verification(
            objective.id,
            task_class,
            dict(execution.output),
        )
        evidence_refs = [
            evidence.source or evidence.kind
            for evidence in verification.evidence
        ]
        failure_reason = (
            None
            if verification.verified
            else "; ".join(evidence.detail for evidence in verification.evidence)
        )
        for record in records:
            self.verification_records.run_verification(
                record,
                lambda _record: (
                    verification.verified,
                    {
                        "refs": evidence_refs,
                        "confidence": 1.0 if verification.verified else 0.0,
                    },
                    failure_reason,
                ),
                policy,
            )
        outcome, reason = self.verification_records.evaluate_outcomes(
            objective.id,
            records,
            attempt,
            task_class=task_class,
        )
        trace = self.store.get_job_trace(objective.id) or self._new_trace(objective)
        trace["verification"] = records[-1].to_dict() if records else {}
        trace["verificationOutcome"] = outcome.value
        trace["verificationReason"] = reason
        trace.setdefault("verificationRecords", []).extend(
            record.to_dict() for record in records
        )
        self._save_trace(trace)
        execution.output["verificationRecord"] = records[-1].to_dict() if records else {}
        execution.output["verificationOutcome"] = outcome.value
        self.store.save_execution(execution)
        return records, outcome, reason

    def _complete_preflight(
        self,
        objective: Objective,
        trace: dict[str, Any],
        source: str,
        operation: str,
        output: dict[str, Any],
        evidence: Evidence,
        started: float,
        verification: VerificationResult | None = None,
    ) -> Report:
        execution = Execution(
            id=new_id("exec"),
            objective_id=objective.id,
            capability_id=source,
            operation=operation,
            status=ExecutionStatus.EXECUTED,
            request={"source": source, "operation": operation},
            output=dict(output),
            attempt=1,
            started_at=utc_now_iso(),
            finished_at=utc_now_iso(),
            evidence=[evidence],
            mode=str(ExecutionMode.SIMULATED.value),
        )
        verification = VerificationResult(
            id=new_id("vresult"),
            objective_id=objective.id,
            execution_id=execution.id,
            verified=verification.verified if verification is not None else True,
            method=verification.method if verification is not None else evidence.kind,
            evidence=(
                list(verification.evidence)
                if verification is not None and verification.evidence
                else [evidence]
            ),
        )
        self.store.save_execution(execution)
        self.store.save_verification(verification)
        self._record_advanced_verification(objective, execution, verification)
        execution = self.store.get_execution(execution.id) or execution
        trace.update(
            {
                "executor": source,
                "result": dict(output),
                "evidence": [evidence.to_dict()],
                "finalStatus": "VERIFIED_COMPLETE",
                "workersInvoked": 0,
                "modelCalls": 0,
                "premiumCalls": 0,
                "grokCalls": 0,
            }
        )
        trace["verification"] = execution.output.get("verificationRecord", trace.get("verification", {}))
        trace["coachDecision"] = {
            "decision": "SKIPPED",
            "reason": "exact_cache_hit" if source == "intelligence_cache" else "deterministic_fast_path",
        }
        self._save_trace(trace)
        objective.status = ObjectiveState.COMPLETED
        objective.verification_status = VerificationStatus.VERIFIED
        objective.result = dict(execution.output)
        objective.evidence = [evidence]
        objective.next_action = None
        objective.failure = None
        objective.mark_updated()
        self.store.save_objective(objective)
        self.bus.emit(
            EventType.VERIFICATION_PASSED,
            objective_id=objective.id,
            execution_id=execution.id,
            payload={"verification_id": verification.id, "method": verification.method},
        )
        self.bus.emit(
            EventType.OBJECTIVE_COMPLETED,
            objective_id=objective.id,
            execution_id=execution.id,
            payload={"source": source, "workersInvoked": 0},
        )
        return Report(
            objective_id=objective.id,
            state=ObjectiveState.COMPLETED,
            verification_status=VerificationStatus.VERIFIED,
            capability_id=source,
            execution_id=execution.id,
            attempt=1,
            result=dict(execution.output),
        )

    def _run_routine_workflow(self, workflow_id: str, params: dict[str, Any]) -> dict[str, Any]:
        workflow = self.store.get_workflow(workflow_id)
        if workflow is None:
            return {"ok": False, "status": "UNCONFIGURED", "workflow_id": workflow_id}
        first_step = workflow.steps[0] if workflow.steps else {}
        operation = str(first_step.get("operation") or "echo")
        objective = Objective(
            id=new_id("obj"),
            title=f"routine:{workflow_id}",
            description="routine workflow execution",
            context={
                "operation": operation,
                "params": dict(first_step.get("params") or params or {}),
                "taskClass": workflow.task_class,
            },
        )
        self.store.save_objective(objective)
        report = self.run(objective.id)
        return {
            "ok": report.state == ObjectiveState.COMPLETED,
            "status": report.state.value,
            "workflow_id": workflow_id,
            "objective_id": objective.id,
            "verification": report.verification_status.value if report.verification_status else None,
            "result": report.result,
        }

    def _cache_policy_reason(self, objective: Objective, operation: str) -> str:
        params = dict(objective.context.get("params") or {})
        operation_class = str(
            params.get("operation_class")
            or objective.context.get("operationClass")
            or "standard"
        )
        if operation == "shell.run":
            decision = self.registry.policy.check_shell(
                str(params.get("command") or ""), operation_class
            )
            if not decision.allowed or decision.requires_approval:
                return "; ".join(decision.reasons) or "shell approval required"
        if operation == "fs.write":
            decision = self.registry.policy.check_write_path(str(params.get("path") or ""))
            if not decision.allowed:
                return "; ".join(decision.reasons)
        if (
            operation_class in self.registry.policy.approval_required_classes
            and not self._workflow_approval_valid(
                objective.context.get("approvalEvidence"), operation
            )
        ):
            return f"operation class {operation_class!r} requires fresh approval"
        return ""

    def _verify_cached_output(
        self,
        objective: Objective,
        operation: str,
        payload: dict[str, Any],
    ) -> VerificationResult:
        request = dict(objective.context.get("params") or {})
        request["verify_method"] = "exit_code" if operation == "shell.run" else "structural"
        execution = Execution(
            id=new_id("cache_verify"),
            objective_id=objective.id,
            capability_id="intelligence_cache",
            operation=operation,
            status=ExecutionStatus.EXECUTED,
            request=request,
            output=dict(payload),
        )
        return self.verifier.verify(objective, execution)

    def _preflight(
        self,
        objective: Objective,
        operation: str,
        trace: dict[str, Any],
        started: float,
        *,
        allow_cache: bool = True,
    ) -> Report | None:
        cache_intent = self._cache_intent(objective, operation)
        output_schema = str(objective.context.get("output_schema") or "")
        cache = self._cache_for(objective)
        policy_reason = self._cache_policy_reason(objective, operation)
        if not allow_cache:
            trace["cacheDecision"] = {
                "status": "BYPASSED",
                "reason": "forced execution",
            }
        elif policy_reason:
            trace["cacheDecision"] = {
                "status": "BYPASSED",
                "reason": f"current policy: {policy_reason}",
            }
        else:
            cache_result = cache.get(
                cache_intent,
                output_schema=output_schema,
                project=str(objective.context.get("project") or ""),
            )
            if cache_result.get("hit"):
                cached_payload = dict(cache_result.get("payload") or {})
                verification = self._verify_cached_output(
                    objective, operation, cached_payload
                )
                if not verification.verified:
                    trace["cacheDecision"] = {
                        "status": "REJECTED",
                        "reason": "cached output failed current verification",
                        "verification": verification.to_dict(),
                    }
                    self._save_trace(trace)
                    cache_key = str(cache_result.get("cacheKey") or "")
                    row = self.store.get_intelligence_entry(cache_key)
                    if row is not None:
                        row["status"] = "INVALID"
                        row["lastVerifiedAt"] = ""
                        self.store.save_intelligence_entry(row)
                else:
                    trace["cacheDecision"] = {
                        "status": "HIT",
                        "level": cache_result.get("level"),
                        "provenance": cache_result.get("provenance"),
                        "reason": cache_result.get("reason"),
                    }
                    return self._complete_preflight(
                        objective,
                        trace,
                        "intelligence_cache",
                        operation,
                        {
                            **cached_payload,
                            "source": "intelligence_cache",
                            "cacheKey": cache_result.get("cacheKey"),
                            "provenance": cache_result.get("provenance"),
                            "workersInvoked": 0,
                            "modelCalls": 0,
                            "grokCalls": 0,
                        },
                        Evidence(
                            kind="cache",
                            detail=str(cache_result.get("reason") or "exact cache hit"),
                            source=str(cache_result.get("provenance") or ""),
                        ),
                        started,
                        verification=verification,
                    )
            else:
                trace["cacheDecision"] = {
                    "status": "MISS",
                    "reason": cache_result.get("reason"),
                }

        if operation == "deterministic.calculate":
            value = self._safe_arithmetic(objective.title)
            if value is not None:
                return self._complete_preflight(
                    objective,
                    trace,
                    "system.deterministic",
                    operation,
                    {"value": value, "source": "deterministic", "workersInvoked": 0, "modelCalls": 0, "grokCalls": 0},
                    Evidence(kind="recompute", detail=f"deterministic result {value}", source="engine"),
                    started,
                )

        if operation in ("state.lookup", "database.lookup"):
            state = {
                "objectiveCount": len(self.store.list_objectives()),
                "capabilityCount": len(self.store.list_capabilities()),
                "eventCount": len(self.bus.recent(limit=10000)),
            }
            return self._complete_preflight(
                objective,
                trace,
                "system.state_lookup",
                operation,
                {"state": state, "source": "state_lookup", "workersInvoked": 0, "modelCalls": 0, "grokCalls": 0},
                Evidence(kind="database", detail="state lookup confirmed", source="store"),
                started,
            )
        return None

    def _record_executor_metric(
        self,
        objective: Objective,
        execution: Execution,
        verified: bool,
        started: float,
        failure_type: str | None = None,
    ) -> None:
        capability = self.store.get_capability(execution.capability_id)
        self.store.record_executor_metric(
            {
                "executor": execution.capability_id,
                "runtime": capability.adapter if capability else "",
                "model_provider": "",
                "task_class": str(objective.context.get("taskClass") or execution.operation),
                "job_id": objective.id,
                "success": 1 if verified else 0,
                "verification_result": "VERIFIED" if verified else "FAILED",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "retry_count": max(0, execution.attempt - 1),
                "resource_cost": str(execution.cost_status or "UNKNOWN"),
                "human_correction": 0,
                "context_size": len(json.dumps(execution.output, default=str)),
                "final_outcome": failure_type or ("VERIFIED_COMPLETE" if verified else "FAILED"),
            }
        )

    def _postprocess_success(
        self, objective: Objective, execution: Execution, plan: ExecutionPlan, started: float
    ) -> list[str]:
        self._record_executor_metric(objective, execution, True, started)
        trace = self.store.get_job_trace(objective.id) or self._new_trace(objective)
        lesson_refs: list[str] = []
        workflow_id = str((trace.get("workflowDecision") or {}).get("workflowId") or "")
        if workflow_id:
            self.workflow_compiler.record_workflow_use(workflow_id, True)
            trace.setdefault("workflowChanges", []).append({"workflowId": workflow_id, "event": "VERIFIED_EVIDENCE"})
        security_scope = str(objective.context.get("securityScope") or "PUBLIC").upper()
        if security_scope == "HIGHLY_SENSITIVE":
            trace.setdefault("cacheUpdates", []).append({
                "cacheKey": None,
                "status": "SKIPPED",
                "reason": "HIGHLY_SENSITIVE material is never cached",
            })
        elif looks_secret(execution.output):
            trace.setdefault("cacheUpdates", []).append({
                "cacheKey": None,
                "status": "SKIPPED",
                "reason": "secret-bearing output is never cached",
            })
        else:
            entry = self._cache_for(objective).build_entry(
                self._cache_intent(objective, execution.operation),
                category=execution.operation,
                output_schema=str(objective.context.get("output_schema") or ""),
                payload=dict(execution.output),
                source_refs=[
                    {"kind": "execution", "ref": execution.id},
                    {"kind": "verification", "ref": trace.get("verification", {}).get("id", "")},
                ],
                artifact_ref=str(objective.context.get("artifactRef") or ""),
                security_scope=security_scope,
            )
            self._cache_for(objective).put(entry)
            trace.setdefault("cacheUpdates", []).append({"cacheKey": entry.cacheKey, "status": "STORED"})
        workflow_success_count = 0
        workflow_id = str((trace.get("workflowDecision") or {}).get("workflowId") or "")
        if workflow_id:
            workflow = self.store.get_workflow(workflow_id)
            workflow_success_count = int((workflow.metrics if workflow else {}).get("successes", 0))
        history = [
            {
                "id": item.id,
                "attempt": item.attempt,
                "status": item.status.value,
                "evidence_refs": [e.source for e in item.evidence],
                "workflow_used": bool(workflow_id),
                "workflow_id": workflow_id,
                "workflow_success_count": workflow_success_count,
                "cache_miss_reason": (trace.get("cacheDecision") or {}).get("reason", ""),
                "context_size": len(json.dumps(item.output, default=str)),
                "verification_rounds": 1,
            }
            for item in self.store.list_executions(objective.id)
        ]
        if execution.attempt > 1 or workflow_id:
            lessons = self.coach.analyze_execution(objective.id, str(objective.context.get("taskClass") or execution.operation), history)
            lesson_refs = [lesson.id for lesson in lessons]
            trace["coachDecision"] = {"decision": "RUN", "reason": "meaningful_completed_work"}
        else:
            trace["coachDecision"] = {"decision": "SKIPPED", "reason": "routine_success"}
        trace["lessonRefs"] = lesson_refs
        trace["workersInvoked"] = execution.attempt
        trace["executor"] = execution.capability_id
        trace["modelCalls"] = 0
        trace["premiumCalls"] = 0
        trace["grokCalls"] = 0
        self._save_trace(trace)
        return lesson_refs

    def _postprocess_failure(
        self, objective: Objective, execution: Execution, plan: ExecutionPlan, failure_type: str, started: float
    ) -> list[str]:
        self._record_executor_metric(
            objective, execution, False, started, failure_type
        )
        trace = self.store.get_job_trace(objective.id) or self._new_trace(objective)
        history = [
            {
                "id": item.id,
                "attempt": item.attempt,
                "status": item.status.value,
                "evidence_refs": [e.source for e in item.evidence],
            }
            for item in self.store.list_executions(objective.id)
        ]
        lessons = self.coach.analyze_execution(objective.id, str(objective.context.get("taskClass") or execution.operation), history)
        trace["coachDecision"] = {"decision": "RUN", "reason": "worker_failed"}
        trace["lessonRefs"] = [lesson.id for lesson in lessons]
        trace["workersInvoked"] = execution.attempt
        self._save_trace(trace)
        return [lesson.id for lesson in lessons]

    # ------------------------------------------------------------------
    # main loop
    # ------------------------------------------------------------------
    def run(self, objective_id: str, options: EngineOptions | None = None) -> Report:
        opts = options or EngineOptions()
        objective = self.store.get_objective(objective_id)
        if objective is None:
            raise AgentOSError(f"unknown objective: {objective_id}")
        if objective.status in (
            ObjectiveState.COMPLETED,
            ObjectiveState.CANCELLED,
        ) and not opts.force:
            return Report(
                objective_id=objective.id,
                state=objective.status,
                verification_status=objective.verification_status,
                next_action="objective is already terminal; use --force to re-run",
                result=objective.result,
                failure=objective.failure,
            )

        # DECOMPOSITION GATE: a parent waits for its children; a child
        # waits for its dependencies. Leaves stay individually runnable.
        dependency_report = self._dependency_block(objective)
        if dependency_report is not None:
            return dependency_report

        # INSPECT CURRENT STATE
        started = time.monotonic()
        trace = self._new_trace(objective)
        self._save_trace(trace)
        self.store.bump_intelligence_counter("requests_total", 1)
        objective.status = ObjectiveState.RUNNING
        objective.failure = None
        objective.mark_updated()
        self.store.save_objective(objective)
        self.bus.emit(
            EventType.OBJECTIVE_STARTED,
            objective_id=objective.id,
            payload={"title": objective.title},
        )

        # DISCOVER CAPABILITIES
        capabilities = self.registry.discover()

        # PLAN: router determines strategy, steps, roles and rationale.
        operation = self._select_operation(objective, opts)
        trace["operation"] = operation
        self._save_trace(trace)
        preflight = self._preflight(
            objective,
            operation,
            trace,
            started,
            allow_cache=not opts.force,
        )
        if preflight is not None:
            return preflight
        workflow = None if opts.force else self._eligible_workflow(objective, operation)
        if workflow is not None:
            plan = self._plan_from_workflow(objective, workflow, operation)
            trace["workflowDecision"] = {
                "status": "REUSED",
                "workflowId": workflow.id,
                "version": workflow.version,
                "maturity": workflow.maturity.value,
            }
        else:
            plan = self.router.plan(
                objective, operation, capabilities, max_attempts=max(1, opts.max_attempts)
            )
            trace["workflowDecision"] = {"status": "FRESH_PLAN"}
        if len(plan.steps) > MAX_PLAN_STEPS:
            return self._block_oversized_plan(objective, trace, plan)
        target_capability = str(objective.context.get("targetCapability") or "")
        triage_decision = triage(
            objective.title,
            exact_cache_hit=False,
            known_workflow=workflow is not None,
            target_capability=target_capability,
        )
        team_plan = plan_team(
            quality_floor=str(objective.context.get("qualityFloor") or ""),
            risk=str(objective.context.get("risk") or "low"),
            decomposable=bool(objective.context.get("decomposable")),
            independent_subtasks=int(objective.context.get("subtasks") or 1),
            verification_burden=bool(objective.context.get("verificationBurden")),
            security=str(objective.context.get("security") or "internal"),
        )
        trace["triage"] = triage_decision.to_dict()
        trace["teamPlan"] = team_plan.to_dict()
        trace["route"]["candidates"] = [
            capability.id for capability in capabilities if capability.supports(operation)
        ]
        trace["route"]["chosenExecutor"] = (
            plan.steps[0].capability_id if plan.steps else None
        )
        trace["route"]["reason"] = plan.rationale
        self._save_trace(trace)
        self.store.save_plan(plan)
        objective.strategy = (
            f"operation:{operation} via "
            f"{plan.steps[0].capability_id if plan.steps and plan.steps[0].capability_id else 'unresolved'} "
            f"[{plan.strategy.value}]"
        )
        objective.mark_updated()
        self.store.save_objective(objective)
        self.bus.emit(
            EventType.CAPABILITY_SELECTED,
            objective_id=objective.id,
            payload={
                "operation": operation,
                "strategy": plan.strategy.value,
                "rationale": plan.rationale,
                "roles": [r.value for r in plan.roles],
            },
        )

        if plan.strategy == Strategy.PARALLEL:
            return self._run_parallel(objective, plan, operation, opts, started)
        return self._run_sequential(objective, plan, operation, opts, started)

    def _dependency_block(self, objective: Objective) -> Report | None:
        """BLOCKED when children/deps are incomplete; None when runnable."""
        children = self.store.list_objectives(parent_id=objective.id)
        if children:
            incomplete = [
                c for c in children if c.status != ObjectiveState.COMPLETED
            ]
            if incomplete:
                return self._blocked_by_dependencies(
                    objective,
                    "blocked by incomplete child objectives: "
                    f"{[c.id for c in incomplete]}",
                )
            return None
        deps = objective.parents_data.get("depends_on") or []
        if deps:
            completed_ids = {
                sib.id
                for sib in (
                    self.store.list_objectives(parent_id=objective.parent_id)
                    if objective.parent_id
                    else []
                )
                if sib.status == ObjectiveState.COMPLETED
            }
            for dep_id in deps:
                dep = self.store.get_objective(dep_id)
                if dep is not None and dep.status == ObjectiveState.COMPLETED:
                    completed_ids.add(dep_id)
            ok, reason = objective_is_unblocked(objective, completed_ids)
            if not ok:
                return self._blocked_by_dependencies(
                    objective, reason or "blocked by incomplete dependencies"
                )
        return None

    def _blocked_by_dependencies(
        self, objective: Objective, reason: str
    ) -> Report:
        objective.status = ObjectiveState.BLOCKED
        objective.next_action = reason
        objective.result = {"blocked_reason": reason}
        objective.mark_updated()
        self.store.save_objective(objective)
        return Report(
            objective_id=objective.id,
            state=ObjectiveState.BLOCKED,
            verification_status=objective.verification_status,
            next_action=reason,
            result={"blocked_reason": reason},
        )

    # ------------------------------------------------------------------
    # sequential / composite strategies
    # ------------------------------------------------------------------
    def _run_sequential(
        self,
        objective: Objective,
        plan: ExecutionPlan,
        operation: str,
        opts: EngineOptions,
        started: float,
    ) -> Report:
        max_attempts = max(1, opts.max_attempts)
        revisions = 0
        step_index = 0
        last_execution: Execution | None = None
        last_verification: VerificationResult | None = None
        last_failure_type: str | None = None

        while step_index < len(plan.steps):
            step = plan.steps[step_index]
            if step.kind in ("operation", "execute", "build", "research", "delegate"):
                outcome = self._run_operation_step(
                    objective, step, operation, opts, max_attempts
                )
                if outcome.execution is None:
                    return self._blocked(objective, step.operation or operation, plan)
                last_execution = outcome.execution
                if (
                    plan.strategy == Strategy.ITERATIVE
                    and outcome.verification is not None
                    and outcome.verification.verified
                ):
                    last_verification = outcome.verification
                    break  # ITERATIVE stops at first verification
                last_verification = outcome.verification
                last_failure_type = outcome.failure_type
                if last_verification is None or not last_verification.verified:
                    assert last_execution is not None and last_verification is not None
                    if self._is_unsafe(last_execution):
                        return self._blocked_unsafe(objective, last_execution)
                    return self._failed(
                        objective,
                        last_execution,
                        last_verification,
                        last_failure_type or "unknown",
                        outcome.attempts,
                        max_attempts,
                        started,
                        plan,
                    )
            elif step.kind == "verify":
                outcome = self._run_verify_step(objective, step, opts)
                if outcome.verification is None or not outcome.verification.verified:
                    assert outcome.execution is not None and outcome.verification is not None
                    return self._failed(
                        objective,
                        outcome.execution,
                        outcome.verification,
                        outcome.failure_type or "verification",
                        outcome.attempts,
                        max_attempts,
                        started,
                        plan,
                    )
                last_verification = outcome.verification
                if last_execution is None:
                    last_execution = outcome.execution
            elif step.kind == "evaluate":
                assert last_execution is not None
                evaluation = evaluate_quality(
                    objective,
                    last_execution,
                    last_verification or self.verifier.verify(objective, last_execution),
                )
                self.store.save_quality(evaluation)
                self.bus.emit(
                    EventType.VERIFICATION_PASSED
                    if evaluation.grade == "pass"
                    else EventType.VERIFICATION_FAILED,
                    objective_id=objective.id,
                    execution_id=last_execution.id,
                    payload={
                        "quality_id": evaluation.id,
                        "score": evaluation.score,
                        "grade": evaluation.grade,
                    },
                )
                if evaluation.grade != "pass" and revisions < MAX_REVISIONS:
                    revisions += 1
                    self.bus.emit(
                        EventType.RECOVERY_STARTED,
                        objective_id=objective.id,
                        execution_id=last_execution.id,
                        payload={
                            "strategy": "revise",
                            "revision": revisions,
                            "findings": evaluation.findings,
                        },
                    )
                    continue  # re-run from the execute step
                if evaluation.grade == "fail":
                    assert last_verification is not None
                    return self._failed(
                        objective,
                        last_execution,
                        last_verification,
                        "quality",
                        revisions + 1,
                        MAX_REVISIONS + 1,
                        started,
                        plan,
                    )
            step_index += 1

        assert last_execution is not None and last_verification is not None
        return self._completed(objective, last_execution, last_verification, started, plan)

    # ------------------------------------------------------------------
    # parallel strategy with conflict handling + workspace isolation
    # ------------------------------------------------------------------
    def _run_parallel(
        self,
        objective: Objective,
        plan: ExecutionPlan,
        operation: str,
        opts: EngineOptions,
        started: float,
    ) -> Report:
        max_attempts = max(1, opts.max_attempts)
        workers = min(MAX_PARALLEL_WORKERS, len(plan.steps))
        outcomes: dict[str, StepOutcome] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    self._run_operation_step_isolated,
                    objective.to_dict(),
                    step.to_dict(),
                    operation,
                    opts.timeout_seconds,
                    max_attempts,
                    opts.mode,
                    list(opts.authorization or []),
                ): step.id
                for step in plan.steps
            }
            for future in concurrent.futures.as_completed(futures):
                step_id = futures[future]
                try:
                    execution_dict, verification_dict, failure_type, attempts = future.result()
                except Exception as exc:  # never let a worker crash the loop
                    self.bus.emit(
                        EventType.EXECUTION_FAILED,
                        objective_id=objective.id,
                        payload={"step_id": step_id, "error": f"{type(exc).__name__}: {exc}"},
                    )
                    return self._failed_no_execution(
                        objective, operation, f"parallel worker crashed: {exc}", plan
                    )
                execution = Execution.from_dict(execution_dict) if execution_dict else None
                verification = (
                    VerificationResult.from_dict(verification_dict)
                    if verification_dict
                    else None
                )
                outcomes[step_id] = StepOutcome(execution, verification, failure_type, attempts)
                # NOTE: worker threads already persisted their executions and
                # verifications to the same database; re-saving here would
                # collide on ids. This thread only aggregates.
                self.bus.emit(
                    EventType.EXECUTION_COMPLETED
                    if verification is not None and verification.verified
                    else EventType.EXECUTION_FAILED,
                    objective_id=objective.id,
                    execution_id=execution.id if execution else None,
                    payload={"step_id": step_id, "parallel": True},
                )

        failed = [
            (step_id, outcome)
            for step_id, outcome in outcomes.items()
            if outcome.verification is None or not outcome.verification.verified
        ]
        missing = [
            step_id
            for step_id, outcome in outcomes.items()
            if outcome.execution is None
        ]
        if missing:
            return self._blocked(objective, operation, plan)
        if failed:
            step_id, outcome = failed[0]
            assert outcome.execution is not None and outcome.verification is not None
            return self._failed(
                objective,
                outcome.execution,
                outcome.verification,
                outcome.failure_type or "unknown",
                outcome.attempts,
                max_attempts,
                started,
                plan,
            )
        executions = [o.execution for o in outcomes.values() if o.execution]
        verifications = [o.verification for o in outcomes.values() if o.verification]
        assert executions and verifications
        merged = self._merge_parallel(objective, executions, verifications)
        return self._completed(objective, merged, verifications[-1], started, plan)

    def _run_operation_step_isolated(
        self,
        objective_dict: dict[str, Any],
        step_dict: dict[str, Any],
        operation: str,
        timeout: float,
        max_attempts: int,
        mode: ExecutionMode = ExecutionMode.SIMULATED,
        authorization: list[dict] | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None, int]:
        """Worker entry: own store connection (sqlite is not thread-shared)."""
        from agentos.models import Objective as ObjectiveModel
        from agentos.models import PlanStep as PlanStepModel

        worker_store = Store(self.store.path)
        try:
            worker_bus = EventBus(worker_store)
            worker_registry = CapabilityRegistry(worker_store, worker_bus)
            worker_engine = Engine(worker_store, worker_bus, worker_registry)
            objective = ObjectiveModel.from_dict(objective_dict)
            step = PlanStepModel.from_dict(step_dict)
            outcome = worker_engine._run_operation_step(
                objective,
                step,
                operation,
                EngineOptions(
                    timeout_seconds=timeout,
                    mode=mode,
                    authorization=list(authorization or []),
                ),
                max_attempts,
            )
        finally:
            worker_store.close()
        return (
            outcome.execution.to_dict() if outcome.execution else None,
            outcome.verification.to_dict() if outcome.verification else None,
            outcome.failure_type,
            outcome.attempts,
        )

    def _merge_parallel(
        self,
        objective: Objective,
        executions: list[Execution],
        verifications: list[VerificationResult],
    ) -> Execution:
        merged_output: dict[str, Any] = {
            "parallel_items": len(executions),
            "item_outputs": [e.output for e in executions],
        }
        merged_evidence: list[Evidence] = []
        for verification in verifications:
            merged_evidence.extend(verification.evidence)
        merged = Execution(
            id=new_id("exec"),
            objective_id=objective.id,
            capability_id=executions[0].capability_id,
            operation=executions[0].operation,
            status=ExecutionStatus.VERIFIED,
            request={"parallel": True, "items": len(executions)},
            output=merged_output,
            attempt=1,
            started_at=executions[0].started_at,
            evidence=merged_evidence,
        )
        merged.finish(ExecutionStatus.VERIFIED)
        self.store.save_execution(merged)
        return merged

    # ------------------------------------------------------------------
    # step runners
    # ------------------------------------------------------------------
    def _run_operation_step(
        self,
        objective: Objective,
        step: PlanStep,
        operation: str,
        opts: EngineOptions,
        max_attempts: int,
    ) -> StepOutcome:
        # Mode gate at the execution boundary: unauthorized LIVE raises
        # before any adapter is touched (fail-closed). Covers sequential,
        # parallel-isolated, and independent-verification paths.
        require_execution_authority(opts.mode, opts.authorization)
        capability = (
            self.store.get_capability(step.capability_id) if step.capability_id else None
        )
        if capability is None:
            # No capability assigned: the caller converts this to BLOCKED
            # with a recorded gap analysis (single source of truth).
            return StepOutcome(None, None, "no_capability", 0)
        adapter = self.registry.adapter_for(capability)
        if adapter is None:
            return StepOutcome(None, None, "no_adapter", 0)

        self.bus.emit(
            EventType.CAPABILITY_SELECTED,
            objective_id=objective.id,
            payload={
                "capability_id": capability.id,
                "operation": step.operation,
                "role": step.role.value if step.role else None,
            },
        )
        last_execution: Execution | None = None
        last_verification: VerificationResult | None = None
        last_failure_type: str | None = None
        last_attempt = 0
        for attempt in range(1, max_attempts + 1):
            last_attempt = attempt
            execution = Execution(
                id=new_id("exec"),
                objective_id=objective.id,
                capability_id=capability.id,
                operation=step.operation,
                status=ExecutionStatus.REQUESTED,
                request=self._build_request(objective, capability, step, opts),
                attempt=attempt,
                started_at=utc_now_iso(),
            )
            self.store.save_execution(execution)
            last_execution = execution
            self.bus.emit(
                EventType.EXECUTION_STARTED,
                objective_id=objective.id,
                execution_id=execution.id,
                payload={
                    "attempt": attempt,
                    "capability_id": capability.id,
                    "step_id": step.id,
                },
            )
            execution.status = ExecutionStatus.ATTEMPTED
            params = dict(execution.request.get("params", {}))
            project = params.get("project_path")
            project_root = project if isinstance(project, str) and project else None
            before_state: dict[str, str] | None = None
            if project_root:
                try:
                    from agentos.changes import capture_fs_state

                    before_state = capture_fs_state(project_root)
                except Exception:
                    before_state = None
            try:
                result = adapter.execute(
                    step.operation,
                    ExecutionRequest(
                        operation=step.operation,
                        params=params,
                        timeout_seconds=float(
                            execution.request.get("timeout_seconds", opts.timeout_seconds)
                        ),
                        mode=opts.mode,
                        authorization=list(opts.authorization or []),
                        project_path=project_root,
                    ),
                )
                execution.output = dict(result.output or {})
                execution.evidence = list(result.evidence or [])
                if result.exit_code is not None:
                    execution.output.setdefault("exit_code", result.exit_code)
                if result.ok:
                    execution.status = ExecutionStatus.EXECUTED
                    execution.error = None
                else:
                    execution.status = ExecutionStatus.FAILED
                    execution.error = {
                        "message": result.error or "adapter reported failure",
                        "exit_code": result.exit_code,
                    }
            except Exception as exc:  # never let an adapter crash the loop
                error_text = f"{type(exc).__name__}: {exc}"
                result = AdapterResult(ok=False, output={}, error=error_text)
                execution.status = ExecutionStatus.FAILED
                execution.error = {"message": error_text}
                execution.evidence.append(
                    Evidence(
                        kind="exception",
                        detail=f"adapter raised {type(exc).__name__}: {exc}",
                        source="engine",
                    )
                )
            if project_root and before_state is not None:
                try:
                    from agentos.changes import (
                        attribute_changes,
                        capture_fs_state,
                        change_summary,
                        diff_fs,
                        git_status_short,
                    )

                    after_state = capture_fs_state(project_root)
                    diff = diff_fs(before_state, after_state)
                    attr = attribute_changes(
                        before_state, after_state, baseline=before_state
                    )
                    git_summary = git_status_short(project_root)
                    execution.output["files_changed"] = {
                        "added": diff["added"],
                        "modified": diff["modified"],
                        "deleted": diff["deleted"],
                        "produced": list(attr["produced"]),
                        "pre_existing": list(attr["pre_existing"]),
                    }
                    execution.output["git_diff_summary"] = git_summary
                    execution.evidence.append(
                        Evidence(
                            kind="change_footprint",
                            detail=change_summary(
                                before_state,
                                after_state,
                                baseline=before_state,
                                git_status=git_summary or None,
                            ),
                            source="engine",
                        )
                    )
                except Exception:
                    pass  # change detection must never break execution
            # Task 5 debt: populate the Execution record fields from the
            # request (mode/authorization), the Task 4 change keys already
            # attached to output (files_changed/git_diff_summary), a short
            # human summary, and the normalize_result pair (stored in output
            # — Execution carries no dedicated fields for the pair).
            self._populate_execution_record(execution, result, opts, capability)
            execution.finish(execution.status)
            self.store.save_execution(execution)

            if execution.status == ExecutionStatus.EXECUTED:
                self.bus.emit(
                    EventType.EXECUTION_COMPLETED,
                    objective_id=objective.id,
                    execution_id=execution.id,
                    payload={"attempt": attempt, "step_id": step.id},
                )
            else:
                self.bus.emit(
                    EventType.EXECUTION_FAILED,
                    objective_id=objective.id,
                    execution_id=execution.id,
                    payload={"attempt": attempt, "error": execution.error, "step_id": step.id},
                )

            objective.status = ObjectiveState.VERIFYING
            objective.mark_updated()
            self.store.save_objective(objective)
            self.bus.emit(
                EventType.VERIFICATION_STARTED,
                objective_id=objective.id,
                execution_id=execution.id,
            )
            verification = self.verifier.verify(objective, execution)
            _, advanced_outcome, advanced_reason = self._record_advanced_verification(
                objective, execution, verification, attempt
            )
            if advanced_outcome != VerificationOutcome.ACCEPT:
                verification.verified = False
                if advanced_reason:
                    verification.evidence.append(
                        Evidence(
                            kind="advanced_verification",
                            detail=advanced_reason,
                            source="verification_engine",
                        )
                    )
            last_verification = verification
            self.store.save_verification(verification)
            trace = self.store.get_job_trace(objective.id)
            if trace is not None:
                trace.setdefault("evidence", []).extend(
                    evidence.to_dict() for evidence in verification.evidence
                )
                self._save_trace(trace)

            if verification.verified:
                self.bus.emit(
                    EventType.VERIFICATION_PASSED,
                    objective_id=objective.id,
                    execution_id=execution.id,
                    payload={
                        "verification_id": verification.id,
                        "method": verification.method,
                        "step_id": step.id,
                    },
                )
                return StepOutcome(execution, verification, None, attempt)

            self.bus.emit(
                EventType.VERIFICATION_FAILED,
                objective_id=objective.id,
                execution_id=execution.id,
                payload={"verification_id": verification.id, "step_id": step.id},
            )
            failure_type = classify_failure(
                (execution.error or {}).get("message")
                if isinstance(execution.error, dict)
                else str(execution.error),
                execution.output.get("exit_code")
                if isinstance(execution.output, dict)
                else None,
            )
            last_failure_type = failure_type
            failure = Failure(
                id=new_id("fail"),
                objective_id=objective.id,
                execution_id=execution.id,
                failure_type=failure_type,
                detail="; ".join(e.detail for e in verification.evidence)
                or "verification failed",
                attempt=attempt,
                recovered=False,
            )
            self.store.save_failure(failure)
            trace = self.store.get_job_trace(objective.id)
            if trace is not None:
                trace.setdefault("failures", []).append(
                    {
                        "executionId": execution.id,
                        "attempt": attempt,
                        "reason": failure_type,
                        "feedback": failure.detail,
                    }
                )
                self._save_trace(trace)

            if self.fleet_monitor.is_job_suppressed(objective.id):
                if trace is not None:
                    trace.setdefault("escalations", []).append({
                        "outcome": "SUPPRESSED_RETRY",
                        "reason": "fleet incident is open",
                        "attempt": attempt,
                    })
                    self._save_trace(trace)
                break

            if attempt < max_attempts and retryable(failure_type):
                trace = self.store.get_job_trace(objective.id)
                if trace is not None:
                    trace.setdefault("retries", []).append({
                        "executionId": execution.id,
                        "attempt": attempt,
                        "reason": failure_type,
                        "strategy": "retry",
                    })
                    self._save_trace(trace)
                self.bus.emit(
                    EventType.RECOVERY_STARTED,
                    objective_id=objective.id,
                    execution_id=execution.id,
                    payload={
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "backoff_seconds": backoff_delay_seconds(attempt),
                        "failure_type": failure_type,
                        "strategy": "retry",
                        "step_id": step.id,
                    },
                )
                objective.status = ObjectiveState.RUNNING
                objective.mark_updated()
                self.store.save_objective(objective)
                continue
            if attempt < max_attempts and not retryable(failure_type):
                # Recovery strategy: alternate capability for unretryable errors.
                alternate = self._alternate_capability(step.operation, capability.id)
                if alternate is not None:
                    self.bus.emit(
                        EventType.RECOVERY_STARTED,
                        objective_id=objective.id,
                        execution_id=execution.id,
                        payload={
                            "strategy": "alternate_capability",
                            "from": capability.id,
                            "to": alternate.id,
                            "step_id": step.id,
                        },
                    )
                    trace = self.store.get_job_trace(objective.id)
                    if trace is not None:
                        trace.setdefault("retries", []).append({
                            "executionId": execution.id,
                            "attempt": attempt,
                            "reason": failure_type,
                            "strategy": "alternate_capability",
                            "from": capability.id,
                            "to": alternate.id,
                        })
                        trace.setdefault("escalations", []).append(
                            {
                                "outcome": "ESCALATE_WORKER",
                                "from": capability.id,
                                "to": alternate.id,
                                "reason": failure_type,
                            }
                        )
                        self._save_trace(trace)
                    capability = alternate
                    adapter = self.registry.adapter_for(capability)
                    if adapter is None:
                        break
                    objective.status = ObjectiveState.RUNNING
                    objective.mark_updated()
                    self.store.save_objective(objective)
                    continue
            break

        assert last_execution is not None and last_verification is not None
        return StepOutcome(
            last_execution, last_verification, last_failure_type or "unknown", last_attempt
        )

    def _populate_execution_record(
        self,
        execution: Execution,
        result: AdapterResult,
        opts: EngineOptions,
        capability: Any,
    ) -> None:
        """Fill the recorded Task 5 Execution fields after an adapter run."""
        execution.mode = str(getattr(opts.mode, "value", opts.mode))
        execution.authorization = list(opts.authorization or [])
        result_state, verification_state = normalize_result(result, opts.mode)
        if isinstance(execution.output, dict):
            execution.output["result_state"] = result_state.value
            execution.output["verification_state"] = verification_state.value
            execution.files_changed = _flatten_files_changed(
                execution.output.get("files_changed")
            )
            git_summary = execution.output.get("git_diff_summary")
            execution.git_diff_summary = (
                git_summary if isinstance(git_summary, str) else ""
            )
            # UNKNOWN unless an adapter measured a real charge (never invented).
            execution.cost_status = cost_status(execution.output.get("cost"))
        else:
            execution.files_changed = []
            execution.git_diff_summary = ""
        execution.result_summary = _execution_summary(
            execution, result, capability
        )[:200]
        execution.evidence.append(
            Evidence(
                kind="normalized_result",
                detail=(
                    f"result={result_state.value} "
                    f"verification={verification_state.value} "
                    f"mode={execution.mode}"
                ),
                source="engine",
            )
        )

    def _run_verify_step(
        self, objective: Objective, step: PlanStep, opts: EngineOptions
    ) -> StepOutcome:
        """Independent verification: a check executed by another capability."""
        outcome = self._run_operation_step(objective, step, step.operation, opts, 1)
        if outcome.verification is not None and outcome.verification.verified:
            self.bus.emit(
                EventType.VERIFICATION_PASSED,
                objective_id=objective.id,
                execution_id=outcome.execution.id if outcome.execution else None,
                payload={"independent": True, "step_id": step.id},
            )
        return outcome

    def _alternate_capability(
        self, operation: str, failed_id: str
    ) -> Any | None:
        for capability in self.store.list_capabilities():
            if (
                capability.id != failed_id
                and capability.supports(operation)
                and capability.is_usable()
            ):
                return capability
        return None

    @staticmethod
    def _is_unsafe(execution: Execution) -> bool:
        kinds = {e.kind for e in execution.evidence}
        if "policy_denied" not in kinds:
            return False
        detail = " ".join(e.detail for e in execution.evidence).lower()
        return "destructive" in detail or "approval" in detail

    # ------------------------------------------------------------------
    # recovery entry point
    # ------------------------------------------------------------------
    def recover(
        self, objective_id: str, options: EngineOptions | None = None
    ) -> Report:
        objective = self.store.get_objective(objective_id)
        if objective is None:
            raise AgentOSError(f"unknown objective: {objective_id}")
        if objective.status not in (ObjectiveState.FAILED, ObjectiveState.BLOCKED):
            return Report(
                objective_id=objective.id,
                state=objective.status,
                verification_status=objective.verification_status,
                next_action="objective is not FAILED or BLOCKED; nothing to recover",
                result=objective.result,
            )
        self.bus.emit(
            EventType.RECOVERY_STARTED,
            objective_id=objective.id,
            payload={"from_state": objective.status.value, "strategy": "replan"},
        )
        report = self.run(objective_id, options)
        if report.state == ObjectiveState.COMPLETED:
            self.bus.emit(
                EventType.RECOVERY_COMPLETED,
                objective_id=objective.id,
                payload={"execution_id": report.execution_id},
            )
        return report

    # ------------------------------------------------------------------
    # bounded, class-dependent recovery (Task 8)
    # ------------------------------------------------------------------
    def bounded_recover(
        self,
        objective: Objective,
        adapter_result: AdapterResult,
        max_attempts: int = 3,
    ) -> RecoveryOutcome:
        """Recover per failure class with a hard attempt cap (never infinite).

        Decides via the module-level ``bounded_recover`` (no execution, no
        sleeping — backoff delays are computed, not slept), then persists
        every attempt as a ``Failure`` row, emits the existing
        recovery started/completed events, and leaves the objective in the
        outcome's terminal state. BLOCKED outcomes record a
        ``blocked_reason`` naming the missing credential/config in generic
        terms (never secret values).
        """
        outcome = bounded_recover(objective, adapter_result, max_attempts)
        cap = max(1, min(int(max_attempts), ABSOLUTE_CAP))
        self.bus.emit(
            EventType.RECOVERY_STARTED,
            objective_id=objective.id,
            payload={
                "strategy": outcome.action,
                "failure_type": outcome.failure_type,
                "max_attempts": cap,
                "backoff_seconds": list(outcome.backoff_seconds),
            },
        )
        for attempt in range(1, outcome.attempts_used + 1):
            self.store.save_failure(
                Failure(
                    id=new_id("fail"),
                    objective_id=objective.id,
                    execution_id=None,
                    failure_type=outcome.failure_type,
                    detail=outcome.detail,
                    attempt=attempt,
                    recovered=False,
                )
            )
        objective.status = outcome.terminal_state
        if outcome.terminal_state == ObjectiveState.BLOCKED:
            objective.result = {
                "blocked_reason": outcome.detail,
                "failure_type": outcome.failure_type,
                "recovery_action": outcome.action,
            }
        else:
            objective.result = {
                "failure_type": outcome.failure_type,
                "recovery_action": outcome.action,
                "backoff_seconds": list(outcome.backoff_seconds),
            }
            if outcome.action == "backoff":
                objective.result["retry_after_seconds"] = (
                    sum(outcome.backoff_seconds) if outcome.backoff_seconds else 0.0
                )
        objective.failure = {
            "type": outcome.failure_type,
            "detail": outcome.detail,
            "attempt": outcome.attempts_used,
            "max_attempts": cap,
        }
        objective.next_action = outcome.detail
        objective.mark_updated()
        self.store.save_objective(objective)
        self.bus.emit(
            EventType.RECOVERY_COMPLETED,
            objective_id=objective.id,
            payload={
                "recovered": False,
                "failure_type": outcome.failure_type,
                "action": outcome.action,
                "attempts_used": outcome.attempts_used,
            },
        )
        return outcome

    # ------------------------------------------------------------------
    # re-verification of the latest execution (idempotent inspection)
    # ------------------------------------------------------------------
    def verify_latest(self, objective_id: str) -> VerificationResult:
        objective = self.store.get_objective(objective_id)
        if objective is None:
            raise AgentOSError(f"unknown objective: {objective_id}")
        executions = self.store.list_executions(objective_id=objective_id)
        if not executions:
            raise AgentOSError(f"objective {objective_id} has no executions to verify")
        latest = max(executions, key=lambda e: (e.started_at, e.attempt))
        self.bus.emit(
            EventType.VERIFICATION_STARTED,
            objective_id=objective.id,
            execution_id=latest.id,
            payload={"reverify": True},
        )
        verification = self.verifier.verify(objective, latest)
        self.store.save_verification(verification)
        objective.verification_status = (
            VerificationStatus.VERIFIED
            if verification.verified
            else VerificationStatus.FAILED
        )
        objective.mark_updated()
        self.store.save_objective(objective)
        self.bus.emit(
            EventType.VERIFICATION_PASSED
            if verification.verified
            else EventType.VERIFICATION_FAILED,
            objective_id=objective.id,
            execution_id=latest.id,
            payload={"verification_id": verification.id, "reverify": True},
        )
        return verification

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _select_operation(self, objective: Objective, opts: EngineOptions) -> str:
        if opts.operation:
            return opts.operation
        context_operation = objective.context.get("operation")
        if isinstance(context_operation, str) and context_operation.strip():
            return context_operation.strip()
        for constraint in objective.constraints:
            if isinstance(constraint, str) and constraint.startswith("operation:"):
                value = constraint.split(":", 1)[1].strip()
                if value:
                    return value
        lowered = f"{objective.title} {objective.description}".lower()
        if any(w in lowered for w in ("run", "execute", "command", "shell", "script")):
            return "shell.run"
        if any(w in lowered for w in ("write", "create file", "save")):
            return "fs.write"
        if any(w in lowered for w in ("read", "inspect file", "show file", "list")):
            if "director" in lowered or "list" in lowered:
                return "fs.list"
            return "fs.read"
        if any(
            w in lowered
            for w in ("implement", "code", "build", "refactor", "fix the", "feature")
        ):
            return "opencode.run"
        if any(w in lowered for w in ("repo", "github", "pull request", "issue")):
            return "github.repo"
        return "echo"

    def _build_request(
        self, objective: Objective, capability: Any, step: PlanStep, opts: EngineOptions
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        context_params = objective.context.get("params")
        if isinstance(context_params, dict):
            params.update(context_params)
        params.update(step.params)
        params.setdefault(
            "operation_class", str(objective.context.get("operation_class", "standard"))
        )
        # Capability-declared verification travels with the request so the
        # verifier can honour it without provider-specific code.
        verification = capability.verification or {}
        request: dict[str, Any] = {
            "operation": step.operation,
            "params": params,
            "timeout_seconds": float(step.timeout_seconds or opts.timeout_seconds),
            "verify_method": verification.get("method", ""),
            "verify_expected": verification.get("expected"),
        }
        if capability.source == "configured" and capability.config.get("command"):
            # The shell adapter runs the configured command for real.
            # {placeholders} are substituted from params; scalar params are
            # also exposed as AGENTOS_PARAM_* environment variables.
            template = str(capability.config["command"])
            resolved = template
            for key, value in params.items():
                if isinstance(value, (str, int, float, bool)):
                    resolved = resolved.replace("{" + str(key) + "}", str(value))
            params["command"] = resolved
            env = dict(params.get("env") or {})
            for key, value in params.items():
                if isinstance(value, (str, int, float, bool)):
                    env.setdefault(f"AGENTOS_PARAM_{str(key).upper()}", str(value))
            params["env"] = env
        return request

    def _objective_class(self, operation: str, plan: ExecutionPlan) -> str:
        return f"{operation}:{plan.strategy.value}"

    def _record_pattern(
        self,
        objective: Objective,
        plan: ExecutionPlan,
        operation: str,
        execution: Execution | None,
        verified: bool,
        failure_type: str | None,
        recovery: str | None,
        started: float,
    ) -> None:
        duration_ms = int((time.monotonic() - started) * 1000)
        capability_id = execution.capability_id if execution else "none"
        if verified:
            lesson = f"{capability_id} verified {operation} via {plan.strategy.value}"
        else:
            lesson = (
                f"{capability_id} failed {operation} via {plan.strategy.value} "
                f"({failure_type or 'unknown'})"
            )
        self.store.save_pattern(
            Pattern(
                id=new_id("pat"),
                objective_class=self._objective_class(operation, plan),
                operation=operation,
                strategy=plan.strategy.value,
                capability_id=capability_id,
                verified=verified,
                failure_type=failure_type,
                recovery=recovery,
                duration_ms=duration_ms,
                lesson=lesson,
            )
        )

    def _record_gap(
        self, objective: Objective, operation: str, missing: str
    ) -> GapAnalysis:
        alternatives = sorted(
            {
                c.id
                for c in self.store.list_capabilities()
                if c.is_usable() and c.type.value in ("cli", "tool", "agent")
            }
        )[:5]
        gap = GapAnalysis(
            id=new_id("gap"),
            objective_id=objective.id,
            operation=operation,
            required_capability=f"capability supporting {operation!r}",
            why_required=(
                f"objective {objective.id} requires operation {operation!r} "
                f"but {missing}"
            ),
            alternatives=alternatives,
            missing_interface=f"adapter + capability record for {operation!r}",
            implementation_paths=[
                f"implement Adapter '{operation.split('.')[0]}' per adapters/base.py protocol",
                "register capability record via registry with probed health",
                "add a verification method covering the new output shape",
            ],
            risk="new adapter executes untested provider behaviour; gate with policy + timeouts",
            verification_plan=(
                "unit-test adapter with fixtures; shim-test selection; "
                "live-run once with bounded timeout; record pattern"
            ),
        )
        self.store.save_gap(gap)
        self.bus.emit(
            EventType.CAPABILITY_MISSING,
            objective_id=objective.id,
            payload={"operation": operation, "gap_id": gap.id},
        )
        return gap

    # ------------------------------------------------------------------
    # terminal states
    # ------------------------------------------------------------------
    def _completed(
        self,
        objective: Objective,
        execution: Execution,
        verification: VerificationResult,
        started: float,
        plan: ExecutionPlan,
    ) -> Report:
        execution.status = ExecutionStatus.VERIFIED
        execution.finish(ExecutionStatus.VERIFIED)
        self.store.save_execution(execution)
        self.store.mark_failures_recovered(objective.id)
        objective.status = ObjectiveState.COMPLETED
        objective.verification_status = VerificationStatus.VERIFIED
        objective.result = dict(execution.output)
        objective.evidence = list(verification.evidence)
        objective.next_action = None
        objective.failure = None
        objective.mark_updated()
        self.store.save_objective(objective)
        self.bus.emit(
            EventType.VERIFICATION_PASSED,
            objective_id=objective.id,
            execution_id=execution.id,
            payload={"verification_id": verification.id, "method": verification.method},
        )
        self.bus.emit(
            EventType.OBJECTIVE_COMPLETED,
            objective_id=objective.id,
            execution_id=execution.id,
            payload={"strategy": plan.strategy.value, "plan_id": plan.id},
        )
        self._record_pattern(
            objective, plan, execution.operation, execution, True, None,
            "retry" if execution.attempt > 1 else None, started,
        )
        self._postprocess_success(objective, execution, plan, started)
        trace = self.store.get_job_trace(objective.id)
        if trace is not None:
            trace["finalStatus"] = "VERIFIED_COMPLETE"
            self._save_trace(trace)
        return Report(
            objective_id=objective.id,
            state=ObjectiveState.COMPLETED,
            verification_status=VerificationStatus.VERIFIED,
            capability_id=execution.capability_id,
            execution_id=execution.id,
            attempt=execution.attempt,
            next_action=None,
            result=dict(execution.output),
        )

    def _failed(
        self,
        objective: Objective,
        execution: Execution,
        verification: VerificationResult,
        failure_type: str,
        attempt: int,
        max_attempts: int,
        started: float,
        plan: ExecutionPlan,
    ) -> Report:
        execution.status = ExecutionStatus.FAILED
        execution.finish(ExecutionStatus.FAILED)
        self.store.save_execution(execution)
        next_action = suggest_next_action(
            failure_type, attempt, max_attempts, execution.operation
        )
        objective.status = ObjectiveState.FAILED
        objective.verification_status = VerificationStatus.FAILED
        objective.next_action = next_action
        objective.failure = {
            "type": failure_type,
            "detail": "; ".join(e.detail for e in verification.evidence),
            "attempt": attempt,
            "max_attempts": max_attempts,
        }
        objective.evidence = list(verification.evidence)
        objective.mark_updated()
        self.store.save_objective(objective)
        self._record_pattern(
            objective, plan, execution.operation, execution, False,
            failure_type, None, started,
        )
        self._postprocess_failure(objective, execution, plan, failure_type, started)
        trace = self.store.get_job_trace(objective.id)
        if trace is not None:
            trace["finalStatus"] = "FAILED"
            escalation_outcome = (
                "ESCALATE_HUMAN"
                if failure_type in ("AUTH_REQUIRED", "POLICY_BLOCK")
                else "ESCALATE_SUPERVISOR"
            )
            trace.setdefault("escalations", []).append({
                "outcome": escalation_outcome,
                "reason": failure_type,
                "attempt": attempt,
                "maxAttempts": max_attempts,
            })
            self._save_trace(trace)
            if escalation_outcome == "ESCALATE_HUMAN":
                self.store.bump_intelligence_counter("human_escalation", 1)
                self.store.bump_intelligence_counter("human_attention_total", 1)
        self.bus.emit(
            EventType.RECOVERY_COMPLETED,
            objective_id=objective.id,
            execution_id=execution.id,
            payload={"recovered": False, "failure_type": failure_type},
        )
        self.bus.emit(
            EventType.OBJECTIVE_FAILED,
            objective_id=objective.id,
            execution_id=execution.id,
            payload={
                "failure_type": failure_type,
                "attempt": attempt,
                "capability_id": execution.capability_id,
                "operation": execution.operation,
            },
        )
        return Report(
            objective_id=objective.id,
            state=ObjectiveState.FAILED,
            verification_status=VerificationStatus.FAILED,
            capability_id=execution.capability_id,
            execution_id=execution.id,
            attempt=attempt,
            next_action=next_action,
            failure=dict(objective.failure),
        )

    def _failed_no_execution(
        self, objective: Objective, operation: str, detail: str, plan: ExecutionPlan
    ) -> Report:
        objective.status = ObjectiveState.FAILED
        objective.verification_status = VerificationStatus.FAILED
        objective.next_action = detail
        objective.failure = {"type": "no_execution", "detail": detail}
        objective.mark_updated()
        self.store.save_objective(objective)
        self.bus.emit(
            EventType.OBJECTIVE_FAILED,
            objective_id=objective.id,
            payload={"failure_type": "no_execution"},
        )
        return Report(
            objective_id=objective.id,
            state=ObjectiveState.FAILED,
            verification_status=VerificationStatus.FAILED,
            next_action=detail,
            failure=dict(objective.failure),
        )

    def _blocked(self, objective: Objective, operation: str, plan: ExecutionPlan) -> Report:
        gap = self._record_gap(objective, operation, "no capability supports it")
        next_action = (
            f"no capability supports operation {operation!r}; "
            f"gap analysis {gap.id} recorded; register a capability, then run recover"
        )
        objective.status = ObjectiveState.BLOCKED
        objective.next_action = next_action
        objective.mark_updated()
        self.store.save_objective(objective)
        return Report(
            objective_id=objective.id,
            state=ObjectiveState.BLOCKED,
            verification_status=objective.verification_status,
            next_action=next_action,
        )

    def _blocked_unsafe(self, objective: Objective, execution: Execution) -> Report:
        next_action = (
            "execution refused by policy (destructive or approval-required); "
            "grant approval via policy configuration, then run recover. "
            "AgentOS stops here rather than acting without authority."
        )
        objective.status = ObjectiveState.BLOCKED
        objective.next_action = next_action
        objective.mark_updated()
        self.store.save_objective(objective)
        self.bus.emit(
            EventType.OBJECTIVE_FAILED,
            objective_id=objective.id,
            execution_id=execution.id,
            payload={"failure_type": "policy", "stopped": "unsafe"},
        )
        return Report(
            objective_id=objective.id,
            state=ObjectiveState.BLOCKED,
            verification_status=objective.verification_status,
            capability_id=execution.capability_id,
            execution_id=execution.id,
            next_action=next_action,
        )

    def _failed_no_adapter(
        self, objective: Objective, capability_id: str, operation: str, plan: ExecutionPlan
    ) -> Report:
        next_action = (
            f"capability {capability_id!r} has no working adapter; "
            "register an adapter for it, then run recover"
        )
        objective.status = ObjectiveState.FAILED
        objective.verification_status = VerificationStatus.FAILED
        objective.next_action = next_action
        objective.failure = {"type": "no_adapter", "detail": next_action}
        objective.mark_updated()
        self.store.save_objective(objective)
        self.bus.emit(
            EventType.OBJECTIVE_FAILED,
            objective_id=objective.id,
            payload={"failure_type": "no_adapter", "capability_id": capability_id},
        )
        return Report(
            objective_id=objective.id,
            state=ObjectiveState.FAILED,
            verification_status=VerificationStatus.FAILED,
            capability_id=capability_id,
            next_action=next_action,
            failure=dict(objective.failure),
        )