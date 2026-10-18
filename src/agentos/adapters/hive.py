"""Hive adapter: connect AgentOS to the existing NOAERTH hive scheduler.

Uses the existing Hive infrastructure at /srv/noaerth/autonomousos/mesh/hive/
for task submission, worker discovery, and status tracking. Does NOT create
a second scheduler — delegates to the existing Hive.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


class HiveAdapter:
    """Adapter to the existing NOAERTH hive scheduler."""

    def __init__(
        self,
        hive_dir: str | None = None,
        state_dir: str | None = None,
        studio_db: str | None = None,
    ) -> None:
        self.hive_dir = (
            Path(hive_dir)
            if hive_dir
            else Path("/srv/noaerth/autonomousos/mesh/hive")
        )
        self.state_dir = (
            Path(state_dir)
            if state_dir
            else Path("/srv/noaerth/autonomousos/state")
        )
        self.studio_db = (
            studio_db or "/srv/noaerth/realm-studio/state/studio.sqlite3"
        )

    def discover_workers(self) -> list[dict[str, Any]]:
        workers: list[dict[str, Any]] = []
        resource_path = self.state_dir / "resource.json"
        if resource_path.exists():
            try:
                data = json.loads(resource_path.read_text())
                for system in data.get("systems", []):
                    if system.get("active_workers", 0) > 0:
                        workers.append(
                            {
                                "system_id": system.get("system_id"),
                                "active_workers": system.get("active_workers"),
                                "priority": system.get("priority"),
                            }
                        )
            except (json.JSONDecodeError, OSError):
                pass
        return workers

    def inspect_resource_usage(self) -> dict[str, Any]:
        resource_path = self.state_dir / "resource.json"
        if not resource_path.exists():
            return {"ok": False, "error": "resource.json not found"}
        try:
            data = json.loads(resource_path.read_text())
            return {
                "ok": True,
                "mode": data.get("mode", "UNKNOWN"),
                "disk_pct": data.get("disk_pct", 0),
                "allow_new_work": data.get("allow_new_work", False),
                "systems": data.get("systems", []),
            }
        except (json.JSONDecodeError, OSError) as exc:
            return {"ok": False, "error": str(exc)}

    def _check_resource_admission(self) -> tuple[bool, str]:
        usage = self.inspect_resource_usage()
        if not usage["ok"]:
            return False, usage.get("error", "resource check failed")
        if usage.get("mode") in ("SEVERE", "CRITICAL", "ERROR"):
            return False, f"resource mode is {usage['mode']}"
        if not usage.get("allow_new_work", False):
            return False, "allow_new_work is false"
        if usage.get("disk_pct", 0) >= 90:
            return False, f"disk usage {usage['disk_pct']}% >= 90%"
        return True, "ok"

    def _submit_to_studio(self, task: dict[str, Any]) -> dict[str, Any]:
        if not os.path.exists(self.studio_db):
            return {"error": f"studio db not found: {self.studio_db}"}
        try:
            conn = sqlite3.connect(self.studio_db, timeout=10)
            cursor = conn.cursor()
            task_id = f"task_{os.urandom(6).hex()}"
            cursor.execute(
                """INSERT INTO tasks (id, title, prompt, level, agent, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                (
                    task_id,
                    task.get("title", ""),
                    task.get("prompt", ""),
                    task.get("level", "MEDIUM"),
                    task.get("agent", "opencode"),
                    "PENDING",
                ),
            )
            conn.commit()
            conn.close()
            return {"task_id": task_id}
        except sqlite3.Error as exc:
            return {"error": str(exc)}

    def _update_task_status(self, task_id: str, status: str) -> dict[str, Any]:
        if not os.path.exists(self.studio_db):
            return {"ok": False, "error": "studio db not found"}
        try:
            conn = sqlite3.connect(self.studio_db, timeout=10)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE tasks SET status = ? WHERE id = ?", (status, task_id)
            )
            conn.commit()
            conn.close()
            return {"ok": True}
        except sqlite3.Error as exc:
            return {"ok": False, "error": str(exc)}

    def submit_task(self, task: dict[str, Any]) -> dict[str, Any]:
        allowed, reason = self._check_resource_admission()
        if not allowed:
            return {"ok": False, "error": reason}
        result = self._submit_to_studio(task)
        if "error" in result:
            return {"ok": False, "error": result["error"]}
        return {"ok": True, "task_id": result["task_id"]}

    def get_task_status(self, task_id: str) -> dict[str, Any] | None:
        if not os.path.exists(self.studio_db):
            return None
        try:
            conn = sqlite3.connect(self.studio_db, timeout=10)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, status, agent FROM tasks WHERE id = ?", (task_id,)
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                return {"task_id": row[0], "status": row[1], "agent": row[2]}
            return None
        except sqlite3.Error:
            return None

    def get_task_logs(self, task_id: str) -> str:
        return f"logs for {task_id} not yet implemented"

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        return self._update_task_status(task_id, "CANCELLED")

    def collect_artifacts(self, task_id: str) -> list[str]:
        return []

    def collect_verification(self, task_id: str) -> dict[str, Any]:
        return {"task_id": task_id, "verified": False, "evidence": []}
