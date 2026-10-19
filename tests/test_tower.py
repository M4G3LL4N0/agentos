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

    def test_cancel_objective(self) -> None:
        from agentos.services import AgentOS
        from agentos.tower import Tower

        app = AgentOS(home=self.temp_dir)
        tower = Tower(app)
        app.create_objective("test objective")
        tower.selected = 0
        tower._cancel_objective()
        self.assertIn("Cancelled", tower.messages[0])


if __name__ == "__main__":
    unittest.main()
