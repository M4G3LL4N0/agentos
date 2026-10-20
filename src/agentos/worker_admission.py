"""ServerOS worker admission + JanitorOS scratch leases (Phase 4B).

`admit_worker` answers exactly four questions for a prospective worker —
using ONLY a published resource snapshot (read-only, no telemetry of its
own) and explicit inputs:

  1. Can a new worker safely start?          -> can_start
  2. What resource budget may it receive?    -> budget
  3. Which resource constrains admission?    -> constraining_resource
  4. When should admission be retried?       -> retry_after_s

Design rules (Phase 4B mission):
- NO scheduler here: the authoritative schedulers are AutonomousOS
  (demands/allocations) and REALM Studio (task execution). This module
  only advises/refuses; it never starts, stops or kills a process.
- Provider rate limits are evaluated INDEPENDENTLY of CPU/band state —
  a cooling provider refuses even on a HEALTHY host and vice versa.
- Hysteresis comes from the host governor band (never re-decided here),
  so a single bad sample cannot oscillate admission.
- Fail closed: missing/stale/UNKNOWN sources refuse with an honest
  reason and a retry hint; consumers that cannot read the snapshot at
  all fall back to the pre-existing gates (documented in the contract).
- Initial concurrency: at most MAX_ADDITIONAL_HEAVY_CODING (2)
  additional heavyweight coding workers, only under measured headroom.

Scratch leases wrap the EXISTING JanitorOS mechanism
(`janitor space scratch register/complete/reap`) and keep the full
ServerOS lease record (task_id, venture_id, workspace, scratch_root,
storage_budget, creation_time, owner, lease_state, retention_policy).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from agentos.models import utc_now_iso
from agentos.resource_contract import (
    MAX_ADDITIONAL_HEAVY_CODING,
    SNAPSHOT_MAX_AGE_S,
    UNKNOWN,
    ResourcePaths,
    default_runner,
)

Runner = Callable[[list[str], float], tuple[int, str]]

PROFILES = {
    "heavyweight_coding": {"heavy": True, "priority_class": "high"},
    "light": {"heavy": False, "priority_class": "normal"},
    "verification": {"heavy": False, "priority_class": "normal"},
}

# retry hints (seconds) — bounded, deterministic
_RETRY = {
    "stale_snapshot": 15,
    "unknown_source": 15,
    "provider_cooldown": None,  # computed from remaining cooldown
    "band_critical": 300,
    "band_pressed": 120,
    "band_recovering": 120,
    "heavy_under_pressure": 120,
    "low_priority_deferred": 60,
    "storage_denied": 60,
    "storage_unknown": 60,
    "process_stacking": 120,
    "stuck_filesystem_scan": 120,
    "verification_overload": 120,
    "autonomousos_hold": 60,
    "worker_capacity_exhausted": 60,
}

PRESSED_MAX_BYTES = 100 * 1024 * 1024  # matches host governor pressed cap


def admit_worker(
    snapshot: dict[str, Any] | None,
    *,
    profile: str = "heavyweight_coding",
    bytes_needed: int = 0,
    level: str = "MEDIUM",
    provider_cooldown_until: float | None = None,
    now: float | None = None,
    max_age_s: float = SNAPSHOT_MAX_AGE_S,
) -> dict[str, Any]:
    """Decide admission for one prospective worker. Pure; no side effects."""
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}")
    now = time.time() if now is None else now
    decision: dict[str, Any] = {
        "profile": profile,
        "level": level,
        "bytes_needed": bytes_needed,
        "can_start": False,
        "budget": {},
        "constraining_resource": UNKNOWN,
        "retry_after_s": _RETRY["unknown_source"],
        "reason": "",
        "defer_low_priority": False,
        "decided_utc": utc_now_iso(),
    }

    def refuse(constraint: str, retry: int | None, reason: str, **extra: Any) -> dict[str, Any]:
        decision["can_start"] = False
        decision["constraining_resource"] = constraint
        decision["retry_after_s"] = _RETRY.get(constraint) if retry is None else retry
        decision["reason"] = reason
        decision.update(extra)
        return decision

    if not snapshot:
        return refuse("unknown_source", _RETRY["unknown_source"],
                      "resource snapshot unavailable (fail closed)")

    meta = snapshot.get("_meta") or {}
    generated = float(meta.get("generated_epoch") or 0)
    if generated and now - generated > max_age_s:
        return refuse("stale_snapshot", _RETRY["stale_snapshot"],
                      f"resource snapshot stale ({int(now - generated)}s > {max_age_s}s)",
                      state_age_s=round(now - generated, 1))
    if not generated:
        return refuse("stale_snapshot", _RETRY["stale_snapshot"],
                      "resource snapshot has no generation timestamp")

    # 1) Provider rate limit — independent dimension, evaluated first.
    if provider_cooldown_until is not None and provider_cooldown_until > now:
        remaining = int(provider_cooldown_until - now) + 1
        return refuse("provider_cooldown", remaining,
                      "provider cooling down (independent of host resources)",
                      provider_available_utc=None)

    # 2) Host band (hysteresis already applied by the governor).
    band = snapshot.get("resource_band", UNKNOWN)
    capacity = snapshot.get("recommended_worker_capacity") or {}
    constraints = list(capacity.get("constraints") or [])

    if band == UNKNOWN:
        return refuse("unknown_source", _RETRY["unknown_source"],
                      "resource_band UNKNOWN (fail closed)")
    if band == "CRITICAL":
        return refuse("band_critical", _RETRY["band_critical"],
                      "host band CRITICAL: unsafe to start new workers")

    # 3) AutonomousOS primary brake (never override an explicit hold).
    aos = meta.get("autonomousos") or {}
    if aos.get("allow_new_work") is False:
        return refuse("autonomousos_hold", _RETRY["autonomousos_hold"],
                      "AutonomousOS governor holds allow_new_work=false")

    # 4) Storage admission (JanitorOS).
    storage = snapshot.get("storage_admission", UNKNOWN)
    if storage == "DENY":
        return refuse("storage_denied", _RETRY["storage_denied"],
                      "JanitorOS storage admission DENY")
    if storage == UNKNOWN:
        return refuse("storage_unknown", _RETRY["storage_unknown"],
                      "storage admission UNKNOWN (fail closed)")

    # 5) Infrastructure blockers (stacking / stuck scans) — no new workers.
    infra = meta.get("infrastructure") or {}
    if infra.get("stacking_detected") is True:
        return refuse("process_stacking", _RETRY["process_stacking"],
                      "worker/janitor process stacking detected")
    if isinstance(infra.get("stuck_finds"), int) and infra["stuck_finds"] > 0:
        return refuse("stuck_filesystem_scan", _RETRY["stuck_filesystem_scan"],
                      f"{infra['stuck_finds']} stuck filesystem scan(s) running")

    # 6) Verification overload — only blocks verification-class work.
    verification = meta.get("verification") or {}
    if profile == "verification" and verification.get("overloaded") is True:
        return refuse("verification_overload", _RETRY["verification_overload"],
                      f"verification capacity saturated (test leases >= {verification.get('test_cap')})")

    # 7) PRESSED / RECOVERING: slow admissions, defer low-priority work.
    if band in ("PRESSED", "RECOVERING"):
        if str(level).upper() == "LOW":
            return refuse("low_priority_deferred", _RETRY["low_priority_deferred"],
                          f"host {band}: deferring low-priority work",
                          defer_low_priority=True)
        if PROFILES[profile]["heavy"]:
            return refuse("heavy_under_pressure", _RETRY["heavy_under_pressure"],
                          f"host {band}: heavyweight coding deferred (hysteresis-held band)")

    # 8) Capacity from the contract (never above the 2-worker ceiling).
    additional = int(capacity.get("additional_heavy_coding", 0) or 0)
    additional = max(0, min(additional, MAX_ADDITIONAL_HEAVY_CODING))
    if profile == "heavyweight_coding" and additional < 1:
        return refuse("worker_capacity_exhausted", _RETRY["worker_capacity_exhausted"],
                      "no additional heavyweight coding capacity "
                      f"(constraints: {', '.join(constraints) or 'none'})")

    # admitted ------------------------------------------------------------
    byte_cap = bytes_needed
    if band in ("PRESSED", "RECOVERING") and bytes_needed > PRESSED_MAX_BYTES:
        byte_cap = PRESSED_MAX_BYTES
    decision.update({
        "can_start": True,
        "constraining_resource": "none",
        "retry_after_s": 0,
        "reason": (
            f"admitted: band={band} storage=ALLOW "
            f"additional_heavy_coding={additional if profile == 'heavyweight_coding' else 'n/a'}"
        ),
        "defer_low_priority": band in ("PRESSED", "RECOVERING"),
        "budget": {
            "additional_heavy_coding": additional
            if profile == "heavyweight_coding"
            else None,
            "max_absolute_heavy_coding": MAX_ADDITIONAL_HEAVY_CODING,
            "bytes_allowed": bytes_needed,
            "bytes_cap": byte_cap,
            "band": band,
            "cpu_heavy_cap": (aos.get("cpu_heavy_cap")),
            "constraints": constraints,
        },
    })
    return decision


# ---------------------------------------------------------------------------
# scratch leases (wraps the existing JanitorOS scratch registry)
# ---------------------------------------------------------------------------

LEASE_STATES = ("ACTIVE", "COMPLETED_PENDING_REAP", "RETIRED", "REFUSED")
RETENTION_POLICIES = ("rollback_critical", "evidence", "disposable")

LEASE_RECORD_CAP = 500


def _registry_path() -> Path:
    paths = ResourcePaths.from_env()
    return Path(paths.snapshot_out).parent / "scratch-leases.json"


def _load_registry(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or _registry_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def _save_registry(records: list[dict[str, Any]], path: Path | None = None) -> None:
    target = path or _registry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(records[-LEASE_RECORD_CAP:], indent=2) + "\n"
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, target)


def register_scratch(
    *,
    task_id: str,
    venture_id: str,
    workspace: str,
    scratch_root: str,
    storage_budget: int,
    owner: str,
    retention_policy: str = "disposable",
    runner: Runner = default_runner,
    paths: ResourcePaths | None = None,
    registry: Path | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """Declare task scratch with JanitorOS at task start (existing policy)."""
    if retention_policy not in RETENTION_POLICIES:
        raise ValueError(f"retention_policy must be one of {RETENTION_POLICIES}")
    paths = paths or ResourcePaths.from_env()
    rc, out = runner(
        [paths.janitor, "space", "scratch", "register",
         "--task", task_id, "--path", scratch_root],
        20.0,
    )
    janitor_ok = rc == 0
    record = {
        "task_id": task_id,
        "venture_id": venture_id,
        "workspace": workspace,
        "scratch_root": scratch_root,
        "storage_budget": int(storage_budget),
        "creation_time": utc_now_iso(),
        "created_epoch": now if now is not None else time.time(),
        "owner": owner,
        "lease_state": "ACTIVE" if janitor_ok else "REFUSED",
        "retention_policy": retention_policy,
        "janitor": {"rc": rc, "ok": janitor_ok, "detail": out.strip()[:400]},
    }
    records = _load_registry(registry)
    records = [r for r in records if r.get("task_id") != task_id]
    records.append(record)
    _save_registry(records, registry)
    return record


def complete_scratch(
    *,
    task_id: str,
    runner: Runner = default_runner,
    paths: ResourcePaths | None = None,
    registry: Path | None = None,
    evidence: dict[str, Any] | None = None,
    request_reap: bool = True,
) -> dict[str, Any]:
    """Mark scratch finished on verified completion; request JanitorOS
    retirement under the existing policy. Rollback-critical and evidence
    records are retained (never requested for reaping)."""
    paths = paths or ResourcePaths.from_env()
    records = _load_registry(registry)
    record = next((r for r in records if r.get("task_id") == task_id), None)
    if record is None:
        return {"ok": False, "error": f"no lease registered for task {task_id}"}

    rc, out = runner(
        [paths.janitor, "space", "scratch", "complete", "--task", task_id], 20.0
    )
    record["lease_state"] = "COMPLETED_PENDING_REAP" if rc == 0 else "REFUSED"
    record["completed_utc"] = utc_now_iso()
    record["janitor_complete"] = {"rc": rc, "ok": rc == 0, "detail": out.strip()[:400]}
    record["evidence"] = evidence or {}
    reap_requested = False
    if record.get("retention_policy") != "disposable":
        reap_detail = "retained (rollback_critical/evidence)"
    elif rc != 0:
        reap_detail = f"reap not requested (janitor complete rc={rc})"
    elif not request_reap:
        reap_detail = "reap skipped (request_reap=False)"
    else:
        rrc, rout = runner(
            [paths.janitor, "space", "scratch", "reap"], 30.0
        )
        reap_requested = True
        reap_detail = rout.strip()[:400] or f"reap rc={rrc}"
        if rrc == 0:
            record["lease_state"] = "RETIRED"
    record["cleanup"] = {
        "requested": reap_requested,
        "detail": reap_detail,
        "policy": record.get("retention_policy"),
    }
    for i, existing in enumerate(records):
        if existing.get("task_id") == task_id:
            records[i] = record
            break
    _save_registry(records, registry)
    return {"ok": rc == 0, "lease": record}


def list_leases(registry: Path | None = None) -> list[dict[str, Any]]:
    return _load_registry(registry)
