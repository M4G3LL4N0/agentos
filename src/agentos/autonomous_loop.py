"""Bounded autonomous operating loop for AgentOS.

Ten explicit stages: INSPECT, PLAN, DECOMPOSE, ROUTE, DELEGATE, EXECUTE,
VERIFY, REVIEW, REPAIR, COMPLETE. Hard-bounded by cycle, time, worker,
and cost limits. Free-only provider routing unless an explicit paid
authorization is recorded. State persists across restarts so an
interrupted loop recovers instead of losing progress.

No infinite loops: every run terminates when limits are reached or the
objective reaches a terminal state.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from enum import StrEnum
from pathlib import Path
from typing import Any

from agentos.engine import AgentOSError
from agentos.models import ObjectiveState, utc_now_iso

MAX_STAGES = 10


class LoopStage(StrEnum):
    INSPECT = "INSPECT"
    PLAN = "PLAN"
    DECOMPOSE = "DECOMPOSE"
    ROUTE = "ROUTE"
    DELEGATE = "DELEGATE"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    REVIEW = "REVIEW"
    REPAIR = "REPAIR"
    COMPLETE = "COMPLETE"


LOOP_ORDER = [
    LoopStage.INSPECT,
    LoopStage.PLAN,
    LoopStage.DECOMPOSE,
    LoopStage.ROUTE,
    LoopStage.DELEGATE,
    LoopStage.EXECUTE,
    LoopStage.VERIFY,
    LoopStage.REVIEW,
    LoopStage.REPAIR,
    LoopStage.COMPLETE,
]


class LoopStatus(StrEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    INTERRUPTED = "INTERRUPTED"
    LIMIT_REACHED = "LIMIT_REACHED"


@dataclass
class LoopLimits:
    max_cycles: int = 5
    max_seconds: float = 600.0
    max_workers: int = 4
    max_cost_usd: float = 0.0
    free_only: bool = True
    allowed_providers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoopLimits:
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**known)


@dataclass
class LoopState:
    status: str = LoopStatus.STOPPED
    objective_id: str = ""
    stage_index: int = 0
    cycles: int = 0
    started_at: str = ""
    updated_at: str = ""
    spent_seconds: float = 0.0
    spent_usd: float = 0.0
    workers_used: int = 0
    last_error: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoopState:
        known = {f: data.get(f) for f in cls.__dataclass_fields__}
        return cls(**known)


class AutonomousLoop:
    """Bounded autonomous progression over one objective at a time."""

    def __init__(
        self,
        app: Any,
        home: Path | None = None,
        limits: LoopLimits | None = None,
    ) -> None:
        self.app = app
        self.home = home or Path(app.home)
        self.limits = limits or LoopLimits()
        self.state_path = self.home / "loop_state.json"
        self.limits_path = self.home / "loop_limits.json"
        self._executing = False
        self._load()

    # -- persistence ----------------------------------------------------
    def _load(self) -> None:
        if self.state_path.exists():
            try:
                self.state = LoopState.from_dict(
                    json.loads(self.state_path.read_text())
                )
            except (json.JSONDecodeError, OSError):
                self.state = LoopState()
            if self.state.status == LoopStatus.RUNNING:
                # a crash left us RUNNING: mark interrupted so recovery
                # logic can decide — never assume the run survived.
                self.state.status = LoopStatus.INTERRUPTED
                self.state.last_error = "process interrupted"
                self._save()
        else:
            self.state = LoopState()
        if self.limits_path.exists():
            try:
                self.limits = LoopLimits.from_dict(
                    json.loads(self.limits_path.read_text())
                )
            except (json.JSONDecodeError, OSError):
                pass

    def _save(self) -> None:
        self.state.updated_at = utc_now_iso()
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state.to_dict(), indent=2))
        tmp.replace(self.state_path)

    def save_limits(self) -> None:
        self.limits_path.write_text(json.dumps(self.limits.to_dict(), indent=2))

    # -- limits ---------------------------------------------------------
    def _within_limits(self, now: float, started: float) -> tuple[bool, str]:
        if self.state.cycles >= self.limits.max_cycles:
            return False, f"cycle limit {self.limits.max_cycles} reached"
        if now - started >= self.limits.max_seconds:
            return False, f"time limit {self.limits.max_seconds:g}s reached"
        if self.state.spent_usd > self.limits.max_cost_usd:
            return False, f"cost limit ${self.limits.max_cost_usd:g} reached"
        if self.state.workers_used > self.limits.max_workers:
            return False, f"worker limit {self.limits.max_workers} reached"
        return True, ""

    def paid_execution_allowed(self, authorization: list[dict] | None) -> bool:
        if self.limits.free_only:
            return False
        for item in authorization or []:
            tags = [str(item.get("kind", "")).lower(), str(item.get("grant", "")).lower()]
            if "paid" in tags or "approved" in tags:
                return True
        return False

    # -- control --------------------------------------------------------
    def pause(self) -> LoopState:
        if self.state.status == LoopStatus.RUNNING:
            self.state.status = LoopStatus.PAUSED
            self._save()
        return self.state

    def resume(self, objective_id: str | None = None) -> LoopState:
        if self.state.status in (
            LoopStatus.PAUSED,
            LoopStatus.INTERRUPTED,
            LoopStatus.LIMIT_REACHED,
            LoopStatus.STOPPED,
        ):
            if objective_id:
                self.state.objective_id = objective_id
            self.state.status = LoopStatus.RUNNING
            self.state.last_error = ""
            self._save()
        return self.state

    def interrupt(self) -> LoopState:
        self.state.status = LoopStatus.INTERRUPTED
        self._save()
        return self.state

    # -- execution ------------------------------------------------------
    def _record(self, stage: str, detail: str, ok: bool = True) -> None:
        self.state.history.append(
            {"at": utc_now_iso(), "stage": stage, "detail": detail, "ok": ok}
        )
        if len(self.state.history) > 200:
            self.state.history = self.state.history[-200:]

    def _emit(self, stage: str, objective_id: str, payload: dict[str, Any]) -> None:
        # loop history is persisted in loop_state.json; also mirror into the
        # event log using existing verified event types only.
        try:
            from agentos.models import Event, EventType, new_id

            event_type = (
                EventType.VERIFICATION_PASSED
                if payload.get("ok")
                else EventType.VERIFICATION_FAILED
            )
            self.app.store.save_event(
                Event(
                    id=new_id("evt"),
                    event_type=event_type,
                    objective_id=objective_id,
                    payload={"loop_stage": stage, **payload},
                    created_at=utc_now_iso(),
                )
            )
        except Exception:
            pass

    def run(self, objective_id: str) -> dict[str, Any]:
        """Run bounded autonomous progression until terminal or limited.

        Returns a report with final status, stages completed and evidence.
        """
        if self._executing:
            raise AgentOSError("loop already running")
        objective = self.app.store.get_objective(objective_id)
        if objective is None:
            raise AgentOSError(f"unknown objective: {objective_id}")

        self._executing = True
        self.state.status = LoopStatus.RUNNING
        self.state.objective_id = objective_id
        if not self.state.started_at:
            self.state.started_at = utc_now_iso()
        self._save()

        started = time.monotonic()
        report: dict[str, Any] = {
            "objective_id": objective_id,
            "stages": [],
            "ok": True,
            "final_status": "",
            "termination": "",
        }

        try:
            for stage in LOOP_ORDER:
                if stage in (LoopStage.REPAIR, LoopStage.COMPLETE):
                    continue  # handled below by flow control
                ok, detail = self._run_stage(stage, objective_id)
                report["stages"].append({"stage": stage.value, "ok": ok, "detail": detail})
                self._record(stage.value, detail, ok)
                self._emit(stage.value, objective_id, {"ok": ok, "detail": detail})

                within, reason = self._within_limits(time.monotonic(), started)
                if not within:
                    self.state.status = LoopStatus.LIMIT_REACHED
                    self.state.last_error = reason
                    report["termination"] = reason
                    report["ok"] = ok
                    break

                if not ok:
                    # REVIEW+REPAIR attempt for failed stage
                    recovered, rdetail = self._repair(objective_id, stage, detail)
                    self.state.cycles += 1
                    self._record(LoopStage.REPAIR.value, rdetail, recovered)
                    if not recovered:
                        report["stages"].append(
                            {"stage": LoopStage.REPAIR.value, "ok": False, "detail": rdetail}
                        )
                        report["ok"] = False
                        report["termination"] = rdetail
                        break
                    self.state.cycles += 0
                    # replay the failed stage once after repair
                    ok2, detail2 = self._run_stage(stage, objective_id)
                    self._record(stage.value, f"retry: {detail2}", ok2)
                    if not ok2:
                        report["ok"] = False
                        report["termination"] = f"stage {stage} failed after repair"
                        break
                    report["stages"].append(
                        {"stage": stage.value, "ok": True, "detail": f"retry: {detail2}"}
                    )
                    continue
            else:
                # all stages passed: final completion bookkeeping
                final_ok, final_detail = self._complete(objective_id)
                report["stages"].append(
                    {"stage": LoopStage.COMPLETE.value, "ok": final_ok, "detail": final_detail}
                )
                self._record(LoopStage.COMPLETE.value, final_detail, final_ok)
                report["ok"] = final_ok
                if not final_ok:
                    report["termination"] = final_detail
        finally:
            self._executing = False
            self.state.spent_seconds += time.monotonic() - started
            if self.state.status == LoopStatus.RUNNING:
                self.state.status = LoopStatus.STOPPED
            if not report["termination"]:
                report["termination"] = self.state.status
            self._save()

        report["final_status"] = self.state.status
        report["cycles"] = self.state.cycles
        return report

    # -- stage implementations ------------------------------------------
    def _run_stage(self, stage: LoopStage, objective_id: str) -> tuple[bool, str]:
        if stage == LoopStage.INSPECT:
            obj = self.app.store.get_objective(objective_id)
            if obj is None:
                return False, "objective disappeared"
            return True, f"state={obj.status.value}"
        if stage == LoopStage.PLAN:
            plans = self.app.store.list_plans(objective_id)
            if plans:
                return True, f"existing plan {plans[0].id}"
            return True, "plan will be created by engine"
        if stage == LoopStage.DECOMPOSE:
            obj = self.app.store.get_objective(objective_id)
            if obj and obj.description:
                return True, "objective has description"
            return True, "single-step objective"
        if stage == LoopStage.ROUTE:
            from agentos.adapters.ai_mesh import AIMeshAdapter

            mesh = AIMeshAdapter()
            providers = mesh.discover_providers()
            if not providers:
                return False, "no providers available"
            ready = [p for p in providers if p.get("state") == "READY"]
            if not ready:
                return False, "no READY providers"
            return True, f"routed: {ready[0]['id']}"
        if stage == LoopStage.DELEGATE:
            # host-resource, then storage, admission before delegating
            host = getattr(self.app, "host_admission", None)
            if host:
                decision = host(bytes_needed=1024 * 1024)
                if not decision.get("admit", True):
                    return False, f"host governor refused: {decision.get('reason')}"
            from agentos.adapters.janitor import JanitorOSAdapter

            jan = JanitorOSAdapter()
            if jan.janitor_binary:
                admission = jan.request_admission(1024 * 1024)
                if not admission.get("ok"):
                    return False, f"storage admission refused: {admission.get('error')}"
            self.state.workers_used = min(self.state.workers_used + 1, self.limits.max_workers)
            return True, "delegated to execution plan"
        if stage == LoopStage.EXECUTE:
            report = self.app.run(objective_id, force=True, timeout_seconds=120.0)
            terminal = (
                ObjectiveState.COMPLETED,
                ObjectiveState.FAILED,
                ObjectiveState.CANCELLED,
            )
            if report.state in terminal:
                return True, f"execution state={report.state.value}"
            if report.state == ObjectiveState.BLOCKED:
                return False, f"blocked: {report.failure or 'unknown'}"
            return False, f"non-terminal state {report.state.value}"
        if stage == LoopStage.VERIFY:
            verification = self.app.verify(objective_id)
            if verification.verified:
                return True, f"verified via {verification.method}"
            return False, "verification failed"
        if stage == LoopStage.REVIEW:
            obj = self.app.store.get_objective(objective_id)
            if obj is None:
                return False, "objective missing after verify"
            if obj.verification_status.value == "VERIFIED":
                return True, "review passed"
            return False, f"review: {obj.verification_status.value}"
        return False, f"unknown stage {stage}"

    def _repair(self, objective_id: str, stage: LoopStage, detail: str) -> tuple[bool, str]:
        # bounded repair: single recovery attempt via app.recover
        try:
            report = self.app.recover(objective_id)
            return True, f"recovered objective ({stage.value}: {detail})"
        except AgentOSError as exc:
            return False, f"repair failed: {exc}"

    def _complete(self, objective_id: str) -> tuple[bool, str]:
        obj = self.app.store.get_objective(objective_id)
        if obj is None:
            return False, "objective missing at completion"
        if obj.status == ObjectiveState.COMPLETED and (
            obj.verification_status.value == "VERIFIED"
        ):
            return True, f"{objective_id} COMPLETED and VERIFIED"
        if obj.status == ObjectiveState.COMPLETED:
            return True, f"{objective_id} COMPLETED (verification={obj.verification_status.value})"
        return False, f"objective did not complete: {obj.status.value}"
