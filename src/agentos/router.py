"""Capability-aware routing for AgentOS.

Given an objective, constraints, available capabilities, health, history,
cost/latency signals and permissions, the router determines an execution
strategy and records *why* a capability was selected. Routing is
data-driven, never arbitrary: every plan carries its rationale.

Strategies: DIRECT, SEQUENTIAL, PARALLEL, DELEGATED, ITERATIVE,
RESEARCH_THEN_EXECUTE, BUILD_THEN_VERIFY, REVIEW_THEN_REVISE, GRAPH.

Temporary roles (researcher, architect, builder, tester, evaluator,
red_team, analyst) are assignments inside a plan, not persistent agents.
The topology contracts when the objective completes.
"""

from __future__ import annotations

from typing import Any

from agentos.models import (
    Capability,
    ExecutionPlan,
    HealthStatus,
    Objective,
    PlanStep,
    Role,
    Strategy,
    new_id,
    utc_now_iso,
)
from agentos.store import Store

HEALTH_SCORE = {
    HealthStatus.AVAILABLE: 100,
    HealthStatus.OK: 100,
    HealthStatus.UNKNOWN: 60,
    HealthStatus.DEGRADED: 30,
    HealthStatus.AUTH_REQUIRED: 10,
    HealthStatus.MISCONFIGURED: 5,
    HealthStatus.DOWN: 0,
    HealthStatus.UNAVAILABLE: 0,
    HealthStatus.DISABLED: 0,
}

DELEGATED_OPERATIONS = (
    "opencode.run",
    "grok.run",
    "openclaw.run",
    "github.",
    "xai.",
)

ROLE_FOR_STEP = {
    "research": Role.RESEARCHER,
    "execute": Role.BUILDER,
    "build": Role.BUILDER,
    "verify": Role.TESTER,
    "evaluate": Role.EVALUATOR,
    "delegate": Role.ARCHITECT,
    "review": Role.EVALUATOR,
    "redteam": Role.RED_TEAM,
    "analyze": Role.ANALYST,
}


def score_capability(
    capability: Capability, stats: dict[str, dict[str, int]]
) -> tuple[float, list[str]]:
    """Score a capability; returns (score, reason_parts)."""
    parts: list[str] = []
    score = float(HEALTH_SCORE.get(capability.health, 0))
    parts.append(f"health={capability.health.value}({HEALTH_SCORE.get(capability.health, 0)})")
    history = stats.get(capability.id)
    if history and history["runs"] > 0:
        rate = history["verified"] / history["runs"]
        bonus = (rate - 0.5) * 40.0
        score += bonus
        parts.append(
            f"history={history['verified']}/{history['runs']} verified({bonus:+.0f})"
        )
    else:
        parts.append("history=none")
    cost = capability.cost or {}
    latency = capability.latency or {}
    cost_hint = cost.get("per_run")
    if isinstance(cost_hint, (int, float)):
        penalty = min(20.0, float(cost_hint))
        score -= penalty
        parts.append(f"cost({penalty:.0f})")
    latency_hint = latency.get("p50_ms")
    if isinstance(latency_hint, (int, float)) and latency_hint > 0:
        penalty = min(10.0, float(latency_hint) / 1000.0)
        score -= penalty
        parts.append(f"latency({penalty:.1f})")
    if not capability.availability:
        score = 0.0
        parts.append("disabled")
    return score, parts


def detect_write_conflicts(items: list[dict[str, Any]]) -> list[str]:
    """Find filesystem paths written by more than one parallel item."""
    seen: dict[str, int] = {}
    for item in items:
        params = item.get("params", {}) if isinstance(item, dict) else {}
        path = params.get("path") if isinstance(params, dict) else None
        if path:
            seen[str(path)] = seen.get(str(path), 0) + 1
    return sorted(p for p, n in seen.items() if n > 1)


