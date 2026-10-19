"""AgentOS Tower — live terminal UI for monitoring and control.

Lightweight curses-based TUI showing system health, objectives, hive
workers, providers, resources, verification, and activity. Supports
keyboard interaction for safe operations.
"""

from __future__ import annotations

import curses
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentos.adapters.ai_mesh import AIMeshAdapter, ProviderState
from agentos.adapters.hive import HiveAdapter
from agentos.adapters.janitor import JanitorOSAdapter
from agentos.models import ObjectiveState
from agentos.services import AgentOS


class Tower:
    """Live AgentOS tower — monitor and control the execution plane."""

    def __init__(self, app: AgentOS) -> None:
        self.app = app
        self.hive = HiveAdapter()
        self.mesh = AIMeshAdapter()
        self.janitor = JanitorOSAdapter()
        self.selected = 0
        self.scroll_offset = 0
        self.detail_view: str | None = None
        self.last_refresh = 0.0
        self.refresh_interval = 2.0
        self.messages: list[str] = []

    def run(self, stdscr: Any) -> None:
        curses.curs_set(0)
        stdscr.timeout(int(self.refresh_interval * 1000))
        self._init_colors()
        self._stdscr = stdscr

        while True:
            self._refresh_data()
            self._draw(stdscr)
            key = stdscr.getch()
            if key == ord("q") or key == 27:
                break
            if self.detail_view is not None and key != curses.KEY_RESIZE:
                self.detail_view = None
                continue
            if key == curses.KEY_UP:
                self.selected = max(0, self.selected - 1)
            elif key == curses.KEY_DOWN:
                self.selected += 1
            elif key == ord("r"):
                self._force_refresh()
            elif key == ord("\n") or key == ord(" "):
                self._toggle_detail()
            elif key == ord("p"):
                self._pause_resume()
            elif key == ord("c"):
                self._cancel_objective()
            elif key == ord("t"):
                self._retry_objective()
            elif key == ord("v"):
                self._view_verification()
            elif key == ord("a"):
                self._view_approvals()
            elif key == ord("m"):
                self._view_routing()
            elif key == ord("R"):
                self._view_resources()
            elif key == ord("l"):
                self._view_logs()
            elif key == ord("h") or key == ord("?"):
                self._show_help()

    def _init_colors(self) -> None:
        curses.start_color()
        curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)
        curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(4, curses.COLOR_CYAN, curses.COLOR_BLACK)
        curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLACK)
        curses.init_pair(6, curses.COLOR_MAGENTA, curses.COLOR_BLACK)

    def _refresh_data(self) -> None:
        now = time.time()
        if now - self.last_refresh < self.refresh_interval:
            return
        self.last_refresh = now
        self._data = self._collect_data()

    def _force_refresh(self) -> None:
        self.last_refresh = 0.0
        self._refresh_data()

    def _collect_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system": self._system_status(),
            "objectives": self._objectives_summary(),
            "hive_workers": self._hive_workers(),
            "providers": self._providers_status(),
            "resources": self._resources_status(),
            "verification": self._verification_status(),
            "activity": self._recent_activity(),
        }
        return data

    def _system_status(self) -> dict[str, Any]:
        uptime = subprocess.run(
            ["uptime", "-p"], capture_output=True, text=True
        ).stdout.strip()
        load = os.getloadavg()
        db_size = (
            self.app.db_path.stat().st_size
            if self.app.db_path.exists()
            else 0
        )
        return {
            "uptime": uptime,
            "load": f"{load[0]:.1f} {load[1]:.1f} {load[2]:.1f}",
            "db_size": f"{db_size / 1024:.0f}KB",
            "service": self._service_status(),
            "db_health": self._db_health(),
        }

    def _service_status(self) -> str:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "agentos"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip()
        except Exception:
            return "unknown"

    def _db_health(self) -> str:
        try:
            result = self.app.store.conn.execute("PRAGMA integrity_check").fetchone()
            return result[0] if result else "unknown"
        except Exception as exc:
            return f"error: {exc}"

    def _objectives_summary(self) -> dict[str, Any]:
        objectives = self.app.list_objectives()
        by_state: dict[str, int] = {}
        for obj in objectives:
            state = obj.status.value
            by_state[state] = by_state.get(state, 0) + 1
        return {
            "total": len(objectives),
            "by_state": by_state,
            "objectives": [
                {
                    "id": o.id,
                    "title": o.title[:60],
                    "status": o.status.value,
                    "priority": o.priority.value,
                    "verification": o.verification_status.value,
                }
                for o in objectives[:20]
            ],
        }

    def _hive_workers(self) -> list[dict[str, Any]]:
        workers = self.hive.discover_workers()
        resource = self.hive.inspect_resource_usage()
        return workers if workers else []

    def _providers_status(self) -> list[dict[str, Any]]:
        providers = self.mesh.discover_providers()
        return providers

    def _resources_status(self) -> dict[str, Any]:
        forecast = self.janitor.get_storage_forecast()
        resource = self.hive.inspect_resource_usage()
        load = os.getloadavg()
        mem = self._memory_info()
        return {
            "cpu_load": f"{load[0]:.1f}",
            "memory": mem,
            "disk": forecast,
            "resource_mode": resource.get("mode", "UNKNOWN"),
            "allow_new_work": resource.get("allow_new_work", False),
        }

    def _memory_info(self) -> str:
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            total = int(lines[0].split()[1]) * 1024
            available = int(lines[2].split()[1]) * 1024
            used = total - available
            return f"{used / 1024 / 1024 / 1024:.1f}G / {total / 1024 / 1024 / 1024:.1f}G"
        except Exception:
            return "unknown"

    def _verification_status(self) -> dict[str, Any]:
        objectives = self.app.list_objectives()
        verified = sum(
            1
            for o in objectives
            if o.verification_status.value == "VERIFIED"
        )
        unverified = sum(
            1
            for o in objectives
            if o.verification_status.value == "UNVERIFIED"
        )
        failed = sum(
            1
            for o in objectives
            if o.verification_status.value == "FAILED"
        )
        return {
            "verified": verified,
            "unverified": unverified,
            "failed": failed,
        }

    def _recent_activity(self) -> list[dict[str, Any]]:
        events = self.app.events(limit=20)
        return events

    def _draw(self, stdscr: Any) -> None:
        stdscr.erase()
        max_y, max_x = stdscr.getmaxyx()
        if max_y < 24 or max_x < 80:
            stdscr.addstr(0, 0, "Terminal too small (need 80x24+)")
            stdscr.refresh()
            return

        data = getattr(self, "_data", self._collect_data())
        if self.detail_view:
            self._draw_detail(stdscr, data)
        else:
            self._draw_main(stdscr, data)
        stdscr.refresh()

    def _draw_main(self, stdscr: Any, data: dict[str, Any]) -> None:
        max_y, max_x = stdscr.getmaxyx()

        self._draw_header(stdscr, data, max_x)
        row = 2

        row = self._draw_system(stdscr, data, row, max_x)
        row = self._draw_objectives(stdscr, data, row, max_x)
        row = self._draw_hive(stdscr, data, row, max_x)
        row = self._draw_providers(stdscr, data, row, max_x)
        row = self._draw_resources(stdscr, data, row, max_x)
        row = self._draw_verification(stdscr, data, row, max_x)
        self._draw_activity(stdscr, data, row, max_x)
        self._draw_footer(stdscr, max_y, max_x)

    def _draw_header(self, stdscr: Any, data: dict[str, Any], max_x: int) -> None:
        title = " AGENTOS TOWER "
        ts = data.get("timestamp", "")
        stdscr.addstr(0, 0, title, curses.A_BOLD | curses.color_pair(4))
        stdscr.addstr(0, len(title), f" {ts[:19]}")
        stdscr.addstr(1, 0, "─" * max_x, curses.color_pair(4))

    def _draw_system(self, stdscr: Any, data: dict[str, Any], row: int, max_x: int) -> int:
        sys_data = data["system"]
        stdscr.addstr(row, 0, "SYSTEM", curses.A_BOLD | curses.color_pair(4))
        row += 1
        service_color = (
            curses.color_pair(1)
            if sys_data["service"] == "active"
            else curses.color_pair(2)
        )
        stdscr.addstr(row, 2, f"Service: ")
        stdscr.addstr(f"{sys_data['service']}", service_color)
        stdscr.addstr(f"  Uptime: {sys_data['uptime']}")
        row += 1
        stdscr.addstr(
            row,
            2,
            f"Load: {sys_data['load']}  DB: {sys_data['db_size']}  Health: {sys_data['db_health']}",
        )
        return row + 2

    def _draw_objectives(self, stdscr: Any, data: dict[str, Any], row: int, max_x: int) -> int:
        obj_data = data["objectives"]
        stdscr.addstr(row, 0, "OBJECTIVES", curses.A_BOLD | curses.color_pair(4))
        row += 1
        by_state = obj_data["by_state"]
        states = ["RUNNING", "READY", "BLOCKED", "COMPLETED", "FAILED", "CANCELLED"]
        for state in states:
            count = by_state.get(state, 0)
            if count == 0:
                continue
            color = self._state_color(state)
            stdscr.addstr(row, 2, f"{state}: ")
            stdscr.addstr(f"{count}", color)
            row += 1

        objectives = obj_data.get("objectives", [])
        if objectives:
            row += 1
            stdscr.addstr(row, 2, "ID                   STATUS      PRIORITY  TITLE", curses.A_DIM)
            row += 1
            for i, obj in enumerate(objectives[:8]):
                sel = ">" if i == self.selected else " "
                color = self._state_color(obj["status"])
                line = (
                    f"{sel} {obj['id'][:18]:<18} {obj['status']:<11} "
                    f"{obj['priority']:<8} {obj['title'][:30]}"
                )
                stdscr.addstr(row, 2, line, color)
                row += 1
        return row + 1

    def _draw_hive(self, stdscr: Any, data: dict[str, Any], row: int, max_x: int) -> int:
        workers = data["hive_workers"]
        stdscr.addstr(row, 0, "HIVE", curses.A_BOLD | curses.color_pair(4))
        row += 1
        if not workers:
            stdscr.addstr(row, 2, "No active workers", curses.color_pair(3))
            return row + 2
        for w in workers:
            stdscr.addstr(
                row,
                2,
                f"{w['system_id']:<20} workers={w['active_workers']} priority={w['priority']}",
            )
            row += 1
        return row + 1

    def _draw_providers(self, stdscr: Any, data: dict[str, Any], row: int, max_x: int) -> int:
        providers = data["providers"]
        stdscr.addstr(row, 0, "PROVIDERS", curses.A_BOLD | curses.color_pair(4))
        row += 1
        if not providers:
            stdscr.addstr(row, 2, "No providers detected", curses.color_pair(3))
            return row + 2
        for p in providers:
            state = p.get("state", "UNKNOWN")
            color = (
                curses.color_pair(1)
                if state == "READY"
                else curses.color_pair(3)
                if state == "AUTH_REQUIRED"
                else curses.color_pair(2)
            )
            stdscr.addstr(
                row,
                2,
                f"{p['id']:<15} [{state}] cost={p['cost_rank']} free={p['free']}",
                color,
            )
            row += 1
        return row + 1

    def _draw_resources(self, stdscr: Any, data: dict[str, Any], row: int, max_x: int) -> int:
        res = data["resources"]
        stdscr.addstr(row, 0, "RESOURCES", curses.A_BOLD | curses.color_pair(4))
        row += 1
        disk = res["disk"]
        disk_color = (
            curses.color_pair(2)
            if disk["used_pct"] > 90
            else curses.color_pair(3)
            if disk["used_pct"] > 80
            else curses.color_pair(1)
        )
        stdscr.addstr(row, 2, f"CPU: {res['cpu_load']}  MEM: {res['memory']}")
        row += 1
        stdscr.addstr(row, 2, "Disk: ")
        stdscr.addstr(
            f"{disk['used_pct']}% used ({disk['available_bytes'] / 1024 / 1024 / 1024:.1f}G free)",
            disk_color,
        )
        row += 1
        mode_color = (
            curses.color_pair(1)
            if res["resource_mode"] == "HEALTHY"
            else curses.color_pair(3)
            if res["resource_mode"] in ("GUARDED", "PRESSURE")
            else curses.color_pair(2)
        )
        stdscr.addstr(row, 2, "Mode: ")
        stdscr.addstr(f"{res['resource_mode']}", mode_color)
        stdscr.addstr(f"  New work: {'yes' if res['allow_new_work'] else 'no'}")
        return row + 2

    def _draw_verification(self, stdscr: Any, data: dict[str, Any], row: int, max_x: int) -> int:
        ver = data["verification"]
        stdscr.addstr(row, 0, "VERIFICATION", curses.A_BOLD | curses.color_pair(4))
        row += 1
        stdscr.addstr(row, 2, "Verified: ")
        stdscr.addstr(f"{ver['verified']}", curses.color_pair(1))
        stdscr.addstr(f"  Unverified: {ver['unverified']}  Failed: ")
        stdscr.addstr(f"{ver['failed']}", curses.color_pair(2))
        return row + 2

    def _draw_activity(self, stdscr: Any, data: dict[str, Any], row: int, max_x: int) -> int:
        events = data["activity"]
        stdscr.addstr(row, 0, "ACTIVITY", curses.A_BOLD | curses.color_pair(4))
        row += 1
        if not events:
            stdscr.addstr(row, 2, "No recent activity", curses.A_DIM)
            return row + 1
        for e in events[:6]:
            ts = e.get("created_at", "")[:19]
            etype = e.get("event_type", "")
            oid = e.get("objective_id") or "-"
            stdscr.addstr(row, 2, f"{ts}  {etype:<24} {oid[:20]}", curses.A_DIM)
            row += 1
        return row + 1

    def _draw_footer(self, stdscr: Any, max_y: int, max_x: int) -> None:
        footer = " q:quit  ↑:up  ↓:down  enter:detail  p:pause  c:cancel  t:retry  v:verify  a:approvals  m:routing  R:resources  l:logs  r:refresh  h:help "
        stdscr.addstr(max_y - 1, 0, footer[: max_x - 1], curses.A_REVERSE)

    def _state_color(self, state: str) -> int:
        if state in ("COMPLETED", "VERIFIED", "OK"):
            return curses.color_pair(1)
        if state in ("FAILED", "DOWN", "CANCELLED"):
            return curses.color_pair(2)
        if state in ("RUNNING", "VERIFYING", "BLOCKED", "READY"):
            return curses.color_pair(3)
        return curses.color_pair(5)

    def _toggle_detail(self) -> None:
        objectives = self.app.list_objectives()
        if not objectives:
            return
        idx = min(self.selected, len(objectives) - 1)
        obj = objectives[idx]
        self.detail_view = f"objective:{obj.id}"

    def _pause_resume(self) -> None:
        objectives = self.app.list_objectives()
        if not objectives:
            return
        idx = min(self.selected, len(objectives) - 1)
        obj = objectives[idx]
        if obj.status == ObjectiveState.RUNNING:
            self.messages.append(f"Paused {obj.id}")
        elif obj.status == ObjectiveState.BLOCKED:
            self.messages.append(f"Resumed {obj.id}")

    def _confirm(self, stdscr: Any, message: str) -> bool:
        max_y, max_x = stdscr.getmaxyx()
        prompt = f" {message} — press y to confirm, any key to abort "
        stdscr.addstr(max_y - 1, 0, prompt[: max_x - 1], curses.A_REVERSE)
        stdscr.refresh()
        stdscr.timeout(-1)
        key = stdscr.getch()
        stdscr.timeout(int(self.refresh_interval * 1000))
        return key == ord("y")

    def _cancel_objective(self) -> None:
        objectives = self.app.list_objectives()
        if not objectives:
            return
        idx = min(self.selected, len(objectives) - 1)
        obj = objectives[idx]
        stdscr = getattr(self, "_stdscr", None)
        if stdscr is None or not self._confirm(stdscr, f"Cancel {obj.id}?"):
            self.messages.append(f"Cancel aborted for {obj.id}")
            return
        self.app.cancel_objective(obj.id)
        self.messages.append(f"Cancelled {obj.id}")

    def _retry_objective(self) -> None:
        objectives = self.app.list_objectives()
        if not objectives:
            return
        idx = min(self.selected, len(objectives) - 1)
        obj = objectives[idx]
        if obj.status in (ObjectiveState.FAILED, ObjectiveState.BLOCKED):
            self.app.run(obj.id, force=True)
            self.messages.append(f"Retried {obj.id}")

    def _view_verification(self) -> None:
        objectives = self.app.list_objectives()
        if not objectives:
            return
        idx = min(self.selected, len(objectives) - 1)
        obj = objectives[idx]
        self.detail_view = f"verification:{obj.id}"

    def _view_approvals(self) -> None:
        self.detail_view = "approvals"

    def _view_routing(self) -> None:
        self.detail_view = "routing"

    def _view_resources(self) -> None:
        self.detail_view = "resources"

    def _view_logs(self) -> None:
        self.detail_view = "logs"

    def _show_help(self) -> None:
        self.detail_view = "help"

    def _draw_detail(self, stdscr: Any, data: dict[str, Any]) -> None:
        max_y, max_x = stdscr.getmaxyx()
        parts = self.detail_view.split(":")
        view_type = parts[0]

        if view_type == "objective" and len(parts) > 1:
            self._draw_objective_detail(stdscr, parts[1], max_y, max_x)
        elif view_type == "verification" and len(parts) > 1:
            self._draw_verification_detail(stdscr, parts[1], max_y, max_x)
        elif view_type == "approvals":
            self._draw_approvals_detail(stdscr, max_y, max_x)
        elif view_type == "routing":
            self._draw_routing_detail(stdscr, max_y, max_x)
        elif view_type == "resources":
            self._draw_resources_detail(stdscr, data, max_y, max_x)
        elif view_type == "logs":
            self._draw_logs_detail(stdscr, max_y, max_x)
        elif view_type == "help":
            self._draw_help_detail(stdscr, max_y, max_x)
        else:
            stdscr.addstr(0, 0, "Unknown detail view")

    def _draw_objective_detail(self, stdscr: Any, obj_id: str, max_y: int, max_x: int) -> None:
        detail = self.app.inspect(obj_id)
        obj = detail["objective"]
        stdscr.addstr(0, 0, f" OBJECTIVE: {obj['id']} ", curses.A_BOLD | curses.color_pair(4))
        row = 2
        for key in ("title", "status", "priority", "verification_status", "strategy", "next_action"):
            if obj.get(key):
                stdscr.addstr(row, 2, f"{key}: {obj[key]}")
                row += 1
        row += 1
        stdscr.addstr(row, 0, "EXECUTIONS", curses.A_BOLD | curses.color_pair(4))
        row += 1
        for ex in detail.get("executions", [])[:10]:
            stdscr.addstr(
                row,
                2,
                f"{ex['id'][:18]} [{ex['status']}] {ex['capability_id']}:{ex['operation']}",
            )
            row += 1
        stdscr.addstr(max_y - 1, 0, "Press any key to go back", curses.A_REVERSE)

    def _draw_verification_detail(self, stdscr: Any, obj_id: str, max_y: int, max_x: int) -> None:
        ver = self.app.verify(obj_id)
        stdscr.addstr(0, 0, f" VERIFICATION: {ver.id} ", curses.A_BOLD | curses.color_pair(4))
        row = 2
        stdscr.addstr(row, 2, f"Verified: {ver.verified}")
        row += 1
        stdscr.addstr(row, 2, f"Method: {ver.method}")
        row += 1
        for ev in ver.evidence[:15]:
            stdscr.addstr(row, 2, f"[{ev.kind}] {ev.detail[:80]}")
            row += 1
        stdscr.addstr(max_y - 1, 0, "Press any key to go back", curses.A_REVERSE)

    def _draw_approvals_detail(self, stdscr: Any, max_y: int, max_x: int) -> None:
        stdscr.addstr(0, 0, " APPROVALS ", curses.A_BOLD | curses.color_pair(4))
        row = 2
        pending = self.app.dashboard().get("pending_approvals", [])
        if not pending:
            stdscr.addstr(row, 2, "No pending approvals")
        for a in pending:
            stdscr.addstr(row, 2, f"- {a}")
            row += 1
        stdscr.addstr(max_y - 1, 0, "Press any key to go back", curses.A_REVERSE)

    def _draw_routing_detail(self, stdscr: Any, max_y: int, max_x: int) -> None:
        stdscr.addstr(0, 0, " MODEL ROUTING ", curses.A_BOLD | curses.color_pair(4))
        row = 2
        providers = self.mesh.discover_providers()
        for p in providers:
            stdscr.addstr(
                row,
                2,
                f"{p['id']:<15} [{p['state']}] cost={p['cost_rank']} free={p['free']} caps={','.join(p['capabilities'])}",
            )
            row += 1
        stdscr.addstr(max_y - 1, 0, "Press any key to go back", curses.A_REVERSE)

    def _draw_resources_detail(self, stdscr: Any, data: dict[str, Any], max_y: int, max_x: int) -> None:
        stdscr.addstr(0, 0, " RESOURCE USAGE ", curses.A_BOLD | curses.color_pair(4))
        row = 2
        res = data["resources"]
        stdscr.addstr(row, 2, f"CPU Load: {res['cpu_load']}")
        row += 1
        stdscr.addstr(row, 2, f"Memory: {res['memory']}")
        row += 1
        disk = res["disk"]
        stdscr.addstr(
            row,
            2,
            f"Disk: {disk['used_pct']}% used, {disk['available_bytes'] / 1024 / 1024 / 1024:.1f}G free",
        )
        row += 1
        stdscr.addstr(row, 2, f"Resource Mode: {res['resource_mode']}")
        row += 1
        stdscr.addstr(row, 2, f"Allow New Work: {res['allow_new_work']}")
        row += 1
        stdscr.addstr(max_y - 1, 0, "Press any key to go back", curses.A_REVERSE)

    def _draw_logs_detail(self, stdscr: Any, max_y: int, max_x: int) -> None:
        stdscr.addstr(0, 0, " LOGS ", curses.A_BOLD | curses.color_pair(4))
        row = 2
        events = self.app.events(limit=30)
        for e in events:
            ts = e.get("created_at", "")[:19]
            etype = e.get("event_type", "")
            oid = e.get("objective_id") or "-"
            stdscr.addstr(row, 2, f"{ts}  {etype:<24} {oid[:30]}")
            row += 1
        stdscr.addstr(max_y - 1, 0, "Press any key to go back", curses.A_REVERSE)

    def _draw_help_detail(self, stdscr: Any, max_y: int, max_x: int) -> None:
        stdscr.addstr(0, 0, " HELP ", curses.A_BOLD | curses.color_pair(4))
        help_lines = [
            "",
            "  ↑/↓     Navigate objectives",
            "  Enter   View objective detail",
            "  p       Pause/resume objective",
            "  c       Cancel objective",
            "  t       Retry failed objective",
            "  v       View verification",
            "  a       View approvals",
            "  m       View model routing",
            "  R       View resources",
            "  l       View logs",
            "  r       Force refresh",
            "  h       Show this help",
            "  q/ESC   Quit tower",
            "",
        ]
        for i, line in enumerate(help_lines[: max_y - 2]):
            stdscr.addstr(i + 2, 2, line)
        stdscr.addstr(max_y - 1, 0, "Press any key to go back", curses.A_REVERSE)


def run_tower(app: AgentOS) -> None:
    tower = Tower(app)
    curses.wrapper(tower.run)
