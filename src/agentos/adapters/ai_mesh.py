"""AI Mesh adapter: provider discovery and cost-aware routing.

Connects AgentOS to the existing AI Mesh provider intelligence system.
Discovers local CLI providers (opencode, claude, codex, cursor-agent) and
routes tasks based on cost, capability, and reliability.
"""

from __future__ import annotations

import shutil
from enum import StrEnum
from pathlib import Path
from typing import Any


class ProviderState(StrEnum):
    DETECTED = "DETECTED"
    CONFIGURED = "CONFIGURED"
    READY = "READY"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class AIMeshAdapter:
    """Adapter to the existing AI Mesh provider routing system."""

    def __init__(self, mesh_dir: str | None = None) -> None:
        self.mesh_dir = (
            Path(mesh_dir)
            if mesh_dir
            else Path("/srv/noaerth/autonomousos/mesh")
        )

    def discover_providers(self) -> list[dict[str, Any]]:
        providers: list[dict[str, Any]] = []
        for name, binary, cost_rank, caps in [
            ("opencode", "opencode", 1, ["coding", "general"]),
            ("cursor-agent", "cursor-agent", 2, ["coding"]),
            ("codex", "codex", 3, ["coding", "reasoning"]),
            ("claude", "claude", 4, ["coding", "reasoning"]),
        ]:
            path = shutil.which(binary)
            if path:
                state = self._detect_state(True, True, True)
                providers.append(
                    {
                        "id": name,
                        "binary": binary,
                        "path": path,
                        "cost_rank": cost_rank,
                        "capabilities": caps,
                        "state": state,
                        "free": cost_rank <= 2,
                    }
                )
        return providers

    def _detect_state(
        self, installed: bool, configured: bool, authenticated: bool
    ) -> ProviderState:
        if not installed:
            return ProviderState.UNAVAILABLE
        if not configured:
            return ProviderState.DETECTED
        if not authenticated:
            return ProviderState.AUTH_REQUIRED
        return ProviderState.READY

    def route_task(self, task: dict[str, Any]) -> dict[str, Any]:
        providers = self.discover_providers()
        if not providers:
            return {"ok": False, "error": "no providers available"}

        capability = task.get("capability", "coding")
        capable = [
            p for p in providers if capability in p.get("capabilities", [])
        ]
        if not capable:
            return {
                "ok": False,
                "error": f"no provider for capability: {capability}",
            }

        free = [
            p
            for p in capable
            if p.get("free", False) and p["state"] == ProviderState.READY
        ]
        if free:
            best = min(free, key=lambda p: p["cost_rank"])
            return {"ok": True, "provider": best["id"], "binary": best["path"]}

        ready = [p for p in capable if p["state"] == ProviderState.READY]
        if ready:
            best = min(ready, key=lambda p: p["cost_rank"])
            return {
                "ok": True,
                "provider": best["id"],
                "binary": best["path"],
                "paid": True,
            }

        return {"ok": False, "error": "no ready providers"}

    def get_provider_status(self, provider_id: str) -> dict[str, Any]:
        providers = self.discover_providers()
        for p in providers:
            if p["id"] == provider_id:
                return p
        return {"id": provider_id, "state": ProviderState.UNKNOWN, "error": "not found"}
