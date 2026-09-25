"""Fleet Monitor - event-driven detection, incident clustering, retry suppression."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from agentos.models import Event, EventType, IncidentRecord, utc_now_iso, new_id
from agentos.store import Store


@dataclass
class FleetSignal:
    """A signal from the fleet."""
    signal_type: str
    job_id: str
    executor: str
    task_class: str
    timestamp: str
    details: dict[str, Any]


# Signal types
SIGNAL_TYPES = (
    "job_stalled",
    "job_duplicate",
    "job_orphaned",
    "retry_storm",
    "queue_congestion",
    "deadline_risk",
    "quota_exhaustion",
    "unexpected_spend",
    "provider_degradation",
    "unverified_completion",
    "common_blocker",
    "inefficient_fanout",
    "unused_persistent_agent",
    "repeat_human_correction",
)


def detect_signals(store: Store, time_window_minutes: int = 60) -> list[FleetSignal]:
    """Detect bounded failure and retry signals from measured metrics."""
    window = max(1, min(1440, int(time_window_minutes)))
    metrics = store.get_executor_metrics(limit=1000)
    signals: list[FleetSignal] = []
    for cluster in cluster_failures(metrics):
        if len(cluster) < 2:
            continue
        first = cluster[0]
        signals.append(FleetSignal(
            signal_type="common_blocker",
            job_id=str(first.get("id") or "unknown"),
            executor=str(first.get("executor") or "unknown"),
            task_class=str(first.get("task_class") or "unknown"),
            timestamp=str(first.get("created_at") or utc_now_iso()),
            details={
                "windowMinutes": window,
                "affectedJobs": [str(item.get("id")) for item in cluster],
                "error": str(first.get("error") or first.get("final_outcome") or "failure"),
            },
        ))
    for metric in metrics:
        if int(metric.get("retry_count") or 0) < 3:
            continue
        signals.append(FleetSignal(
            signal_type="retry_storm",
            job_id=str(metric.get("id") or "unknown"),
            executor=str(metric.get("executor") or "unknown"),
            task_class=str(metric.get("task_class") or "unknown"),
            timestamp=str(metric.get("created_at") or utc_now_iso()),
            details={"retryCount": int(metric.get("retry_count") or 0)},
        ))
    return signals[:1000]


def cluster_failures(executor_metrics: list[dict[str, Any]],
                     similarity_threshold: float = 0.7) -> list[list[dict[str, Any]]]:
    """Group similar failures into clusters."""
    if not executor_metrics:
        return []

    # Group by failure signature
    clusters = defaultdict(list)

    for metric in executor_metrics:
        if metric.get("success", 1) == 1:
            continue

        # Build failure signature
        sig_parts = [
            metric.get("executor", "unknown"),
            metric.get("task_class", "unknown"),
            metric.get("final_outcome", "failed"),
        ]
        # Add error pattern if available
        error = metric.get("error", "")
        if error:
            # Simplify error to pattern
            error_pattern = error[:50].lower()
            sig_parts.append(error_pattern)

        signature = "|".join(sig_parts)
        clusters[signature].append(metric)

    # Return clusters with size >= 2
    return [cluster for cluster in clusters.values() if len(cluster) >= 2]


def create_incident_from_cluster(cluster: list[dict[str, Any]]) -> IncidentRecord:
    """Create an IncidentRecord from a failure cluster."""
    if not cluster:
        return None

    first = cluster[0]
    last = cluster[-1]

    # Determine probable cause
    cause_patterns = {
        "auth": "Authentication failure",
        "timeout": "Timeout",
        "rate_limit": "Rate limiting",
        "permission": "Permission denied",
        "not_found": "Resource not found",
        "network": "Network connectivity",
        "quota": "Quota exceeded",
        "validation": "Validation error",
    }

    probable_cause = "Unknown"
    for pattern, cause in cause_patterns.items():
        if any(pattern in str(m.get("error", "")).lower() for m in cluster):
            probable_cause = cause
            break

    return IncidentRecord(
        id=new_id("inc"),
        signature="|".join([first.get("executor", ""), first.get("task_class", ""), probable_cause]),
        affected_jobs=[str(m.get("job_id") or m.get("id", "")) for m in cluster if m.get("job_id") or m.get("id")],
        affected_executors=sorted({m.get("executor", "") for m in cluster}),
        first_seen=first.get("created_at", utc_now_iso()),
        last_seen=last.get("created_at", utc_now_iso()),
        probable_cause=probable_cause,
        confidence=min(0.9, len(cluster) * 0.1),
        status="OPEN",
        mitigation="",
        resolution="",
    )


def suppress_redundant_retries(store: Store, incident: IncidentRecord) -> int:
    """Persist bounded retry suppression for jobs in an open incident."""
    if incident.status not in ("OPEN", "ACTIVE"):
        return 0
    return store.suppress_fleet_jobs(incident)


def route_around_incident(store: Store, incident: IncidentRecord) -> list[str]:
    """Find usable capabilities that share an operation with affected ones."""
    affected = set(incident.affected_executors)
    operations: set[str] = set()
    for capability_id in affected:
        capability = store.get_capability(capability_id)
        if capability is not None:
            operations.update(capability.operations or [])
    if not operations:
        return []
    alternatives = [
        capability.id
        for capability in store.list_capabilities()
        if capability.id not in affected
        and capability.is_usable()
        and any(operation in operations for operation in capability.operations or [])
    ]
    return sorted(set(alternatives))


def escalate_once(incident: IncidentRecord) -> dict[str, Any]:
    """Escalate incident once to human/supervisor."""
    return {
        "escalated": True,
        "incident_id": incident.id,
        "escalation_level": "supervisor",
        "reason": f"Clustered failure: {incident.probable_cause} affecting {len(incident.affected_jobs)} jobs",
        "affected_executors": incident.affected_executors,
    }


def resolve_incident(incident: IncidentRecord, resolution: str,
                     lesson_ref: str | None = None) -> IncidentRecord:
    """Mark incident as resolved."""
    incident.status = "RESOLVED"
    incident.resolution = resolution
    incident.lesson_ref = lesson_ref
    incident.last_seen = utc_now_iso()
    return incident


class FleetMonitor:
    """Event-driven fleet monitoring and incident management."""

    def __init__(self, store: Store):
        self.store = store
        self.active_incidents: dict[str, IncidentRecord] = {
            incident.id: incident
            for incident in store.list_incidents(limit=1000)
            if incident.status in ("OPEN", "ACTIVE")
        }
        self.last_actions: list[dict[str, Any]] = []

    def handle_event(self, event: Event) -> list[dict[str, Any]]:
        if event.event_type in (EventType.EXECUTION_FAILED, EventType.OBJECTIVE_FAILED):
            error = str((event.payload or {}).get("error") or "")
            signal = FleetSignal(
                signal_type="common_blocker",
                job_id=event.objective_id or event.execution_id or event.id,
                executor=str((event.payload or {}).get("capability_id") or "unknown"),
                task_class=str((event.payload or {}).get("operation") or "unknown"),
                timestamp=event.created_at,
                details={"error": error},
            )
            return self.process_signal(signal)
        if event.event_type == EventType.RECOVERY_STARTED:
            attempt = int((event.payload or {}).get("attempt") or 0)
            if attempt >= 3:
                signal = FleetSignal(
                    signal_type="retry_storm",
                    job_id=event.objective_id or event.id,
                    executor=str((event.payload or {}).get("capability_id") or "unknown"),
                    task_class=str((event.payload or {}).get("failure_type") or "unknown"),
                    timestamp=event.created_at,
                    details={"attempt": attempt},
                )
                return self.process_signal(signal)
        return []

    def process_signal(self, signal: FleetSignal) -> list[dict[str, Any]]:
        """Process a fleet signal and return actions taken."""
        actions = []

        metrics = self.store.get_executor_metrics(
            executor=signal.executor,
            task_class=signal.task_class,
            limit=50
        )
        clusters = cluster_failures(metrics)
        for cluster in clusters:
            new_incident = None
            if len(cluster) >= 3:  # Threshold for incident creation
                new_incident = create_incident_from_cluster(cluster)
                if new_incident:
                    existing_incident = self.store.find_incident_by_signature(
                        new_incident.signature
                    )
                    if existing_incident is not None:
                        existing_incident.affected_jobs = sorted(
                            set(existing_incident.affected_jobs)
                            | set(new_incident.affected_jobs)
                        )
                        existing_incident.last_seen = new_incident.last_seen
                        existing_incident.status = "ACTIVE"
                        self.store.save_incident(existing_incident)
                        self.active_incidents[existing_incident.id] = existing_incident
                        actions.append({
                            "action": "incident_updated",
                            "incident_id": existing_incident.id,
                            "affected_count": len(existing_incident.affected_jobs),
                        })
                        new_incident = None
            if new_incident is not None:
                self.store.save_incident(new_incident)
                self.active_incidents[new_incident.id] = new_incident
                actions.append({
                    "action": "incident_created",
                    "incident_id": new_incident.id,
                    "probable_cause": new_incident.probable_cause,
                    "affected_count": len(new_incident.affected_jobs),
                })

                suppressed = suppress_redundant_retries(self.store, new_incident)
                if suppressed:
                    actions.append({
                        "action": "retries_suppressed",
                        "count": suppressed,
                    })

                alternatives = route_around_incident(self.store, new_incident)
                if alternatives:
                    actions.append({
                        "action": "routed_around",
                        "alternatives": alternatives,
                    })

                escalation = escalate_once(new_incident)
                actions.append({
                    "action": "escalated",
                    "details": escalation,
                })

        self.last_actions = actions
        return actions

    def is_job_suppressed(self, job_id: str) -> bool:
        return self.store.is_fleet_job_suppressed(job_id)

    def get_active_incidents(self) -> list[IncidentRecord]:
        return list(self.active_incidents.values())

    def get_incident(self, incident_id: str) -> IncidentRecord | None:
        return self.store.get_incident(incident_id)

    def resolve_incident(self, incident_id: str, resolution: str,
                         lesson_ref: str | None = None) -> bool:
        incident = self.store.get_incident(incident_id)
        if not incident:
            return False

        incident = resolve_incident(incident, resolution, lesson_ref)
        self.store.save_incident(incident)
        self.store.clear_fleet_suppressions(incident_id)

        if incident_id in self.active_incidents:
            del self.active_incidents[incident_id]

        return True

    def get_fleet_health(self) -> dict[str, Any]:
        """Get overall fleet health summary."""
        incidents = self.store.list_incidents(limit=100)
        open_incidents = [i for i in incidents if i.status in ("OPEN", "ACTIVE")]

        return {
            "total_incidents": len(incidents),
            "open_incidents": len(open_incidents),
            "resolved_incidents": len([i for i in incidents if i.status == "RESOLVED"]),
            "affected_executors": list(set(
                e for i in open_incidents for e in i.affected_executors
            )),
            "top_causes": self._top_causes(incidents),
            "no_action": len(open_incidents) == 0,
        }

    def _top_causes(self, incidents: list[IncidentRecord]) -> list[dict[str, Any]]:
        cause_counts = defaultdict(int)
        for inc in incidents:
            cause_counts[inc.probable_cause] += 1
        return sorted(
            [{"cause": c, "count": n} for c, n in cause_counts.items()],
            key=lambda x: x["count"],
            reverse=True
        )[:5]