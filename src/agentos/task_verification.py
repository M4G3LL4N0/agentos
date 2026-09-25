"""Verification-first task model (provider-neutral, additive).

Task completion = WORK + EVIDENCE + VERIFICATION. This module describes and
drives honest verification state over jobs:

* ``verificationRequired`` / ``verificationType`` / ``verifierCapability`` /
  ``verificationEvidence`` / ``verificationStatus`` on the job.
* A small state machine (UNVERIFIED -> REQUIRED -> PENDING -> VERIFIED/FAILED,
  with NOT_REQUIRED/SKIP paths).
* Verifier/worker separation that prefers a different executor when
  meaningful and deliberately does NOT waste a second model call on
  trivially deterministic evidence.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from agentos.models import new_id, utc_now_iso

#: Canonical verification types a job may require.
VERIFICATION_TYPES = (
    "tests",
    "build",
    "runtime",
    "screenshots",
    "api_response",
    "source_verification",
    "independent_model_review",
    "file_diff",
    "deterministic",
)

#: Types whose evidence is trivially deterministic: no extra model/verifier
#: call is warranted — verification is a cheap local check on outputs.
DETERMINISTIC_VERIFICATION_TYPES = frozenset(
    {"deterministic", "file_diff", "source_verification"}
)

#: Verification types that meaningfully benefit from a separate verifier.
SEPARATION_VERIFICATION_TYPES = frozenset(
    {"tests", "build", "runtime", "screenshots", "api_response"}
)


def normalize_verification_type(value: Any) -> str:
    name = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return name if name in VERIFICATION_TYPES else ""


class TaskVerificationStatus(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    REQUIRED = "REQUIRED"
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    NOT_REQUIRED = "NOT_REQUIRED"


TRANSITIONS: dict[tuple[TaskVerificationStatus, str], TaskVerificationStatus] = {
    (TaskVerificationStatus.UNVERIFIED, "REQUIRE"): TaskVerificationStatus.REQUIRED,
    (TaskVerificationStatus.UNVERIFIED, "SKIP"): TaskVerificationStatus.NOT_REQUIRED,
    (TaskVerificationStatus.REQUIRED, "START"): TaskVerificationStatus.PENDING,
    (TaskVerificationStatus.REQUIRED, "SKIP"): TaskVerificationStatus.NOT_REQUIRED,
    (TaskVerificationStatus.PENDING, "PASS"): TaskVerificationStatus.VERIFIED,
    (TaskVerificationStatus.PENDING, "FAIL"): TaskVerificationStatus.FAILED,
}


def transition_verification(
    current: Any,
    event: str,
    *,
    verification_required: bool | None = None,
) -> tuple[TaskVerificationStatus, bool, str]:
    """Advance (or refuse) a verification-status transition.

    Returns ``(new_status, allowed, reason)``. Illegal transitions are
    refused, never silently coerced.
    """
    try:
        state = TaskVerificationStatus(str(current or "UNVERIFIED").upper())
    except ValueError:
        return TaskVerificationStatus.UNVERIFIED, False, f"unknown state {current!r}"
    event = str(event or "").upper()
    if event == "REQUIRE" and verification_required is False:
        return TaskVerificationStatus.NOT_REQUIRED, True, "verification explicitly not required"
    if event == "REQUIRE" and verification_required is None:
        # Auto-require is only legal from the initial state.
        pass
    outcome = TRANSITIONS.get((state, event))
    if outcome is None:
        return state, False, f"illegal transition {state.value} --{event}--> ?"
    return outcome, True, f"{state.value} --{event}--> {outcome.value}"


def verifier_separation(worker: str, verifier: str | None) -> tuple[bool, str]:
    """True when the verifier is a different executor than the worker.

    A missing verifier is not "separation"; self-verification is honest but
    it is NOT separation.
    """
    worker = str(worker or "").strip()
    verifier = str(verifier or "").strip()
    if not verifier:
        return False, "no verifier configured; verification falls back to the worker"
    if worker and verifier == worker:
        return False, "verifier equals worker (no separation)"
    return True, f"verifier {verifier} is separate from worker {worker}"


def _job_requires_verification(job: Any) -> bool:
    explicit = getattr(job, "verificationRequired", None)
    if explicit is not None:
        return bool(explicit)
    return bool(getattr(job, "verificationType", None))


def choose_verifier(
    job: Any,
    worker_cell: Any,
    cells: list[Any],
) -> tuple[str | None, bool, str]:
    """Pick a verifier executor, honoring separation-without-waste.

    Deterministic evidence never triggers a second model call: the worker's
    own cheap check is the verification. Meaningful types prefer a different
    executor that declares a verification policy or a VERIFIED/REVIEWED
    quality ladder; otherwise NONE with an honest reason.
    """
    vtype = normalize_verification_type(getattr(job, "verificationType", ""))
    worker_id = str(getattr(worker_cell, "id", "") or "")
    if vtype in DETERMINISTIC_VERIFICATION_TYPES:
        return worker_id, False, (
            f"deterministic evidence ({vtype}): worker self-verification "
            "suffices; no second model/verifier call is wasted"
        )
    if vtype not in SEPARATION_VERIFICATION_TYPES:
        return None, False, (
            f"verification type {vtype or '<none>'} does not warrant a separate verifier"
        )
    from agentos.federation import QualityFloor, quality_rank

    for cell in cells:
        if cell.id == worker_id:
            continue
        if not cell.usable():
            continue
        policy = getattr(cell, "verificationPolicy", None) or (cell.config or {}).get("verificationPolicy")
        if policy or quality_rank(getattr(cell, "quality_ladder", "")) >= quality_rank(
            QualityFloor.VERIFIED.value
        ):
            return cell.id, True, (
                f"separated verifier {cell.id} (verificationPolicy={'yes' if policy else 'quality=VERIFIED+'})"
            )
    return None, False, (
        "no separable verifier executor exists; verification falls back to the worker (honest)"
    )


def verification_plan(
    job: Any,
    worker_cell: Any | None = None,
    cells: list[Any] | None = None,
) -> dict[str, Any]:
    """Produce the verification-first plan for a job envelope."""
    cells = list(cells or [])
    job_id = getattr(job, "id", "") or ""
    required = _job_requires_verification(job)
    vtype = normalize_verification_type(getattr(job, "verificationType", ""))
    if not required and not vtype:
        return {
            "jobId": job_id,
            "verificationRequired": False,
            "verificationType": "",
            "status": TaskVerificationStatus.NOT_REQUIRED.value,
            "events": [("UNVERIFIED", "SKIP", "verification not required")],
            "verifier": None,
            "verifier_separated": False,
            "verifier_reason": "verification not required for this job",
            "evidence": [],
        }
    worker_id = getattr(worker_cell, "id", "") if worker_cell else None
    verifier, separated, reason = choose_verifier(job, worker_cell, cells)
    status, _allowed, _why = transition_verification(
        TaskVerificationStatus.UNVERIFIED.value,
        "REQUIRE",
        verification_required=required,
    )
    return {
        "jobId": job_id,
        "verificationRequired": True,
        "verificationType": vtype or "source_verification",
        "status": status.value,
        "events": [("UNVERIFIED", "REQUIRE", f"requires {vtype or 'default'} verification")],
        "worker": worker_id,
        "verifier": verifier,
        "verifier_separated": separated,
        "verifier_reason": reason,
        "evidence": list(getattr(job, "verificationEvidence", None) or []),
        "deterministic": vtype in DETERMINISTIC_VERIFICATION_TYPES,
    }


def evidence_for_verification(
    job_id: str,
    status: str,
    verifier: str | None,
    vtype: str,
    evidence: list[str],
) -> dict[str, Any]:
    """Record honest verification evidence (bounded detail, no fabrication)."""
    evidence = [str(e)[:2000] for e in (evidence or [])[:50]]
    return {
        "id": new_id("vjob"),
        "job_id": job_id,
        "verifier": verifier,
        "verification_type": normalize_verification_type(vtype),
        "status": str(status or TaskVerificationStatus.PENDING.value),
        "evidence": evidence,
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
    }