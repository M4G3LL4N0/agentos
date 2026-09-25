"""Durable execution abstraction (provider-neutral).

Default backend: AgentOS native persistence (federation jobs table —
already restart-durable). Optional backend: Temporal, evaluated as
DEFERRED (see :func:`temporal_status` for the exact reason).

Durability is used only when a job needs multi-hour/day execution,
external event waiting, retries, resumability, crash recovery,
conditional transitions or scheduled continuation. Simple commands stay
native and non-durable.
"""

from __future__ import annotations

from typing import Any

TEMPORAL_STATUS = "DEFERRED"

TEMPORAL_REASON = (
    "stdlib-only build: no Temporal SDK dependency, no Temporal service, "
    "and no explicit need for one. Temporal's agent integrations can run "
    "model calls as Activities (so workflow replay does not rerun completed "
    "model calls), but pulling in experimental integration packages for "
    "novelty would violate provider-neutrality and the no-unmeasured-claims "
    "rule. The DurableExecutionBackend interface below is the seam an "
    "OPTIONAL_BACKEND adapter would implement; until then native "
    "persistence is the backend."
)


class DurableExecutionBackend:
    """Native durable backend over the federation jobs table.

    ``start`` persists a DURABLE job record; ``checkpoint`` records
    resumable state; ``resume`` re-reads it (crash recovery = reopen the
    DB and resume). All bounded, all local, zero cost.
    """

    def __init__(self, store: Any) -> None:
        self.store = store

    def backend_name(self) -> str:
        return "native"

    def start(self, job: dict[str, Any]) -> dict[str, Any]:
        from agentos.federation import JobEnvelope, JobStatus

        envelope = JobEnvelope.from_dict(dict(job))
        record = {
            "id": envelope.id,
            "status": JobStatus.QUEUED.value,
            "envelope": envelope.to_dict(),
            "durable": True,
            "checkpoints": [],
        }
        self.store.save_federation_job(
            envelope, JobStatus.QUEUED.value, decision={"durable": True}
        )
        return record

    def checkpoint(self, job_id: str, state: dict[str, Any]) -> dict[str, Any]:
        import json

        job = self.store.get_federation_job(job_id)
        if job is None:
            raise ValueError(f"unknown durable job {job_id!r}")
        envelope = dict(job.get("envelope") or {})
        checkpoints = list(envelope.get("checkpoints") or [])
        checkpoints.append(dict(state))
        envelope["checkpoints"] = checkpoints
        self.store.conn.execute(
            "UPDATE federation_jobs SET envelope_json=? WHERE id=?",
            (json.dumps(envelope, default=str), job_id),
        )
        self.store.conn.commit()
        return {"job_id": job_id, "checkpoints": len(checkpoints)}

    def resume(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_federation_job(job_id)
        if job is None:
            raise ValueError(f"unknown durable job {job_id!r}")
        envelope = dict(job.get("envelope") or {})
        return {
            "job_id": job_id,
            "status": job.get("status"),
            "checkpoints": list(envelope.get("checkpoints") or []),
            "envelope": envelope,
        }


def temporal_status() -> dict[str, Any]:
    """Temporal evaluation: ADAPTER_ONLY / DEFERRED with the exact reason."""
    return {
        "status": TEMPORAL_STATUS,
        "detail": TEMPORAL_REASON,
        "service_running": False,
        "dependency_present": False,
    }


def durable_smoke(store: Any) -> dict[str, Any]:
    """Deterministic durable-workflow smoke over native persistence.

    Starts a durable job, checkpoints twice, closes + reopens nothing
    (same store), resumes and verifies both checkpoints survived. No
    Temporal service involved.
    """
    backend = DurableExecutionBackend(store)
    started = backend.start(
        {"requester": "smoke", "targetCapability": "echo", "deliverable": "durable"}
    )
    backend.checkpoint(started["id"], {"stage": 1, "note": "first"})
    backend.checkpoint(started["id"], {"stage": 2, "note": "second"})
    resumed = backend.resume(started["id"])
    ok = (
        len(resumed["checkpoints"]) == 2
        and resumed["checkpoints"][1].get("stage") == 2
    )
    return {"ok": ok, "job_id": started["id"], "checkpoints": resumed["checkpoints"]}
