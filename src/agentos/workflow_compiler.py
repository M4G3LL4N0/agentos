"""Workflow Compiler - compile, match, version workflows from repeated execution."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from agentos.models import (
    WorkflowDefinition,
    WorkflowStatus,
    WorkflowMaturity,
    utc_now_iso,
    new_id,
)
from agentos.store import Store


@dataclass
class WorkflowMatch:
    """Result of workflow matching."""
    workflow: WorkflowDefinition | None
    match_score: float
    reason: str


def compile_workflow_from_executions(task_class: str,
                                     executions: list[dict[str, Any]],
                                     store: Store) -> WorkflowDefinition | None:
    """Compile a workflow from successful executions of the same task class.

    Requires at least 3 successful executions with same strategy.
    """
    if len(executions) < 3:
        return None

    # Filter successful executions
    successful = [e for e in executions if e.get("status") == "VERIFIED_COMPLETE"]
    if len(successful) < 3:
        return None

    # Check if they share a common strategy
    strategies = [e.get("strategy") for e in successful]
    if len(set(strategies)) > 1:
        # Multiple strategies - can't compile single workflow
        return None

    # Extract common steps
    all_steps = []
    for e in successful:
        steps = e.get("plan_steps", [])
        if steps:
            all_steps.append(steps)

    if not all_steps:
        return None

    # Find common step sequence
    common_steps = _find_common_sequence(all_steps)
    if len(common_steps) < 2:
        return None

    # Extract capability requirements
    capability_reqs = set()
    for e in successful:
        cap = e.get("capability_id")
        if cap:
            capability_reqs.add(cap)

    # Build workflow
    workflow = WorkflowDefinition(
        id=new_id("wf"),
        name=f"{task_class}_workflow",
        task_class=task_class,
        version=1,
        inputs=_extract_common_inputs(successful),
        preconditions=_extract_preconditions(successful),
        steps=common_steps,
        capability_requirements=list(capability_reqs),
        preferred_executors=_preferred_executors(successful),
        fallback_executors=_fallback_executors(successful),
        cache_policy="exact",
        approval_policy="none",
        verification_policy="test",
        outputs=_extract_common_outputs(successful),
        success_evidence=_extract_evidence_refs(successful),
        failure_handling={"retry": "same_worker", "max_retries": 2},
        metrics={"compiled_from": len(successful)},
        status=WorkflowStatus.OBSERVED,
        maturity=WorkflowMaturity.FIRST_RUN,
        created_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )

    return workflow


def _find_common_sequence(step_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Find the longest common subsequence of steps across executions."""
    if not step_lists:
        return []

    # Use first as baseline
    base = step_lists[0]
    common = []

    for index, step in enumerate(base):
        matches_all = all(
            index < len(other) and _steps_match(step, other[index])
            for other in step_lists[1:]
        )
        if matches_all:
            common.append(step)

    return common


def _steps_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Check ordered step equivalence, including behavior-affecting fields."""
    return (
        a.get("operation") == b.get("operation")
        and a.get("capability_id") == b.get("capability_id")
        and a.get("params", {}) == b.get("params", {})
        and list(a.get("depends_on") or []) == list(b.get("depends_on") or [])
    )


def _extract_common_inputs(executions: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract common input schema from executions."""
    if not executions:
        return {}

    # Find keys present in all executions
    all_keys = [set(e.get("request", {}).keys()) for e in executions]
    common_keys = set.intersection(*all_keys) if all_keys else set()

    # Build schema with type info
    schema = {}
    for key in common_keys:
        types = {type(e.get("request", {}).get(key)).__name__ for e in executions}
        schema[key] = {"type": list(types)[0] if len(types) == 1 else "any"}
    return schema


def _extract_preconditions(executions: list[dict[str, Any]]) -> list[str]:
    """Extract preconditions from successful executions."""
    preconditions = ["cache_available"]
    # Add any capability readiness checks
    caps = {e.get("capability_id") for e in executions if e.get("capability_id")}
    for cap in caps:
        preconditions.append(f"capability_ready:{cap}")
    for key in sorted({
        str(key)
        for execution in executions
        for key in (execution.get("request") or {})
    }):
        precondition = f"input:{key}"
        if precondition not in preconditions:
            preconditions.append(precondition)
    return preconditions


