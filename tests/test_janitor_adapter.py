"""Tests for the JanitorOS adapter — storage admission and protected paths."""

from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch


class TestJanitorOSAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()

    def test_request_admission_success(self) -> None:
        from agentos.adapters.janitor import JanitorOSAdapter

        adapter = JanitorOSAdapter()
        with patch.object(adapter, "_run_janitor") as mock_run:
            mock_run.return_value = {"ok": True, "admitted": True}
            result = adapter.request_admission(1024 * 1024 * 100)
            self.assertTrue(result["ok"])

    def test_request_admission_denied(self) -> None:
        from agentos.adapters.janitor import JanitorOSAdapter

        adapter = JanitorOSAdapter()
        with patch.object(adapter, "_run_janitor") as mock_run:
            mock_run.return_value = {
                "ok": True,
                "admitted": False,
                "reason": "disk full",
            }
            result = adapter.request_admission(1024 * 1024 * 100)
            self.assertFalse(result["ok"])
            self.assertIn("disk full", result["error"])

    def test_check_protected_path_git(self) -> None:
        from agentos.adapters.janitor import JanitorOSAdapter

        adapter = JanitorOSAdapter()
        self.assertTrue(adapter.check_protected_path("/repo/.git/config"))
        self.assertTrue(adapter.check_protected_path("/repo/.git/HEAD"))

    def test_check_protected_path_env(self) -> None:
        from agentos.adapters.janitor import JanitorOSAdapter

        adapter = JanitorOSAdapter()
        self.assertTrue(adapter.check_protected_path("/repo/.env"))
        self.assertTrue(adapter.check_protected_path("/repo/config/.env.local"))

    def test_check_protected_path_safe(self) -> None:
        from agentos.adapters.janitor import JanitorOSAdapter

        adapter = JanitorOSAdapter()
        self.assertFalse(adapter.check_protected_path("/tmp/worktree/src/main.py"))
        self.assertFalse(adapter.check_protected_path("/srv/noaerth/agentos/state"))

    def test_get_storage_forecast(self) -> None:
        from agentos.adapters.janitor import JanitorOSAdapter

        adapter = JanitorOSAdapter()
        forecast = adapter.get_storage_forecast()
        self.assertIn("total_bytes", forecast)
        self.assertIn("used_bytes", forecast)
        self.assertIn("available_bytes", forecast)

    def test_no_binary_returns_error(self) -> None:
        from agentos.adapters.janitor import JanitorOSAdapter

        adapter = JanitorOSAdapter(janitor_binary="/nonexistent/janitor")
        result = adapter.request_admission(1024)
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"])


if __name__ == "__main__":
    unittest.main()
