"""Routines - cheap detector + early exit, delta report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentos.store import Store
from agentos.models import Event, EventType, utc_now_iso, new_id
from agentos.efficiency import compute_delta, content_state


@dataclass
class Routine:
    """A routine with cheap detector and workflow."""
    id: str
    trigger: str
    cheap_detector: str  # Name of detector function
    change_condition: str
    workflow_id: str | None
    early_exit: str = "NO_ACTION"  # NO_ACTION, RUN_WORKFLOW, ALERT
    report_policy: str = "delta"  # delta, full, none
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = utc_now_iso()
        if not self.updated_at:
            self.updated_at = utc_now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "trigger": self.trigger,
            "cheap_detector": self.cheap_detector,
            "change_condition": self.change_condition,
            "workflow_id": self.workflow_id,
            "early_exit": self.early_exit,
            "report_policy": self.report_policy,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# Built-in detectors
def detector_usage_snapshot(store: Store, params: dict[str, Any]) -> dict[str, Any]:
    """Detect if usage snapshot has changed."""
    data_dir = params.get("data_dir", "")
    if not data_dir:
        return {"changed": False, "reason": "no data_dir"}

    from pathlib import Path
    path = Path(data_dir).expanduser()
    if not path.exists():
        return {"changed": False, "reason": "data_dir not found"}

    files = [file for file in path.glob("*") if file.is_file()]
    if not files:
        return {"changed": False, "reason": "no files"}
    latest_mtime = max(file.stat().st_mtime for file in files)

    last_import = store.conn.execute(
        "SELECT MAX(updated_at) FROM usage_snapshots"
    ).fetchone()
    if not last_import or not last_import[0]:
        return {"changed": True, "reason": "no previous import", "latest_mtime": latest_mtime}

    from datetime import datetime
    try:
        baseline = datetime.fromisoformat(str(last_import[0]))
        if baseline.tzinfo is None:
            from datetime import timezone
            baseline = baseline.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return {"changed": True, "reason": "invalid previous import timestamp", "latest_mtime": latest_mtime}
    if latest_mtime > baseline.timestamp():
        return {"changed": True, "reason": "new export files", "latest_mtime": latest_mtime}
    return {"changed": False, "reason": "unchanged", "latest_mtime": latest_mtime}


def detector_ecosystem_delta(store: Store, params: dict[str, Any]) -> dict[str, Any]:
    """Detect if ecosystem candidates have changed."""
    # Get last scan
    candidates = store.list_ecosystem_candidates()
    if not candidates:
        return {"changed": True, "reason": "no baseline"}

    # In real system, would compare with current sources
    return {"changed": False, "reason": "no new sources checked"}


def detector_repo_changes(store: Store, params: dict[str, Any]) -> dict[str, Any]:
    """Detect if repository has changes since last scan."""
    project_path = params.get("project_path", "")
    if not project_path:
        return {"changed": False, "reason": "no project_path"}

    from pathlib import Path
    import subprocess

    path = Path(project_path).expanduser()
    if not path.exists():
        return {"changed": False, "reason": "project not found"}

    try:
        # Check git status
        result = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            return {"changed": True, "reason": "uncommitted changes", "files": result.stdout.strip().split("\n")}
        return {"changed": False, "reason": "clean working tree"}
    except Exception:
        return {"changed": False, "reason": "git check failed"}


def detector_location_arrival(store: Store, params: dict[str, Any]) -> dict[str, Any]:
    """Detect if location context has changed."""
    # Would check GPS or location context
    return {"changed": False, "reason": "location detector not implemented"}


BUILTIN_DETECTORS = {
    "usage_snapshot": detector_usage_snapshot,
    "ecosystem_delta": detector_ecosystem_delta,
    "repo_changes": detector_repo_changes,
    "location_arrival": detector_location_arrival,
}


DEFAULT_ROUTINES = [
    {
        "id": "routine_usage_refresh",
        "trigger": "usage_refresh",
        "cheap_detector": "usage_snapshot",
        "change_condition": "new export files in data_dir",
        "workflow_id": "skill_usage_refresh",
        "early_exit": "NO_ACTION",
        "report_policy": "delta",
        "enabled": True,
    },
    {
        "id": "routine_ecosystem_scan",
        "trigger": "ecosystem_scan",
        "cheap_detector": "ecosystem_delta",
        "change_condition": "new or updated candidates",
        "workflow_id": "skill_ecosystem_delta",
        "early_exit": "NO_ACTION",
        "report_policy": "delta",
        "enabled": True,
    },
    {
        "id": "routine_repo_audit",
        "trigger": "repo_audit",
        "cheap_detector": "repo_changes",
        "change_condition": "uncommitted changes or new commits",
        "workflow_id": "skill_repo_audit",
        "early_exit": "NO_ACTION",
        "report_policy": "delta",
        "enabled": True,
    },
    {
        "id": "routine_location_arrival",
        "trigger": "location_arrival",
        "cheap_detector": "location_arrival",
        "change_condition": "new location context",
        "workflow_id": "skill_location_arrival",
        "early_exit": "NO_ACTION",
        "report_policy": "full",
        "enabled": True,
    },
]


def run_cheap_detector(detector_name: str, store: Store,
                       params: dict[str, Any]) -> dict[str, Any]:
    """Run a cheap detector by name."""
    detector = BUILTIN_DETECTORS.get(detector_name)
    if not detector:
        return {"changed": False, "reason": f"unknown detector: {detector_name}"}
    return detector(store, params)


def generate_delta_report(baseline_ref: str, current_state: dict[str, Any],
                          task_intent: str, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Generate a delta report for routine output."""
    delta = compute_delta(
        baseline=dict(baseline or {}),
        current=current_state,
        task=task_intent,
        baseline_ref=baseline_ref,
        cache_scope="routine",
    )

    return {
        "baseline_ref": baseline_ref,
        "delta": delta.to_dict(),
        "task_intent": task_intent,
        "report_policy": "delta",
    }