def _extract_common_outputs(executions: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract common output schema."""
    if not executions:
        return {}

    all_keys = [set(e.get("output", {}).keys()) for e in executions]
    common_keys = set.intersection(*all_keys) if all_keys else set()

    schema = {}
    for key in common_keys:
        types = {type(e.get("output", {}).get(key)).__name__ for e in executions}
        schema[key] = {"type": list(types)[0] if len(types) == 1 else "any"}
    return schema


def _extract_evidence_refs(executions: list[dict[str, Any]]) -> list[str]:
    """Extract evidence refs from successful executions."""
    refs = []
    for e in executions:
        refs.extend(e.get("evidence_refs", []))
    return list(set(refs))[:20]  # Limit


def _preferred_executors(executions: list[dict[str, Any]]) -> list[str]:
    """Extract preferred executors from successful runs."""
    executors = {}
    for e in executions:
        cap = e.get("capability_id")
        if cap:
            executors[cap] = executors.get(cap, 0) + 1
    return sorted(executors.keys(), key=lambda k: executors[k], reverse=True)[:3]


def _fallback_executors(executions: list[dict[str, Any]]) -> list[str]:
    """Extract fallback executors (less used but successful)."""
    executors = {}
    for e in executions:
        cap = e.get("capability_id")
        if cap:
            executors[cap] = executors.get(cap, 0) + 1
    return sorted(executors.keys(), key=lambda k: executors[k])[:2]


def match_workflow(task_class: str, inputs: dict[str, Any],
                   store: Store) -> WorkflowMatch:
    """Find matching workflow for task class and inputs."""
    workflows = [
        workflow
        for status in (
            WorkflowStatus.OBSERVED,
            WorkflowStatus.VERIFIED,
            WorkflowStatus.CERTIFIED,
        )
        for workflow in store.list_workflows(task_class=task_class, status=status)
    ]

    if not workflows:
        return WorkflowMatch(None, 0.0, "no eligible workflows for task class")

    best_match = None
    best_score = 0.0

    for wf in workflows:
        if any(
            precondition.startswith("capability_ready:")
            and not store._check_precondition(precondition, inputs)
            for precondition in wf.preconditions
        ):
            continue
        score = _score_workflow_match(wf, inputs, store)
        if score > best_score:
            best_score = score
            best_match = wf

    if best_match and best_score >= 0.7:
        return WorkflowMatch(best_match, best_score, "preconditions met")
    elif best_match:
        return WorkflowMatch(best_match, best_score, "partial match - preconditions not fully met")
    return WorkflowMatch(None, 0.0, "no matching workflow")


def _score_workflow_match(
    workflow: WorkflowDefinition,
    inputs: dict[str, Any],
    store: Store,
) -> float:
    """Score workflow match based on preconditions and input compatibility."""
    score = 0.0

    # Preconditions check
    precond_met = 0
    for precondition in workflow.preconditions:
        if precondition == "cache_available":
            precond_met += 1
        elif precondition.startswith("capability_ready:"):
            capability_id = precondition.split(":", 1)[1]
            capability = store.get_capability(capability_id)
            if capability is not None and capability.is_usable():
                precond_met += 1
        elif precondition.startswith("input:"):
            key = precondition.split(":", 1)[1]
            if key in inputs:
                precond_met += 1

    if workflow.preconditions:
        score += 0.5 * (precond_met / len(workflow.preconditions))

    if workflow.inputs:
        matched = sum(1 for key in workflow.inputs if key in inputs)
        score += 0.5 * (matched / len(workflow.inputs))

    return score


class WorkflowCompiler:
    """Orchestrates workflow compilation and management."""

    def __init__(self, store: Store):
        self.store = store

    def try_compile(self, task_class: str) -> WorkflowDefinition | None:
        rows = self.store.conn.execute(
            """
            SELECT p.strategy, p.steps_json, o.context_json, o.result_json,
                   o.evidence_json
            FROM plans p
            JOIN objectives o ON o.id = p.objective_id
            WHERE o.status = ?
              AND o.verification_status = ?
              AND json_extract(o.context_json, '$.taskClass') = ?
            ORDER BY p.created_at DESC
            LIMIT 100
            """,
            ("COMPLETED", "VERIFIED", str(task_class)),
        ).fetchall()
        executions: list[dict[str, Any]] = []
        for row in rows:
            steps = json.loads(row["steps_json"] or "[]")
            context = json.loads(row["context_json"] or "{}")
            result = json.loads(row["result_json"] or "{}")
            evidence = json.loads(row["evidence_json"] or "[]")
            executions.append({
                "status": "VERIFIED_COMPLETE",
                "strategy": row["strategy"],
                "plan_steps": steps,
                "request": dict(context.get("params") or {}),
                "output": dict(result) if isinstance(result, dict) else {},
                "capability_id": next(
                    (
                        step.get("capability_id")
                        for step in steps
                        if isinstance(step, dict) and step.get("capability_id")
                    ),
                    None,
                ),
                "evidence_refs": [
                    str(item.get("id") or item.get("source"))
                    for item in evidence
                    if isinstance(item, dict) and (item.get("id") or item.get("source"))
                ],
            })
        return self.compile_from_history(task_class, executions)

    def compile_from_history(self, task_class: str,
                             executions: list[dict[str, Any]]) -> WorkflowDefinition | None:
        """Compile workflow from provided execution history."""
        wf = compile_workflow_from_executions(task_class, executions, self.store)
        if wf:
            self.store.save_workflow(wf)
        return wf

    def promote_workflow(self, workflow_id: str, new_status: WorkflowStatus) -> bool:
        """Promote workflow status only from measured evidence."""
        wf = self.store.get_workflow(workflow_id)
        if not wf:
            return False
        metrics = wf.metrics or {}
        uses = int(metrics.get("uses", 0) or 0)
        successes = int(metrics.get("successes", 0) or 0)
        success_rate = successes / uses if uses else 0.0
        legal = {
            (WorkflowStatus.DRAFT, WorkflowStatus.OBSERVED),
            (WorkflowStatus.OBSERVED, WorkflowStatus.VERIFIED),
            (WorkflowStatus.VERIFIED, WorkflowStatus.CERTIFIED),
        }
        if (wf.status, new_status) not in legal:
            return False
        if new_status == WorkflowStatus.VERIFIED and not (uses >= 5 and success_rate >= 0.9):
            return False
        if new_status == WorkflowStatus.CERTIFIED and not (uses >= 10 and success_rate >= 0.9):
            return False
        wf.status = new_status
        wf.updated_at = utc_now_iso()
        if new_status == WorkflowStatus.VERIFIED:
            wf.maturity = WorkflowMaturity.REPEAT
        elif new_status == WorkflowStatus.CERTIFIED:
            wf.maturity = WorkflowMaturity.MATURE
        self.store.save_workflow(wf)
        return True

    def advance_maturity(self, workflow_id: str) -> bool:
        """Advance maturity only when usage supports the next level."""
        wf = self.store.get_workflow(workflow_id)
        if not wf:
            return False
        uses = int((wf.metrics or {}).get("uses", 0) or 0)
        successes = int((wf.metrics or {}).get("successes", 0) or 0)
        if wf.maturity == WorkflowMaturity.FIRST_RUN and uses >= 5 and successes / max(1, uses) >= 0.9:
            wf.maturity = WorkflowMaturity.REPEAT
        elif wf.maturity == WorkflowMaturity.REPEAT and uses >= 10 and successes / max(1, uses) >= 0.9:
            wf.maturity = WorkflowMaturity.MATURE
        else:
            return False

        wf.updated_at = utc_now_iso()
        self.store.save_workflow(wf)
        return True

    def record_workflow_use(self, workflow_id: str, success: bool) -> None:
        """Record workflow usage for maturity tracking."""
        wf = self.store.get_workflow(workflow_id)
        if not wf:
            return

        wf.metrics = dict(wf.metrics or {})
        wf.metrics["uses"] = wf.metrics.get("uses", 0) + 1
        if success:
            wf.metrics["successes"] = wf.metrics.get("successes", 0) + 1
        else:
            wf.metrics["failures"] = wf.metrics.get("failures", 0) + 1

        # Auto-promote on repeated success
        uses = wf.metrics.get("uses", 0)
        successes = wf.metrics.get("successes", 0)
        if uses >= 5 and successes / uses >= 0.9:
            if wf.status == WorkflowStatus.OBSERVED:
                wf.status = WorkflowStatus.VERIFIED
                wf.maturity = WorkflowMaturity.REPEAT
            elif wf.status == WorkflowStatus.VERIFIED and uses >= 10:
                wf.status = WorkflowStatus.CERTIFIED
                wf.maturity = WorkflowMaturity.MATURE

        wf.updated_at = utc_now_iso()
        self.store.save_workflow(wf)

    def get_workflow_stats(self, task_class: str | None = None) -> dict[str, Any]:
        """Get workflow compilation statistics."""
        workflows = self.store.list_workflows(task_class=task_class)
        return {
            "total": len(workflows),
            "by_status": {
                s.value: sum(1 for w in workflows if w.status == s)
                for s in WorkflowStatus
            },
            "by_maturity": {
                m.value: sum(1 for w in workflows if w.maturity == m)
                for m in WorkflowMaturity
            },
            "total_uses": sum(w.metrics.get("uses", 0) for w in workflows),
            "total_successes": sum(w.metrics.get("successes", 0) for w in workflows),
        }