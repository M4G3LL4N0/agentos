"""Tests for the AI Mesh adapter — provider discovery and routing."""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch


class TestAIMeshAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.mesh_dir = os.path.join(self.temp_dir, "autonomousos", "mesh")
        os.makedirs(self.mesh_dir, exist_ok=True)

    def test_discover_providers_empty(self) -> None:
        from agentos.adapters.ai_mesh import AIMeshAdapter

        adapter = AIMeshAdapter(mesh_dir=self.mesh_dir)
        with patch("shutil.which", return_value=None):
            providers = adapter.discover_providers()
        self.assertIsInstance(providers, list)
        self.assertEqual(len(providers), 0)

    def test_detect_provider_states(self) -> None:
        from agentos.adapters.ai_mesh import AIMeshAdapter, ProviderState

        adapter = AIMeshAdapter(mesh_dir=self.mesh_dir)
        self.assertEqual(
            adapter._detect_state(True, True, True), ProviderState.READY
        )
        self.assertEqual(
            adapter._detect_state(True, True, False), ProviderState.AUTH_REQUIRED
        )
        self.assertEqual(
            adapter._detect_state(True, False, False), ProviderState.DETECTED
        )
        self.assertEqual(
            adapter._detect_state(False, False, False), ProviderState.UNAVAILABLE
        )

    def test_route_task_free_first(self) -> None:
        from agentos.adapters.ai_mesh import AIMeshAdapter, ProviderState

        adapter = AIMeshAdapter(mesh_dir=self.mesh_dir)
        with patch.object(adapter, "discover_providers") as mock_discover:
            mock_discover.return_value = [
                {
                    "id": "opencode",
                    "cost_rank": 1,
                    "state": ProviderState.READY,
                    "free": True,
                    "capabilities": ["coding", "general"],
                    "path": "/usr/bin/opencode",
                },
                {
                    "id": "claude",
                    "cost_rank": 4,
                    "state": ProviderState.READY,
                    "free": False,
                    "capabilities": ["coding", "reasoning"],
                    "path": "/usr/bin/claude",
                },
            ]
            result = adapter.route_task({"capability": "coding"})
            self.assertEqual(result["provider"], "opencode")

    def test_route_task_no_free(self) -> None:
        from agentos.adapters.ai_mesh import AIMeshAdapter, ProviderState

        adapter = AIMeshAdapter(mesh_dir=self.mesh_dir)
        with patch.object(adapter, "discover_providers") as mock_discover:
            mock_discover.return_value = [
                {
                    "id": "claude",
                    "cost_rank": 4,
                    "state": ProviderState.READY,
                    "free": False,
                    "capabilities": ["coding", "reasoning"],
                    "path": "/usr/bin/claude",
                },
            ]
            result = adapter.route_task({"capability": "coding"})
            self.assertEqual(result["provider"], "claude")

    def test_route_task_no_providers(self) -> None:
        from agentos.adapters.ai_mesh import AIMeshAdapter

        adapter = AIMeshAdapter(mesh_dir=self.mesh_dir)
        with patch.object(adapter, "discover_providers", return_value=[]):
            result = adapter.route_task({"capability": "coding"})
            self.assertFalse(result["ok"])

    def test_route_task_no_capable(self) -> None:
        from agentos.adapters.ai_mesh import AIMeshAdapter, ProviderState

        adapter = AIMeshAdapter(mesh_dir=self.mesh_dir)
        with patch.object(adapter, "discover_providers") as mock_discover:
            mock_discover.return_value = [
                {
                    "id": "opencode",
                    "cost_rank": 1,
                    "state": ProviderState.READY,
                    "free": True,
                    "capabilities": ["coding"],
                },
            ]
            result = adapter.route_task({"capability": "reasoning"})
            self.assertFalse(result["ok"])

    def test_get_provider_status(self) -> None:
        from agentos.adapters.ai_mesh import AIMeshAdapter

        adapter = AIMeshAdapter(mesh_dir=self.mesh_dir)
        with patch.object(adapter, "discover_providers") as mock_discover:
            mock_discover.return_value = [
                {"id": "opencode", "state": "READY"},
            ]
            status = adapter.get_provider_status("opencode")
            self.assertEqual(status["id"], "opencode")

    def test_get_provider_status_not_found(self) -> None:
        from agentos.adapters.ai_mesh import AIMeshAdapter

        adapter = AIMeshAdapter(mesh_dir=self.mesh_dir)
        with patch.object(adapter, "discover_providers", return_value=[]):
            status = adapter.get_provider_status("nonexistent")
            self.assertEqual(status["state"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