class RoutineEngine:
    """Executes routines on trigger."""

    def __init__(self, store: Store, workflow_executor: Any | None = None):
        self.store = store
        self.workflow_executor = workflow_executor
        self._load_builtins()

    def _load_builtins(self) -> None:
        """Ensure built-in routines are in store."""
        for r in DEFAULT_ROUTINES:
            existing = self.store.conn.execute(
                "SELECT 1 FROM routines WHERE id=?", (r["id"],)
            ).fetchone()
            if not existing:
                self.store.save_routine(r)

    def handle_event(self, event: Event) -> dict[str, Any] | None:
        routine_id = str((event.payload or {}).get("routineId") or "")
        if event.event_type != EventType.OBJECTIVE_COMPLETED or not routine_id:
            return None
        return self.run_routine(routine_id, dict(event.payload or {}).get("routineParams") or {})

    def run_routine(self, routine_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run a routine and return result."""
        routine_data = self.store.conn.execute(
            "SELECT * FROM routines WHERE id=? AND enabled=1", (routine_id,)
        ).fetchone()

        if not routine_data:
            return {"ok": False, "reason": "routine not found or disabled"}

        routine = dict(routine_data)

        # Run cheap detector
        detector_result = run_cheap_detector(
            routine["cheap_detector"], self.store, params
        )

        if not detector_result.get("changed", False):
            return {
                "ok": True,
                "action": routine["early_exit"],
                "detector_result": detector_result,
                "report": None,
            }

        # Change detected - run workflow if specified
        if routine["workflow_id"] and self.workflow_executor is not None:
            workflow_result = self.workflow_executor(
                str(routine["workflow_id"]), params
            )
        elif routine["workflow_id"]:
            workflow_result = {
                "ok": False,
                "status": "UNCONFIGURED",
                "workflow_id": routine["workflow_id"],
            }
        else:
            workflow_result = None

        # Generate report
        report = None
        current_state = dict(params.get("current_state") or {})
        if not current_state:
            current_state = dict(detector_result)
        baseline = self.store.get_routine_baseline(str(routine["id"]))
        if routine["report_policy"] == "delta":
            report = generate_delta_report(
                baseline_ref=routine["id"],
                current_state=current_state,
                task_intent=routine["trigger"],
                baseline=baseline,
            )
            self.store.save_routine_baseline(str(routine["id"]), current_state)
        elif routine["report_policy"] == "full":
            report = {
                "report_policy": "full",
                "baseline": baseline,
                "current": current_state,
            }
            self.store.save_routine_baseline(str(routine["id"]), current_state)

        return {
            "ok": True,
            "action": "RUN_WORKFLOW" if routine["workflow_id"] and workflow_result.get("ok") else (
                "ALERT" if routine["workflow_id"] else "ALERT"
            ),
            "detector_result": detector_result,
            "workflow_result": workflow_result,
            "report": report,
        }

    def list_routines(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        return self.store.list_routines(enabled_only=enabled_only)

    def enable_routine(self, routine_id: str, enabled: bool = True) -> bool:
        row = self.store.conn.execute(
            "UPDATE routines SET enabled=?, updated_at=? WHERE id=?",
            (int(enabled), utc_now_iso(), routine_id)
        )
        self.store.conn.commit()
        return row.rowcount > 0