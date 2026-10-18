"""Tests for the AutonomousOS adapter — system registration and demand."""

from __future__ import annotations

import json
import os
import tempfile
import unittest


class TestAutonomousOSAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.state_dir = os.path.join(self.temp_dir, "agentos", "state")
        os.makedirs(self.state_dir, exist_ok=True)

    def test_register_creates_demand(self) -> None:
        from agentos.adapters.autonomous_os import AutonomousOSAdapter

        adapter = AutonomousOSAdapter(state_dir=self.state_dir)
        result = adapter.register("agentos", "AgentOS", 60, 1, 4)
        self.assertTrue(result["ok"])
        demand_path = os.path.join(self.state_dir, "demand.json")
        self.assertTrue(os.path.exists(demand_path))

    def test_publish_demand(self) -> None:
        from agentos.adapters.autonomous_os import AutonomousOSAdapter

        adapter = AutonomousOSAdapter(state_dir=self.state_dir)
        adapter.publish_demand(queue_depth=2, active_workers=1, desired_model=1)
        demand_path = os.path.join(self.state_dir, "demand.json")
        with open(demand_path) as f:
            data = json.load(f)
        self.assertEqual(data["queue_depth"], 2)
        self.assertEqual(data["active_workers"], 1)
        self.assertEqual(data["desired_model"], 1)

    def test_read_allocation(self) -> None:
        from agentos.adapters.autonomous_os import AutonomousOSAdapter

        adapter = AutonomousOSAdapter(state_dir=self.state_dir)
        alloc_path = os.path.join(self.state_dir, "autonomousos-allocation.json")
        with open(alloc_path, "w") as f:
            json.dump({"max_workers": 2, "priority": 60}, f)
        result = adapter.read_allocation()
        self.assertEqual(result["max_workers"], 2)

    def test_read_allocation_missing(self) -> None:
        from agentos.adapters.autonomous_os import AutonomousOSAdapter

        adapter = AutonomousOSAdapter(state_dir=self.state_dir)
        result = adapter.read_allocation()
        self.assertEqual(result["max_workers"], 1)

    def test_get_system_status(self) -> None:
        from agentos.adapters.autonomous_os import AutonomousOSAdapter

        adapter = AutonomousOSAdapter(state_dir=self.state_dir)
        adapter.publish_demand(queue_depth=1, active_workers=0)
        status = adapter.get_system_status()
        self.assertEqual(status["system_id"], "agentos")
        self.assertIn("allocation", status)
        self.assertIn("demand", status)


if __name__ == "__main__":
    unittest.main()
