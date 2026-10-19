"""Tests for the AgentOS Tower — live terminal UI."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch


class TestTower(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        os.environ["AGENTOS_HOME"] = self.temp_dir

    def test_tower_init(self) -> None:
        from agentos.services import AgentOS
        from agentos.tower import Tower

        app = AgentOS(home=self.temp_dir)
        tower = Tower(app)
        self.assertIsNotNone(tower.app)
        self.assertIsNotNone(tower.hive)
        self.assertIsNotNone(tower.mesh)
        self.assertIsNotNone(tower.janitor)

    def test_collect_data(self) -> None:
        from agentos.services import AgentOS
        from agentos.tower import Tower

        app = AgentOS(home=self.temp_dir)
        tower = Tower(app)
        data = tower._collect_data()
        self.assertIn("timestamp", data)
        self.assertIn("system", data)
        self.assertIn("objectives", data)
        self.assertIn("hive_workers", data)
        self.assertIn("providers", data)
        self.assertIn("resources", data)
        self.assertIn("verification", data)
        self.assertIn("activity", data)

    def test_system_status(self) -> None:
        from agentos.services import AgentOS
        from agentos.tower import Tower

        app = AgentOS(home=self.temp_dir)
        tower = Tower(app)
        status = tower._system_status()
        self.assertIn("uptime", status)
        self.assertIn("load", status)
        self.assertIn("db_size", status)
        self.assertIn("service", status)
        self.assertIn("db_health", status)

    def test_objectives_summary(self) -> None:
        from agentos.services import AgentOS
        from agentos.tower import Tower

        app = AgentOS(home=self.temp_dir)
        tower = Tower(app)
        summary = tower._objectives_summary()
        self.assertIn("total", summary)
        self.assertIn("by_state", summary)
        self.assertEqual(summary["total"], 0)

    def test_hive_workers(self) -> None:
        from agentos.services import AgentOS
        from agentos.tower import Tower

        app = AgentOS(home=self.temp_dir)
        tower = Tower(app)
        workers = tower._hive_workers()
        self.assertIsInstance(workers, list)

    def test_providers_status(self) -> None:
        from agentos.services import AgentOS
        from agentos.tower import Tower

        app = AgentOS(home=self.temp_dir)
        tower = Tower(app)
        providers = tower._providers_status()
        self.assertIsInstance(providers, list)

    def test_resources_status(self) -> None:
        from agentos.services import AgentOS
        from agentos.tower import Tower

        app = AgentOS(home=self.temp_dir)
        tower = Tower(app)
        resources = tower._resources_status()
        self.assertIn("cpu_load", resources)
        self.assertIn("memory", resources)
        self.assertIn("disk", resources)
        self.assertIn("resource_mode", resources)

    def test_verification_status(self) -> None:
        from agentos.services import AgentOS
        from agentos.tower import Tower

        app = AgentOS(home=self.temp_dir)
        tower = Tower(app)
        ver = tower._verification_status()
        self.assertIn("verified", ver)
        self.assertIn("unverified", ver)
        self.assertIn("failed", ver)

    @patch("agentos.tower.curses")
    def test_state_color(self, mock_curses: MagicMock) -> None:
        from agentos.services import AgentOS
        from agentos.tower import Tower

        mock_curses.color_pair.return_value = 1
        app = AgentOS(home=self.temp_dir)
        tower = Tower(app)
        color = tower._state_color("COMPLETED")
        self.assertIsNotNone(color)

    def test_cancel_objective_requires_confirmation(self) -> None:
        from agentos.services import AgentOS
        from agentos.tower import Tower

        app = AgentOS(home=self.temp_dir)
        tower = Tower(app)
        app.create_objective("test objective")
        tower.selected = 0
        tower._cancel_objective()
        self.assertIn("Cancel aborted", tower.messages[0])
        self.assertEqual(
            app.list_objectives()[0].status.value, "READY"
        )

    def test_cancel_objective_confirmed(self) -> None:
        from agentos.services import AgentOS
        from agentos.tower import Tower

        app = AgentOS(home=self.temp_dir)
        tower = Tower(app)
        app.create_objective("test objective")
        tower.selected = 0
        tower._stdscr = MagicMock()
        with patch.object(Tower, "_confirm", return_value=True):
            tower._cancel_objective()
        self.assertIn("Cancelled", tower.messages[0])
        self.assertEqual(
            app.list_objectives()[0].status.value, "CANCELLED"
        )


if __name__ == "__main__":
    unittest.main()


class FakeScr:
    """Faithful-enough curses fake: raises on out-of-bounds writes."""

    def __init__(self, rows: int = 24, cols: int = 80) -> None:
        self.rows = rows
        self.cols = cols
        self.writes: list[tuple[int, int, str]] = []
        self._y = 0
        self._x = 0

    def getmaxyx(self) -> tuple[int, int]:
        return (self.rows, self.cols)

    def addstr(self, *args, **kw) -> None:
        import curses as _curses

        if len(args) >= 2 and isinstance(args[0], int):
            y, x = args[0], args[1]
            text = str(args[2]) if len(args) > 2 else ""
        else:
            y, x = self._y, self._x
            text = str(args[0]) if args else ""
        if y < 0 or x < 0 or y >= self.rows or x >= self.cols:
            raise _curses.error("addwstr() returned ERR")
        if y == self.rows - 1 and x + max(len(text), 1) >= self.cols:
            raise _curses.error("addwstr() returned ERR")
        self.writes.append((y, x, text))
        self._y, self._x = y, x + len(text)

    def erase(self) -> None:
        self.writes.clear()

    def refresh(self) -> None:
        pass


class TestTowerBounds(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        os.environ["AGENTOS_HOME"] = self.temp_dir
        from agentos.services import AgentOS
        from agentos.tower import Tower, _SafeScr

        self.app = AgentOS(home=self.temp_dir)
        self.tower = Tower(self.app)
        self._SafeScr = _SafeScr
        # populate enough content to overflow a 24-row terminal
        for i in range(8):
            self.app.create_objective(f"bulk objective {i}", operation="echo")

    def test_draw_main_survives_overflow_on_24_rows(self) -> None:
        import curses

        data = self.tower._collect_data()
        scr = FakeScr(24, 80)
        with patch.object(curses, "color_pair", return_value=0):
            self.tower._draw_main(self._SafeScr(scr), data)
        # footer must still be written on the last row
        self.assertTrue(any(y == 23 for y, x, t in scr.writes))

    def test_raw_screen_raises_without_proxy(self) -> None:
        # documents the vulnerability the proxy fixes: a section entering
        # near the bottom row overflows deterministically on a raw screen
        import curses

        data = {
            "activity": [
                {
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "event_type": "objective.started",
                    "objective_id": "obj_" + "a" * 20,
                }
                for _ in range(6)
            ]
        }
        scr = FakeScr(24, 80)
        with patch.object(curses, "color_pair", return_value=0):
            with self.assertRaises(curses.error):
                self.tower._draw_activity(scr, data, 23, 80)

    def test_swallow_write_errors_only(self) -> None:
        import curses

        inner = FakeScr(10, 20)
        safe = self._SafeScr(inner)
        safe.addstr(50, 0, "way off screen")  # swallowed
        self.assertEqual(inner.writes, [])
        # non-write attributes still pass through
        self.assertEqual(safe.getmaxyx(), (10, 20))

    def test_small_terminal_shows_message_not_crash(self) -> None:
        import curses

        scr = FakeScr(10, 40)
        with patch.object(curses, "color_pair", return_value=0):
            self.tower._draw(self._SafeScr(scr))
        self.assertTrue(
            any("Terminal too small" in t for y, x, t in scr.writes)
        )
