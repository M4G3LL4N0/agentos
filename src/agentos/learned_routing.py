"""Learned Routing - executor metrics, historical signals, configurable weights."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentos.models import new_id
from agentos.store import Store


# Default routing weights - explicit and configurable
DEFAULT_ROUTING_WEIGHTS = {
    "capability_fit": 1.0,
    "quality": 1.0,
    "health": 1.0,
    "scarcity": 1.0,
    "cost": 1.0,
    "latency": 1.0,
    "historical_pass_rate": 1.0,
    "retry_rate": -1.0,  # Negative = penalty
    "verification_success": 1.0,
    "human_correction": -1.0,  # Negative = penalty
    "context_cost": -1.0,  # Negative = penalty
}


@dataclass
class ExecutorProfile:
    """Aggregated profile of an executor from historical metrics."""
    executor: str
    runtime: str
    model_provider: str
    task_class: str
    total_executions: int = 0
    success_count: int = 0
    verification_passes: int = 0
    total_latency_ms: int = 0
    total_retries: int = 0
    human_corrections: int = 0
    avg_context_size: int = 0
    last_seen: str = ""
    pass_rate: float = 0.0
    retry_rate: float = 0.0
    verification_pass_rate: float = 0.0
    human_correction_rate: float = 0.0

    def __post_init__(self):
        if self.total_executions > 0:
            if self.pass_rate == 0.0:
                self.pass_rate = self.success_count / self.total_executions
            if self.retry_rate == 0.0:
                self.retry_rate = self.total_retries / self.total_executions
            if self.verification_pass_rate == 0.0:
                self.verification_pass_rate = self.verification_passes / self.total_executions
            if self.human_correction_rate == 0.0:
                self.human_correction_rate = self.human_corrections / self.total_executions

    def to_dict(self) -> dict[str, Any]:
        return {
            "executor": self.executor,
            "runtime": self.runtime,
            "model_provider": self.model_provider,
            "task_class": self.task_class,
            "total_executions": self.total_executions,
            "success_count": self.success_count,
            "verification_passes": self.verification_passes,
            "total_latency_ms": self.total_latency_ms,
            "total_retries": self.total_retries,
            "human_corrections": self.human_corrections,
            "avg_context_size": self.avg_context_size,
            "last_seen": self.last_seen,
            "pass_rate": self.pass_rate,
            "retry_rate": self.retry_rate,
            "verification_pass_rate": self.verification_pass_rate,
            "human_correction_rate": self.human_correction_rate,
        }


def build_executor_profiles(store: Store,
                            task_class: str | None = None) -> list[ExecutorProfile]:
    """Build executor profiles from historical metrics."""
    metrics = store.get_executor_metrics(task_class=task_class, limit=1000)

    if not metrics:
        return []

    # Group by executor + task_class
    groups: dict[str, list[dict[str, Any]]] = {}
    for m in metrics:
        key = f"{m.get('executor', 'unknown')}|{m.get('task_class', 'unknown')}"
        if key not in groups:
            groups[key] = []
        groups[key].append(m)

    profiles = []
    for key, group in groups.items():
        executor, task_cls = key.split("|", 1)

        total = len(group)
        success = sum(1 for g in group if g.get("success", 0) == 1)
        ver_passes = sum(1 for g in group if g.get("verification_result") == "VERIFIED")
        total_latency = sum(g.get("latency_ms", 0) for g in group)
        total_retries = sum(g.get("retry_count", 0) for g in group)
        human_corrections = sum(g.get("human_correction", 0) for g in group)
        context_sizes = [g.get("context_size", 0) for g in group if g.get("context_size", 0) > 0]
        avg_context = sum(context_sizes) / len(context_sizes) if context_sizes else 0

        runtime = group[0].get("runtime", "")
        model_provider = group[0].get("model_provider", "")

        pass_rate = success / total if total > 0 else 0.0
        retry_rate = total_retries / total if total > 0 else 0.0
        ver_pass_rate = ver_passes / total if total > 0 else 0.0
        human_corr_rate = human_corrections / total if total > 0 else 0.0

        profiles.append(ExecutorProfile(
            executor=executor,
            runtime=runtime,
            model_provider=model_provider,
            task_class=task_cls,
            total_executions=total,
            success_count=success,
            verification_passes=ver_passes,
            total_latency_ms=total_latency,
            total_retries=total_retries,
            human_corrections=human_corrections,
            avg_context_size=int(avg_context),
            last_seen=group[-1].get("created_at", ""),
            pass_rate=pass_rate,
            retry_rate=retry_rate,
            verification_pass_rate=ver_pass_rate,
            human_correction_rate=human_corr_rate,
        ))

    return profiles


def score_executor(profile: ExecutorProfile,
                   weights: dict[str, float] | None = None) -> float:
    """Score an executor based on weights and profile."""
    w = weights or DEFAULT_ROUTING_WEIGHTS

    score = 0.0

    # Capability fit (proxy: success rate)
    score += w.get("capability_fit", 1.0) * profile.pass_rate

    # Quality (verification pass rate)
    score += w.get("quality", 1.0) * profile.verification_pass_rate

    # Health (inverse of retry rate)
    health = 1.0 - min(1.0, profile.retry_rate)
    score += w.get("health", 1.0) * health

    # Scarcity (inverse of executions - rare = more scarce)
    scarcity = 1.0 / (1.0 + profile.total_executions * 0.1)
    score += w.get("scarcity", 1.0) * scarcity

    # Cost (inverse of context size as proxy)
    cost_score = 1.0 / (1.0 + profile.avg_context_size / 1000)
    score += w.get("cost", 1.0) * cost_score

    # Latency (inverse)
    avg_latency = profile.total_latency_ms / max(profile.total_executions, 1)
    latency_score = 1.0 / (1.0 + avg_latency / 1000)
    score += w.get("latency", 1.0) * latency_score

    # Historical pass rate
    score += w.get("historical_pass_rate", 1.0) * profile.pass_rate

    # Retry rate penalty
    score += w.get("retry_rate", -1.0) * profile.retry_rate

    # Verification success
    score += w.get("verification_success", 1.0) * profile.verification_pass_rate

    # Human correction penalty
    score += w.get("human_correction", -1.0) * profile.human_correction_rate

    # Context cost penalty
    score += w.get("context_cost", -1.0) * (profile.avg_context_size / 10000)

    return score


def explain_routing(profile: ExecutorProfile,
                    weights: dict[str, float] | None = None) -> dict[str, Any]:
    """Generate explanation for routing decision."""
    w = weights or DEFAULT_ROUTING_WEIGHTS
    factors = {}

    factors["capability_fit"] = {
        "weight": w.get("capability_fit", 1.0),
        "value": profile.pass_rate,
        "contribution": w.get("capability_fit", 1.0) * profile.pass_rate,
    }
    factors["quality"] = {
        "weight": w.get("quality", 1.0),
        "value": profile.verification_pass_rate,
        "contribution": w.get("quality", 1.0) * profile.verification_pass_rate,
    }
    factors["health"] = {
        "weight": w.get("health", 1.0),
        "value": 1.0 - min(1.0, profile.retry_rate),
        "contribution": w.get("health", 1.0) * (1.0 - min(1.0, profile.retry_rate)),
    }
    factors["scarcity"] = {
        "weight": w.get("scarcity", 1.0),
        "value": 1.0 / (1.0 + profile.total_executions * 0.1),
        "contribution": w.get("scarcity", 1.0) / (1.0 + profile.total_executions * 0.1),
    }
    factors["cost"] = {
        "weight": w.get("cost", 1.0),
        "value": 1.0 / (1.0 + profile.avg_context_size / 1000),
        "contribution": w.get("cost", 1.0) / (1.0 + profile.avg_context_size / 1000),
    }
    factors["latency"] = {
        "weight": w.get("latency", 1.0),
        "value": 1.0 / (1.0 + (profile.total_latency_ms / max(profile.total_executions, 1)) / 1000),
        "contribution": w.get("latency", 1.0) / (1.0 + (profile.total_latency_ms / max(profile.total_executions, 1)) / 1000),
    }
    factors["historical_pass_rate"] = {
        "weight": w.get("historical_pass_rate", 1.0),
        "value": profile.pass_rate,
        "contribution": w.get("historical_pass_rate", 1.0) * profile.pass_rate,
    }
    factors["retry_rate_penalty"] = {
        "weight": w.get("retry_rate", -1.0),
        "value": profile.retry_rate,
        "contribution": w.get("retry_rate", -1.0) * profile.retry_rate,
    }
    factors["verification_success"] = {
        "weight": w.get("verification_success", 1.0),
        "value": profile.verification_pass_rate,
        "contribution": w.get("verification_success", 1.0) * profile.verification_pass_rate,
    }
    factors["human_correction_penalty"] = {
        "weight": w.get("human_correction", -1.0),
        "value": profile.human_correction_rate,
        "contribution": w.get("human_correction", -1.0) * profile.human_correction_rate,
    }
    factors["context_cost_penalty"] = {
        "weight": w.get("context_cost", -1.0),
        "value": profile.avg_context_size / 10000,
        "contribution": w.get("context_cost", -1.0) * (profile.avg_context_size / 10000),
    }

    total = sum(f["contribution"] for f in factors.values())

    return {
        "executor": profile.executor,
        "task_class": profile.task_class,
        "total_score": total,
        "factors": factors,
        "weights_used": w,
    }


class LearnedRouter:
    """Routes tasks using learned executor profiles."""

    def __init__(self, store: Store, weights: dict[str, float] | None = None):
        self.store = store
        self.weights = weights or DEFAULT_ROUTING_WEIGHTS

    def get_best_executor(self, task_class: str,
                          candidate_executors: list[str]) -> tuple[str | None, dict[str, Any]]:
        """Select best executor from candidates."""
        profiles = build_executor_profiles(self.store, task_class)

        # Filter to candidates
        candidates = [p for p in profiles if p.executor in candidate_executors]

        if not candidates:
            return None, {"reason": "no candidate profiles"}

        # Score all candidates
        scored = []
        for p in candidates:
            score = score_executor(p, self.weights)
            explanation = explain_routing(p, self.weights)
            scored.append((score, p.executor, explanation))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        best_score, best_executor, best_explanation = scored[0]

        return best_executor, {
            "selected": best_executor,
            "score": best_score,
            "explanation": best_explanation,
            "candidates_evaluated": len(scored),
            "all_scores": [
                {"executor": e, "score": s, "explanation": exp}
                for s, e, exp in scored
            ],
        }

    def record_routing_decision(self, job_id: str, task_class: str,
                                 chosen_executor: str, scores: dict[str, Any]) -> None:
        """Record routing decision for learning."""
        decision = dict(scores or {})
        decision["task_class"] = str(task_class)
        self.store.save_routing_outcome({
            "id": new_id("route"),
            "job_id": str(job_id),
            "decision": decision,
            "cost": {},
            "chosen": str(chosen_executor),
            "outcome": "PENDING",
        })

    def get_routing_explanation(self, job_id: str) -> dict[str, Any] | None:
        """Get explanation for a past routing decision."""
        return self.store.latest_routing_outcome(job_id)