class Router:
    """Plans execution strategies for objectives."""

    def __init__(self, store: Store) -> None:
        self.store = store

    # ------------------------------------------------------------------
    def plan(
        self,
        objective: Objective,
        operation: str,
        capabilities: list[Capability],
        max_attempts: int = 3,
    ) -> ExecutionPlan:
        context = objective.context or {}
        requested = self._requested_strategy(context)
        items = context.get("items") if isinstance(context.get("items"), list) else None
        steps_spec = context.get("steps") if isinstance(context.get("steps"), list) else None

        if requested == Strategy.GRAPH:
            return self._graph_plan(objective, operation, capabilities)
        if steps_spec:
            return self._sequential_plan(
                objective, operation, capabilities, steps_spec, Strategy.SEQUENTIAL
            )
        if items:
            return self._parallel_or_sequential(objective, operation, capabilities, items)
        if self._is_delegated(operation):
            return self._single_step_plan(
                objective, operation, capabilities, Strategy.DELEGATED, max_attempts
            )
        if context.get("research") or operation.startswith("research"):
            return self._research_then_execute(objective, operation, capabilities)
        if context.get("review"):
            return self._review_then_revise(objective, operation, capabilities)
        if context.get("iterate") or context.get("max_iterations"):
            return self._iterative_plan(objective, operation, capabilities, context)
        if self._looks_like_build(objective):
            return self._build_then_verify(objective, operation, capabilities)
        return self._single_step_plan(
            objective, operation, capabilities, Strategy.DIRECT, max_attempts
        )

    # ------------------------------------------------------------------
    def _requested_strategy(self, context: dict[str, Any]) -> Strategy | None:
        raw = context.get("strategy")
        if isinstance(raw, str):
            try:
                return Strategy(raw.upper())
            except ValueError:
                return None
        return None

    @staticmethod
    def _is_delegated(operation: str) -> bool:
        return operation.startswith(DELEGATED_OPERATIONS)

    @staticmethod
    def _looks_like_build(objective: Objective) -> bool:
        text = f"{objective.title} {objective.description}".lower()
        return any(
            phrase in text
            for phrase in (
                "implement and test",
                "build and verify",
                "create and verify",
                "write and test",
                "implement and verify",
            )
        )

    # ------------------------------------------------------------------
    def _learned_adjustment(self, capability_id: str, operation: str) -> tuple[float, str]:
        metrics = self.store.get_executor_metrics(
            executor=capability_id, task_class=operation, limit=200
        )
        if not metrics:
            return 0.0, "learned=no_history"
        total = len(metrics)
        success_rate = sum(1 for row in metrics if row.get("success")) / total
        verified_rate = sum(
            1 for row in metrics if row.get("verification_result") == "VERIFIED"
        ) / total
        retry_rate = sum(int(row.get("retry_count") or 0) for row in metrics) / total
        correction_rate = sum(int(row.get("human_correction") or 0) for row in metrics) / total
        latency = sum(int(row.get("latency_ms") or 0) for row in metrics) / total
        context = sum(int(row.get("context_size") or 0) for row in metrics) / total
        adjustment = (
            success_rate * 10.0
            + verified_rate * 10.0
            - min(10.0, retry_rate * 2.0)
            - min(8.0, correction_rate * 2.0)
            - min(5.0, latency / 1000.0)
            - min(5.0, context / 5000.0)
        )
        adjustment = max(-25.0, min(25.0, adjustment))
        return adjustment, (
            f"learned={adjustment:+.1f}(pass={success_rate:.2f},"
            f"verify={verified_rate:.2f},retry={retry_rate:.2f},"
            f"correction={correction_rate:.2f},latency={latency:.0f}ms)"
        )

    def _ranked(
        self, operation: str, capabilities: list[Capability]
    ) -> list[tuple[Capability, float, str]]:
        stats = self.store.capability_stats(operation)
        ranked: list[tuple[Capability, float, str]] = []
        for capability in capabilities:
            if not capability.supports(operation) or not capability.availability:
                continue
            score, parts = score_capability(capability, stats)
            learned_score, learned_reason = self._learned_adjustment(
                capability.id, operation
            )
            if capability.is_usable():
                score += learned_score
            parts.append(learned_reason)
            ranked.append((capability, score, "; ".join(parts)))
        ranked.sort(key=lambda item: (-item[1], item[0].id))
        return ranked

    def _best(
        self, operation: str, capabilities: list[Capability]
    ) -> tuple[Capability | None, str]:
        ranked = self._ranked(operation, capabilities)
        usable = [r for r in ranked if r[0].is_usable()]
        if not usable:
            names = ", ".join(c.id for c in capabilities if c.supports(operation))
            return None, (
                f"no usable capability for {operation!r}"
                + (f" (known but unusable: {names})" if names else " (unknown operation)")
            )
        capability, score, breakdown = usable[0]
        alternatives = ", ".join(
            f"{c.id}({s:.0f})" for c, s, _ in usable[1:3]
        )
        rationale = (
            f"selected {capability.id} score={score:.0f} [{breakdown}]"
            + (f"; alternatives: {alternatives}" if alternatives else "; no alternatives")
        )
        return capability, rationale

    @staticmethod
    def _role_for(kind: str, operation: str) -> Role | None:
        if kind in ROLE_FOR_STEP:
            return ROLE_FOR_STEP[kind]
        if operation.startswith("research"):
            return Role.RESEARCHER
        return None

    def _step(
        self,
        kind: str,
        operation: str,
        capabilities: list[Capability],
        params: dict[str, Any] | None = None,
        timeout: float = 120.0,
    ) -> tuple[PlanStep, str]:
        capability, rationale = self._best(operation, capabilities)
        return (
            PlanStep(
                id=new_id("step"),
                kind=kind,
                operation=operation,
                capability_id=capability.id if capability else None,
                role=self._role_for(kind, operation),
                params=dict(params or {}),
                timeout_seconds=timeout,
            ),
            rationale,
        )

    # ------------------------------------------------------------------
    def _single_step_plan(
        self,
        objective: Objective,
        operation: str,
        capabilities: list[Capability],
        strategy: Strategy,
        max_attempts: int,
    ) -> ExecutionPlan:
        step, rationale = self._step(
            "execute",
            operation,
            capabilities,
            params=dict(objective.context.get("params", {}))
            if isinstance(objective.context.get("params"), dict)
            else {},
            timeout=float(objective.context.get("timeout_seconds", 120.0)),
        )
        roles = [step.role] if step.role else []
        return ExecutionPlan(
            id=new_id("plan"),
            objective_id=objective.id,
            strategy=strategy,
            steps=[step],
            rationale=(
                f"strategy={strategy.value}: single execution step. {rationale}. "
                f"max_attempts={max_attempts}."
            ),
            roles=roles,
        )

    def _sequential_plan(
        self,
        objective: Objective,
        operation: str,
        capabilities: list[Capability],
        steps_spec: list[Any],
        strategy: Strategy,
    ) -> ExecutionPlan:
        steps: list[PlanStep] = []
        notes: list[str] = []
        for index, spec in enumerate(steps_spec[:10]):
            if not isinstance(spec, dict):
                continue
            op = str(spec.get("operation", operation))
            kind = str(spec.get("kind", "operation"))
            params = spec.get("params") if isinstance(spec.get("params"), dict) else {}
            step, rationale = self._step(kind, op, capabilities, params)
            steps.append(step)
            notes.append(f"step{index + 1}({kind}:{op})->{step.capability_id or 'none'}")
        if not steps:
            return self._single_step_plan(
                objective, operation, capabilities, Strategy.DIRECT, 3
            )
        roles = [s.role for s in steps if s.role]
        return ExecutionPlan(
            id=new_id("plan"),
            objective_id=objective.id,
            strategy=strategy,
            steps=steps,
            rationale=(
                f"strategy={strategy.value}: {len(steps)} ordered steps "
                f"({'; '.join(notes)}). Steps stop on first failure."
            ),
            roles=roles,
        )

    def _parallel_or_sequential(
        self,
        objective: Objective,
        operation: str,
        capabilities: list[Capability],
        items: list[Any],
    ) -> ExecutionPlan:
        clean = [i for i in items[:10] if isinstance(i, dict)]
        conflicts = detect_write_conflicts(clean)
        capability, cap_rationale = self._best(operation, capabilities)
        steps = [
            PlanStep(
                id=new_id("step"),
                kind="operation",
                operation=operation,
                capability_id=capability.id if capability else None,
                role=Role.BUILDER,
                params=item.get("params", {}) if isinstance(item.get("params"), dict) else {},
                timeout_seconds=float(objective.context.get("timeout_seconds", 120.0)),
            )
            for item in clean
        ]
        if conflicts:
            return ExecutionPlan(
                id=new_id("plan"),
                objective_id=objective.id,
                strategy=Strategy.SEQUENTIAL,
                steps=steps,
                rationale=(
                    "strategy=SEQUENTIAL (parallel requested via items, but "
                    f"write conflicts on {conflicts}; mutations serialized). "
                    f"{cap_rationale}."
                ),
                roles=[Role.BUILDER],
            )
        return ExecutionPlan(
            id=new_id("plan"),
            objective_id=objective.id,
            strategy=Strategy.PARALLEL,
            steps=steps,
            rationale=(
                f"strategy=PARALLEL: {len(steps)} independent items, no write "
                f"conflicts. {cap_rationale}."
            ),
            roles=[Role.BUILDER],
        )

    def _research_then_execute(
        self, objective: Objective, operation: str, capabilities: list[Capability]
    ) -> ExecutionPlan:
        research_op = str(objective.context.get("research_operation", "github.inspect"))
        research_step, research_rationale = self._step(
            "research", research_op, capabilities,
            params=dict(objective.context.get("research_params", {}))
            if isinstance(objective.context.get("research_params"), dict)
            else {},
        )
        execute_step, execute_rationale = self._step(
            "execute", operation, capabilities,
            params=dict(objective.context.get("params", {}))
            if isinstance(objective.context.get("params"), dict)
            else {},
        )
        roles = [r for r in (research_step.role, execute_step.role) if r]
        return ExecutionPlan(
            id=new_id("plan"),
            objective_id=objective.id,
            strategy=Strategy.RESEARCH_THEN_EXECUTE,
            steps=[research_step, execute_step],
            rationale=(
                "strategy=RESEARCH_THEN_EXECUTE: gather context first, then act. "
                f"research: {research_rationale}. execute: {execute_rationale}."
            ),
            roles=roles,
        )

    def _build_then_verify(
        self, objective: Objective, operation: str, capabilities: list[Capability]
    ) -> ExecutionPlan:
        build_step, build_rationale = self._step(
            "build", operation, capabilities,
            params=dict(objective.context.get("params", {}))
            if isinstance(objective.context.get("params"), dict)
            else {},
        )
        verify_step = self._independent_verify_step(
            objective, operation, capabilities, build_step.capability_id
        )
        roles = [r for r in (build_step.role, verify_step.role) if r]
        return ExecutionPlan(
            id=new_id("plan"),
            objective_id=objective.id,
            strategy=Strategy.BUILD_THEN_VERIFY,
            steps=[build_step, verify_step],
            rationale=(
                "strategy=BUILD_THEN_VERIFY: build, then verify with an "
                f"independent capability. build: {build_rationale}. verify: "
                f"{verify_step.capability_id or 'none'}."
            ),
            roles=roles,
        )

    def _independent_verify_step(
        self,
        objective: Objective,
        operation: str,
        capabilities: list[Capability],
        builder_id: str | None,
    ) -> PlanStep:
        """A verify step using a *different* capability than the builder."""
        params = dict(objective.context.get("params", {})) if isinstance(
            objective.context.get("params"), dict
        ) else {}
        path = params.get("path") or objective.context.get("path")
        if path and builder_id != "shell":
            shell_cap, _ = self._best("shell.run", capabilities)
            if shell_cap is not None:
                return PlanStep(
                    id=new_id("step"),
                    kind="verify",
                    operation="shell.run",
                    capability_id=shell_cap.id,
                    role=Role.TESTER,
                    params={"command": f"test -f {path} && echo verified"},
                )
        if path:
            fs_cap, _ = self._best("fs.exists", capabilities)
            if fs_cap is not None and fs_cap.id != builder_id:
                return PlanStep(
                    id=new_id("step"),
                    kind="verify",
                    operation="fs.exists",
                    capability_id=fs_cap.id,
                    role=Role.TESTER,
                    params={"path": str(path)},
                )
        echo_cap, _ = self._best("echo", capabilities)
        return PlanStep(
            id=new_id("step"),
            kind="verify",
            operation="echo",
            capability_id=echo_cap.id if echo_cap else None,
            role=Role.TESTER,
            params={"check": "structural"},
        )

    def _review_then_revise(
        self, objective: Objective, operation: str, capabilities: list[Capability]
    ) -> ExecutionPlan:
        execute_step, execute_rationale = self._step(
            "execute", operation, capabilities,
            params=dict(objective.context.get("params", {}))
            if isinstance(objective.context.get("params"), dict)
            else {},
        )
        evaluate_step, evaluate_rationale = self._step(
            "evaluate", "evaluate.quality", capabilities, params={}
        )
        if evaluate_step.capability_id is None:
            evaluate_step = PlanStep(
                id=new_id("step"),
                kind="evaluate",
                operation="evaluate.quality",
                capability_id=None,
                role=Role.EVALUATOR,
                params={"evaluator": "heuristic"},
            )
            evaluate_rationale = "built-in heuristic evaluator (no capability needed)"
        roles = [r for r in (execute_step.role, evaluate_step.role) if r]
        return ExecutionPlan(
            id=new_id("plan"),
            objective_id=objective.id,
            strategy=Strategy.REVIEW_THEN_REVISE,
            steps=[execute_step, evaluate_step],
            rationale=(
                "strategy=REVIEW_THEN_REVISE: execute, grade quality, revise "
                f"while justified (bounded). execute: {execute_rationale}. "
                f"evaluate: {evaluate_rationale}."
            ),
            roles=roles,
        )

    def _iterative_plan(
        self,
        objective: Objective,
        operation: str,
        capabilities: list[Capability],
        context: dict[str, Any],
    ) -> ExecutionPlan:
        iterations = max(1, min(int(context.get("max_iterations", 3)), 5))
        capability, cap_rationale = self._best(operation, capabilities)
        steps = [
            PlanStep(
                id=new_id("step"),
                kind="operation",
                operation=operation,
                capability_id=capability.id if capability else None,
                role=Role.BUILDER,
                params=dict(context.get("params", {}))
                if isinstance(context.get("params"), dict)
                else {},
            )
            for _ in range(iterations)
        ]
        return ExecutionPlan(
            id=new_id("plan"),
            objective_id=objective.id,
            strategy=Strategy.ITERATIVE,
            steps=steps,
            rationale=(
                f"strategy=ITERATIVE: up to {iterations} attempts, stopping at "
                f"first verification. {cap_rationale}."
            ),
            roles=[Role.BUILDER],
        )

    def _graph_plan(
        self, objective: Objective, operation: str, capabilities: list[Capability]
    ) -> ExecutionPlan:
        from agentos.adapters.langgraph import LangGraphAdapter

        graph_caps = [c for c in capabilities if c.supports("graph.run")]
        usable = [
            c
            for c in graph_caps
            if c.is_usable() and LangGraphAdapter.installed()
        ]
        if usable:
            capability = sorted(usable, key=lambda c: c.id)[0]
            step = PlanStep(
                id=new_id("step"),
                kind="delegate",
                operation="graph.run",
                capability_id=capability.id,
                role=Role.ARCHITECT,
                params=dict(objective.context.get("graph", {}))
                if isinstance(objective.context.get("graph"), dict)
                else {},
            )
            return ExecutionPlan(
                id=new_id("plan"),
                objective_id=objective.id,
                strategy=Strategy.GRAPH,
                steps=[step],
                rationale=(
                    f"strategy=GRAPH: graph-capable {capability.id} available; "
                    "delegating stateful orchestration."
                ),
                roles=[Role.ARCHITECT],
            )
        step, rationale = self._step(
            "execute", operation, capabilities,
            params=dict(objective.context.get("params", {}))
            if isinstance(objective.context.get("params"), dict)
            else {},
        )
        return ExecutionPlan(
            id=new_id("plan"),
            objective_id=objective.id,
            strategy=Strategy.SEQUENTIAL,
            steps=[step],
            rationale=(
                "strategy=SEQUENTIAL (GRAPH requested but no usable graph "
                f"capability; langgraph adapter reports unavailable). {rationale}."
            ),
            roles=[step.role] if step.role else [],
        )