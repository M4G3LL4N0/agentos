"""Coach Layer - lesson generation from execution experience."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentos.models import (
    LessonCandidate,
    LessonStatus,
    VerificationOutcome,
    utc_now_iso,
    new_id,
)
from agentos.store import Store


COACH_TRIGGERS = (
    "retries_exceeded",
    "worker_failed",
    "routing_poor",
    "context_excessive",
    "cache_miss_avoidable",
    "human_correction",
    "task_recurring",
    "verification_expensive",
    "workflow_repeated",
    "provider_underperformed",
    "provider_overperformed",
)


@dataclass
class CoachTrigger:
    """A detected trigger for coach analysis."""
    trigger_type: str
    task_id: str
    task_class: str
    context: dict[str, Any]
    evidence_refs: list[str]


def detect_coach_triggers(task_id: str, task_class: str,
                          execution_history: list[dict[str, Any]],
                          store: Store) -> list[CoachTrigger]:
    """Detect coach triggers from execution history."""
    triggers = []

    if not execution_history:
        return triggers

    last = execution_history[-1]

    # Retries exceeded
    if last.get("attempt", 1) >= 3:
        triggers.append(CoachTrigger(
            trigger_type="retries_exceeded",
            task_id=task_id,
            task_class=task_class,
            context={"attempts": last.get("attempt", 1)},
            evidence_refs=last.get("evidence_refs", []),
        ))

    # Worker failed
    if last.get("status") == "FAILED":
        triggers.append(CoachTrigger(
            trigger_type="worker_failed",
            task_id=task_id,
            task_class=task_class,
            context={"failure_type": last.get("failure_type", "unknown")},
            evidence_refs=last.get("evidence_refs", []),
        ))

    # Routing was poor (multiple escalations)
    escalations = sum(1 for h in execution_history
                      if h.get("escalated", False))
    if escalations >= 2:
        triggers.append(CoachTrigger(
            trigger_type="routing_poor",
            task_id=task_id,
            task_class=task_class,
            context={"escalations": escalations},
            evidence_refs=last.get("evidence_refs", []),
        ))

    # Context was excessively large
    if last.get("context_size", 0) > 10000:
        triggers.append(CoachTrigger(
            trigger_type="context_excessive",
            task_id=task_id,
            task_class=task_class,
            context={"context_size": last.get("context_size", 0)},
            evidence_refs=last.get("evidence_refs", []),
        ))

    # Cache miss was avoidable
    if last.get("cache_miss_reason") and "no entry stored" in last.get("cache_miss_reason", ""):
        # Check if similar task succeeded recently
        similar = store.list_lessons(task_class=task_class, limit=10)
        if similar:
            triggers.append(CoachTrigger(
                trigger_type="cache_miss_avoidable",
                task_id=task_id,
                task_class=task_class,
                context={"miss_reason": last.get("cache_miss_reason")},
                evidence_refs=last.get("evidence_refs", []),
            ))

    # Human correction occurred
    if last.get("human_correction", False):
        triggers.append(CoachTrigger(
            trigger_type="human_correction",
            task_id=task_id,
            task_class=task_class,
            context={"correction": last.get("correction_detail", "")},
            evidence_refs=last.get("evidence_refs", []),
        ))

    # Task class is recurring (5+ executions)
    class_metrics = store.get_executor_metrics(task_class=task_class, limit=100)
    if len(class_metrics) >= 5:
        triggers.append(CoachTrigger(
            trigger_type="task_recurring",
            task_id=task_id,
            task_class=task_class,
            context={"execution_count": len(class_metrics)},
            evidence_refs=last.get("evidence_refs", []),
        ))

    # Verification was expensive (multiple rounds)
    verifications = last.get("verification_rounds", 0)
    if verifications >= 3:
        triggers.append(CoachTrigger(
            trigger_type="verification_expensive",
            task_id=task_id,
            task_class=task_class,
            context={"verification_rounds": verifications},
            evidence_refs=last.get("evidence_refs", []),
        ))

    # Workflow succeeded repeatedly
    if last.get("workflow_used") and last.get("workflow_success_count", 0) >= 3:
        triggers.append(CoachTrigger(
            trigger_type="workflow_repeated",
            task_id=task_id,
            task_class=task_class,
            context={"workflow_id": last.get("workflow_id")},
            evidence_refs=last.get("evidence_refs", []),
        ))

    # Provider under/over performed
    provider_metrics = last.get("provider_metrics", {})
    if provider_metrics.get("success_rate", 1.0) < 0.5:
        triggers.append(CoachTrigger(
            trigger_type="provider_underperformed",
            task_id=task_id,
            task_class=task_class,
            context={"provider": last.get("provider"), "success_rate": provider_metrics.get("success_rate")},
            evidence_refs=last.get("evidence_refs", []),
        ))
    elif provider_metrics.get("success_rate", 0.0) > 0.9 and provider_metrics.get("cost", 0) == 0:
        triggers.append(CoachTrigger(
            trigger_type="provider_overperformed",
            task_id=task_id,
            task_class=task_class,
            context={"provider": last.get("provider")},
            evidence_refs=last.get("evidence_refs", []),
        ))

    return triggers


def generate_lesson(trigger: CoachTrigger,
                    execution_history: list[dict[str, Any]],
                    store: Store) -> LessonCandidate:
    """Generate a LessonCandidate from a coach trigger."""
    last = execution_history[-1] if execution_history else {}

    # Determine root cause and lesson based on trigger type
    root_cause_map = {
        "retries_exceeded": ("Retry logic too aggressive or task fundamentally misrouted",
                            "Add pre-check or adjust routing policy"),
        "worker_failed": ("Worker capability mismatch",
                         "Update capability readiness or select different executor"),
        "routing_poor": ("Routing weights don't reflect actual performance",
                        "Adjust routing weights based on historical success rates"),
        "context_excessive": ("Context includes unnecessary data",
                             "Implement context minimization before execution"),
        "cache_miss_avoidable": ("Cache key generation doesn't capture equivalence",
                                "Review semantic cache key generation"),
        "human_correction": ("Automation missed a decision point",
                            "Add verification step or decision rule"),
        "task_recurring": ("Repeated execution without workflow compilation",
                          "Compile recurring task class into workflow"),
        "verification_expensive": ("Verification chain too heavy for task class",
                                  "Select cheaper sufficient verification"),
        "workflow_repeated": ("Workflow is stable and repeatable",
                             "Promote workflow to CERTIFIED maturity"),
        "provider_underperformed": ("Provider reliability below threshold",
                                   "Demote provider in routing or add fallback"),
        "provider_overperformed": ("Provider exceeds expectations at low cost",
                                  "Promote provider in routing for similar tasks"),
    }

    root_cause, lesson = root_cause_map.get(
        trigger.trigger_type,
        ("Unknown trigger", "Review and adjust process")
    )

    evidence_refs = trigger.evidence_refs + [
        h.get("id") for h in execution_history[-3:] if h.get("id")
    ]

    confidence = 0.3  # Low initial confidence for single observation
    if trigger.trigger_type in ("task_recurring", "workflow_repeated"):
        confidence = 0.6  # Higher for patterns
    elif trigger.trigger_type == "human_correction":
        confidence = 0.7  # Human correction is strong signal

    recommended_change = _recommend_change(trigger.trigger_type, execution_history)

    return LessonCandidate(
        id=new_id("lesson"),
        task_class=trigger.task_class,
        scope=trigger.trigger_type,
        symptom=trigger.trigger_type,
        root_cause=root_cause,
        lesson=lesson,
        evidence_refs=evidence_refs,
        confidence=confidence,
        recommended_change=recommended_change,
        status=LessonStatus.CANDIDATE,
        created_at=utc_now_iso(),
    )


def _recommend_change(trigger_type: str, history: list[dict[str, Any]]) -> str:
    """Recommend concrete change based on trigger."""
    recommendations = {
        "retries_exceeded": "Add pre-execution validation; adjust max_retries in verification policy",
        "worker_failed": "Update capability readiness; add capability to routing fallback",
        "routing_poor": "Retrain routing weights with recent executor_metrics; add provider health check",
        "context_excessive": "Implement delta context; use compute_delta before execution",
        "cache_miss_avoidable": "Review intcache_key normalization; add semantic aliases",
        "human_correction": "Add verification step for this decision point; create skill if recurring",
        "task_recurring": "Compile workflow from successful executions; set workflow status to VERIFIED",
        "verification_expensive": "Select cheaper verification type; use deterministic checks first",
        "workflow_repeated": "Promote workflow maturity; update cache_policy to aggressive",
        "provider_underperformed": "Add provider to fallback_executors; increase health check frequency",
        "provider_overperformed": "Promote provider in preferred_executors; reduce verification requirements",
    }
    return recommendations.get(trigger_type, "Review process and update policy")


class Coach:
    """Main coach orchestrator."""

    def __init__(self, store: Store, sampling_rate: float = 0.1):
        self.store = store
        self.sampling_rate = sampling_rate  # Run on routine success with this probability

    def analyze_execution(self, task_id: str, task_class: str,
                          execution_history: list[dict[str, Any]]) -> list[LessonCandidate]:
        """Analyze execution and generate lessons."""
        triggers = detect_coach_triggers(task_id, task_class, execution_history, self.store)
        lessons = []

        for trigger in triggers:
            lesson = generate_lesson(trigger, execution_history, self.store)
            self.store.save_lesson(lesson)
            lessons.append(lesson)

        return lessons

    def maybe_coach_routine_success(self, task_id: str, task_class: str,
                                     execution_history: list[dict[str, Any]]) -> list[LessonCandidate]:
        """Optionally coach on routine success (sampling)."""
        import random
        if random.random() < self.sampling_rate:
            # Only generate lessons for workflow compilation on routine success
            if execution_history and execution_history[-1].get("workflow_used"):
                trigger = CoachTrigger(
                    trigger_type="workflow_repeated",
                    task_id=task_id,
                    task_class=task_class,
                    context={"workflow_id": execution_history[-1].get("workflow_id")},
                    evidence_refs=execution_history[-1].get("evidence_refs", []),
                )
                lesson = generate_lesson(trigger, execution_history, self.store)
                self.store.save_lesson(lesson)
                return [lesson]
        return []

    def promote_lesson(self, lesson_id: str, new_status: LessonStatus) -> bool:
        """Promote lesson status based on evidence."""
        return self.store.promote_lesson(lesson_id, new_status)

    def get_lessons_for_task_class(self, task_class: str) -> list[LessonCandidate]:
        return self.store.list_lessons(task_class=task_class)

    def get_adopted_lessons(self) -> list[LessonCandidate]:
        return self.store.list_lessons(status=LessonStatus.ADOPTED)