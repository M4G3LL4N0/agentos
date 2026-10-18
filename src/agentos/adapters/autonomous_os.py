"""AutonomousOS adapter: register as a managed system.

AgentOS registers with the existing AutonomousOS control plane to receive
resource governance. Publishes demand, reads allocation, and respects
resource limits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class AutonomousOSAdapter:
    """Adapter to the existing AutonomousOS control plane."""

    def __init__(self, state_dir: str | None = None) -> None:
        self.state_dir = (
            Path(state_dir) if state_dir else Path("/srv/noaerth/agentos/state")
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def register(
        self,
        system_id: str,
        name: str,
        priority: int,
        minimum: int = 1,
        maximum: int = 4,
    ) -> dict[str, Any]:
        self.publish_demand(queue_depth=0, active_workers=0, desired_model=0)
        return {
            "ok": True,
            "system_id": system_id,
            "name": name,
            "priority": priority,
            "minimum": minimum,
            "maximum": maximum,
        }

    def publish_demand(
        self,
        queue_depth: int = 0,
        active_workers: int = 0,
        desired_model: int = 0,
        urgency: str = "NORMAL",
    ) -> dict[str, Any]:
        demand = {
            "system_id": "agentos",
            "queue_depth": queue_depth,
            "active_workers": active_workers,
            "desired_model": desired_model,
            "desired_build": 0,
            "desired_test": 0,
            "desired_browser": 0,
            "desired_io": 0,
            "urgency": urgency,
            "gpu_required": False,
            "render_backlog": 0,
        }
        demand_path = self.state_dir / "demand.json"
        demand_path.write_text(json.dumps(demand, indent=2))
        return {"ok": True, "path": str(demand_path)}

    def read_allocation(self) -> dict[str, Any]:
        alloc_path = self.state_dir / "autonomousos-allocation.json"
        if not alloc_path.exists():
            return {"max_workers": 1, "priority": 60, "ok": True}
        try:
            data = json.loads(alloc_path.read_text())
            data["ok"] = True
            return data
        except (json.JSONDecodeError, OSError):
            return {"max_workers": 1, "priority": 60, "ok": True}

    def get_system_status(self) -> dict[str, Any]:
        allocation = self.read_allocation()
        demand_path = self.state_dir / "demand.json"
        demand: dict[str, Any] = {}
        if demand_path.exists():
            try:
                demand = json.loads(demand_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {
            "system_id": "agentos",
            "allocation": allocation,
            "demand": demand,
        }
