"""Tests for the Hive adapter — integration with existing NOAERTH scheduler."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch


class TestHiveAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.hive_dir = os.path.join(self.temp_dir, "autonomousos", "mesh", "hive")
        os.makedirs(self.hive_dir, exist_ok=True)
        self.state_dir = os.path.join(self.temp_dir, "autonomousos", "state")
        os.makedirs(self.state_dir, exist_ok=True)
        self.resource_json = os.path.join(self.state_dir, "resource.json")
        with open(self.resource_json, "w") as f:
            json.dump(
                {
                    "allow_new_work": True,
                    "mode": "HEALTHY",
                    "disk_pct": 50,
                    "systems": [
                        {
                            "system_id": "friendsos",
                            "active_workers": 4,
                            "priority": 50,
                        }
                    ],
                },
                f,
            )

    def test_discover_workers_empty(self) -> None:
        from agentos.adapters.hive import HiveAdapter

        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=self.state_dir)
        workers = adapter.discover_workers()
        self.assertIsInstance(workers, list)

    def test_discover_workers_finds_active(self) -> None:
        from agentos.adapters.hive import HiveAdapter

        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=self.state_dir)
        workers = adapter.discover_workers()
        self.assertTrue(any(w["system_id"] == "friendsos" for w in workers))

    def test_submit_task_requires_admission(self) -> None:
        from agentos.adapters.hive import HiveAdapter

        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=self.state_dir)
        with patch.object(adapter, "_check_resource_admission") as mock_admission:
            mock_admission.return_value = (False, "disk full")
            result = adapter.submit_task(
                {"id": "t1", "title": "test", "prompt": "do something"}
            )
            self.assertFalse(result["ok"])
            self.assertIn("disk full", result["error"])

    def test_submit_task_success(self) -> None:
        from agentos.adapters.hive import HiveAdapter

        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=self.state_dir)
        with patch.object(adapter, "_check_resource_admission") as mock_admission:
            mock_admission.return_value = (True, "ok")
            with patch.object(adapter, "_submit_to_studio") as mock_submit:
                mock_submit.return_value = {"task_id": "task_123"}
                result = adapter.submit_task(
                    {"id": "t1", "title": "test", "prompt": "do something"}
                )
                self.assertTrue(result["ok"])
                self.assertEqual(result["task_id"], "task_123")

    def test_get_task_status_not_found(self) -> None:
        from agentos.adapters.hive import HiveAdapter

        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=self.state_dir)
        status = adapter.get_task_status("nonexistent")
        self.assertIsNone(status)

    def test_inspect_resource_usage(self) -> None:
        from agentos.adapters.hive import HiveAdapter

        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=self.state_dir)
        usage = adapter.inspect_resource_usage()
        self.assertTrue(usage["ok"])
        self.assertEqual(usage["mode"], "HEALTHY")

    def test_inspect_resource_usage_missing(self) -> None:
        from agentos.adapters.hive import HiveAdapter

        empty_dir = os.path.join(self.temp_dir, "empty")
        os.makedirs(empty_dir, exist_ok=True)
        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=empty_dir)
        usage = adapter.inspect_resource_usage()
        self.assertFalse(usage["ok"])

    def test_cancel_task(self) -> None:
        from agentos.adapters.hive import HiveAdapter

        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=self.state_dir)
        with patch.object(adapter, "_update_task_status") as mock_update:
            mock_update.return_value = {"ok": True}
            result = adapter.cancel_task("task_123")
            self.assertTrue(result["ok"])

    def test_collect_artifacts_empty(self) -> None:
        from agentos.adapters.hive import HiveAdapter

        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=self.state_dir)
        artifacts = adapter.collect_artifacts("task_123")
        self.assertIsInstance(artifacts, list)

    def test_collect_verification(self) -> None:
        from agentos.adapters.hive import HiveAdapter

        adapter = HiveAdapter(hive_dir=self.hive_dir, state_dir=self.state_dir)
        verification = adapter.collect_verification("task_123")
        self.assertIn("task_id", verification)
        self.assertIn("verified", verification)


if __name__ == "__main__":
    unittest.main()
