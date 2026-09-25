"""Real executor benchmarks (measured only, never invented).

Workload classes A-J per mission. Only workloads with a safe local
executor run here; everything else is recorded SKIPPED with an honest
reason (no model spend, no browser without Chrome, no paid anything).
Each record carries only measured fields: success/failure, latency,
retries, tool completion, quality signal, usage/cost if exposed, node
state, date and version. Reports aggregate measurements; they never
invent comparative rankings.
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Callable

WORKLOAD_CLASSES = {
    "A": "deterministic transformation",
    "B": "classification",
    "C": "repository inspection",
    "D": "code patch",
    "E": "research extraction",
    "F": "browser read",
    "G": "browser interaction",
    "H": "multi-step coding",
    "I": "synthesis/reasoning",
    "J": "persistent/offline-browser requirement",
}

#: Workloads runnable locally with zero cost/side effects against echo.
LOCAL_SAFE_WORKLOADS = ("A", "B", "C")


def workload_input(workload: str) -> dict[str, Any]:
    """Deterministic input per workload class (fixed, versioned)."""
    if workload == "A":
        return {"text": "agentos benchmark payload v1", "rounds": 1000}
    if workload == "B":
        return {"text": "urgent: fix the login bug", "labels": ["bug", "feature", "docs"]}
    if workload == "C":
        return {"path": ".", "pattern": "*.py"}
    return {}


def run_learning_benchmark(app: Any) -> dict[str, Any]:
    """Run the deterministic local learning-execution benchmark."""
    from agentos.models import (
        WorkflowDefinition,
        WorkflowMaturity,
        WorkflowStatus,
    )

    benchmark_started = time.monotonic()
    started_ids = {trace["taskId"] for trace in app.store.list_job_traces(limit=10000)}
    samples: list[dict[str, Any]] = []

    def record(name: str, report: Any) -> None:
        trace = app.store.get_job_trace(report.objective_id) or {}
        samples.append({
            "taskClass": name,
            "status": report.state.value,
            "cacheHit": (trace.get("cacheDecision") or {}).get("status") == "HIT",
            "workflowReuse": (trace.get("workflowDecision") or {}).get("status") == "REUSED",
            "workersInvoked": int(trace.get("workersInvoked") or 0),
            "teamSize": int((trace.get("teamPlan") or {}).get("size") or 0),
            "modelCalls": int(trace.get("modelCalls") or 0),
            "grokCalls": int(trace.get("grokCalls") or 0),
            "verificationCalls": 1 if trace.get("verification") else 0,
            "retries": len(trace.get("retries") or []),
            "humanEscalations": sum(
                1 for item in trace.get("escalations") or []
                if item.get("outcome") == "ESCALATE_HUMAN"
            ),
        })

    first = app.delegate("benchmark exact task", operation="echo", params={"message": "fixed"})
    record("repeated_exact_task", first)
    second = app.delegate("benchmark exact task", operation="echo", params={"message": "fixed"})
    record("repeated_exact_task", second)
    record("repo_state_lookup", app.delegate("benchmark repo state", operation="state.lookup"))
    record("deterministic_calculation", app.delegate("compute 6 * 7", operation="deterministic.calculate"))
    from agentos.models import new_id

    benchmark_workflow_id = f"benchmark_workflow_{new_id('run').split('_')[-1]}"
    app.store.save_workflow(WorkflowDefinition(
        id=benchmark_workflow_id,
        name="benchmark workflow",
        task_class=benchmark_workflow_id,
        steps=[{"kind": "operation", "operation": "echo", "capability_id": "echo", "params": {"message": "workflow"}}],
        status=WorkflowStatus.VERIFIED,
        maturity=WorkflowMaturity.REPEAT,
    ))
    objective = app.create_objective(
        "benchmark known workflow",
        operation="echo",
        params={"message": "workflow"},
        extra_context={"taskClass": benchmark_workflow_id},
    )
    record("known_workflow", app.run(objective.id))
    app.store.conn.execute("DELETE FROM workflows WHERE id=?", (benchmark_workflow_id,))
    app.store.conn.commit()
    record("mock_code_job", app.delegate("benchmark mock code", operation="echo", params={"message": "patch"}))
    research = app.create_objective(
        "benchmark research fixture",
        operation="echo",
        params={"message": "source"},
        extra_context={"taskType": "research"},
    )
    record("research_extraction_fixture", app.run(research.id))
    record("failed_executor", app.delegate("benchmark failed executor", operation="shell.run", params={"command": "false"}, max_attempts=1))
    record("provider_outage", app.delegate("benchmark provider outage", operation="shell.run", params={"command": "missing-benchmark-command"}, max_attempts=1))
    routine = app.routine_run("routine_usage_refresh", {})
    samples.append({"taskClass": "routine_unchanged", "status": routine.get("action"), "workersInvoked": 0})
    duplicate = app.delegate("benchmark exact task", operation="echo", params={"message": "fixed"})
    record("duplicate_request", duplicate)

    new_traces = [
        trace for trace in app.store.list_job_traces(limit=10000)
        if trace["taskId"] not in started_ids
    ]
    return {
        "samples": samples,
        "totals": {
            "tasks": len(samples),
            "cacheHits": sum(1 for sample in samples if sample.get("cacheHit")),
            "workflowReuse": sum(1 for sample in samples if sample.get("workflowReuse")),
            "workersInvoked": sum(int(sample.get("workersInvoked") or 0) for sample in samples),
            "teamSize": sum(int(sample.get("teamSize") or 0) for sample in samples),
            "modelCalls": sum(int(sample.get("modelCalls") or 0) for sample in samples),
            "grokCalls": sum(int(sample.get("grokCalls") or 0) for sample in samples),
            "verificationCalls": sum(int(sample.get("verificationCalls") or 0) for sample in samples),
            "retries": sum(int(sample.get("retries") or 0) for sample in samples),
            "humanEscalations": sum(int(sample.get("humanEscalations") or 0) for sample in samples),
            "duplicatePrevented": sum(
                1 for trace in new_traces
                if (trace.get("cacheDecision") or {}).get("status") == "HIT"
            ),
            "latencyMs": int((time.monotonic() - benchmark_started) * 1000),
        },
    }


def expected_output(workload: str, payload: dict[str, Any]) -> str:
    if workload == "A":
        digest = payload["text"].encode()
        for _ in range(payload["rounds"]):
            digest = hashlib.sha256(digest).digest()
        return digest.hex()[:16]
    if workload == "B":
        return "bug"
    if workload == "C":
        return "listing"
    return ""


def run_benchmark(
    executor_id: str,
    workload: str,
    execute: Callable[[str], str],
    node_state: str = "UNKNOWN",
    version: str = "1.0",
) -> dict[str, Any]:
    """Run one benchmark through ``execute`` (injected; real code only).

    ``execute`` receives the workload input text and returns output text.
    Retries are bounded (max 3 attempts); every attempt is timed.
    """
    from agentos.models import utc_now_iso

    workload = str(workload).upper()
    if workload not in WORKLOAD_CLASSES:
        raise ValueError(f"unknown workload class {workload!r}")
    payload = workload_input(workload)
    expected = expected_output(workload, payload)
    attempts = 0
    output = ""
    error: str | None = None
    started_all = time.monotonic()
    while attempts < 3:
        attempts += 1
        started = time.monotonic()
        try:
            output = execute(workload_input_text(workload, payload))
            error = None
            break
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    latency_ms = round((time.monotonic() - started_all) * 1000.0, 3)
    if error is not None:
        return {
            "executor": executor_id,
            "workload": workload,
            "workload_name": WORKLOAD_CLASSES[workload],
            "status": "FAILED",
            "success": False,
            "latency_ms": latency_ms,
            "retries": attempts - 1,
            "tool_completion": False,
            "quality_signal": f"error: {error}"[:200],
            "usage_cost": "UNKNOWN",
            "node_state": node_state,
            "date": utc_now_iso(),
            "version": version,
        }
    quality = "exact" if output.strip() == expected else "mismatch"
    return {
        "executor": executor_id,
        "workload": workload,
        "workload_name": WORKLOAD_CLASSES[workload],
        "status": "COMPLETED",
        "success": quality == "exact",
        "latency_ms": latency_ms,
        "retries": attempts - 1,
        "tool_completion": True,
        "quality_signal": quality,
        "usage_cost": "0",
        "node_state": node_state,
        "date": utc_now_iso(),
        "version": version,
    }


def workload_input_text(workload: str, payload: dict[str, Any]) -> str:
    if workload == "A":
        return f"transform:{payload['text']}:{payload['rounds']}"
    if workload == "B":
        return f"classify:{payload['text']}"
    if workload == "C":
        return f"list:{payload['path']}:{payload['pattern']}"
    return ""


def skip_record(
    executor_id: str, workload: str, reason: str, node_state: str = "UNKNOWN"
) -> dict[str, Any]:
    """Honest SKIPPED record for workloads with no safe local executor."""
    from agentos.models import utc_now_iso

    return {
        "executor": executor_id,
        "workload": str(workload).upper(),
        "workload_name": WORKLOAD_CLASSES.get(str(workload).upper(), "unknown"),
        "status": "SKIPPED",
        "success": None,
        "latency_ms": 0.0,
        "retries": 0,
        "tool_completion": False,
        "quality_signal": reason[:200],
        "usage_cost": "UNKNOWN",
        "node_state": node_state,
        "date": utc_now_iso(),
        "version": "1.0",
    }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate measured records per executor (no invented rankings)."""
    by_executor: dict[str, dict[str, Any]] = {}
    for record in records:
        entry = by_executor.setdefault(
            str(record.get("executor", "?")),
            {"runs": 0, "completed": 0, "successes": 0, "latencies": []},
        )
        entry["runs"] += 1
        if record.get("status") == "COMPLETED":
            entry["completed"] += 1
            if record.get("success"):
                entry["successes"] += 1
            entry["latencies"].append(float(record.get("latency_ms", 0.0) or 0.0))
    report: dict[str, Any] = {}
    for executor in sorted(by_executor):
        entry = by_executor[executor]
        latencies = entry.pop("latencies")
        report[executor] = {
            **entry,
            "success_rate": (
                round(entry["successes"] / entry["completed"], 3)
                if entry["completed"]
                else None
            ),
            "avg_latency_ms": (
                round(sum(latencies) / len(latencies), 3) if latencies else None
            ),
            "note": "measured only; no cross-executor ranking claimed",
        }
    return report
