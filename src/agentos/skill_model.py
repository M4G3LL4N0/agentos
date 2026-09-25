"""Skill Model - compact structured procedures from repeated supervisor procedures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentos.models import SkillDefinition, utc_now_iso, new_id
from agentos.store import Store


SKILL_TEMPLATES = {
    "repo-audit": SkillDefinition(
        id="skill_repo_audit",
        objective="Audit repository for issues, patterns, and improvements",
        inputs={"repo_path": "str", "focus_areas": "list[str]"},
        constraints=["read-only access", "no external calls"],
        decision_rules=[
            "Run static analysis first",
            "Check for security patterns",
            "Identify code smells",
            "Summarize by severity",
        ],
        tools=["filesystem", "shell"],
        outputs={"report": "dict", "findings": "list[dict]", "score": "int"},
        verification="diff + test",
        approval_boundary="read-only",
        failure_handling={"retry": "once", "fallback": "manual review"},
        context_refs=["codebase", "standards"],
        version=1,
    ),
    "usage-refresh": SkillDefinition(
        id="skill_usage_refresh",
        objective="Refresh usage snapshots from local export files",
        inputs={"data_dir": "str", "providers": "list[str]"},
        constraints=["local files only", "no network calls"],
        decision_rules=[
            "Validate export file format",
            "Parse snapshot per provider",
            "Resolve usage against quotas",
            "Record to usage_ledger",
        ],
        tools=["filesystem", "json"],
        outputs={"snapshots_imported": "int", "entries_recorded": "int"},
        verification="ledger cross-check",
        approval_boundary="read-only",
        failure_handling={"retry": "once", "fallback": "alert"},
        context_refs=["quota_config", "provider_registry"],
        version=1,
    ),
    "ecosystem-delta": SkillDefinition(
        id="skill_ecosystem_delta",
        objective="Compute ecosystem candidate delta since last scan",
        inputs={"last_scan_ref": "str", "current_sources": "list[str]"},
        constraints=["incremental only", "no full re-scan"],
        decision_rules=[
            "Load baseline from last scan",
            "Fetch current source metadata",
            "Diff by candidate_id",
            "Classify changes: new/updated/removed",
        ],
        tools=["ecosystem_scout", "delta"],
        outputs={"changed": "list", "verified": "list", "candidates": "list"},
        verification="certify_static on new",
        approval_boundary="read-only",
        failure_handling={"retry": "once", "fallback": "full scan"},
        context_refs=["ecosystem_catalog", "last_scan"],
        version=1,
    ),
    "location-arrival": SkillDefinition(
        id="skill_location_arrival",
        objective="Handle arrival at a location - preferences, suggestions, logistics",
        inputs={"location": "str", "preferences": "dict", "time_window": "str"},
        constraints=["respect preferences", "no external booking"],
        decision_rules=[
            "Load user preferences for location type",
            "Check cached venue data",
            "Filter by dietary/accessibility constraints",
            "Rank by preference match score",
            "Present top 3 with rationale",
        ],
        tools=["location_intel", "preferences"],
        outputs={"suggestions": "list[dict]", "rationale": "str"},
        verification="user confirmation",
        approval_boundary="suggest only",
        failure_handling={"retry": "wider radius", "fallback": "generic"},
        context_refs=["user_preferences", "venue_cache", "location_history"],
        version=1,
    ),
    "tonight": SkillDefinition(
        id="skill_tonight",
        objective="Plan evening activities based on context and preferences",
        inputs={"current_location": "str", "energy_level": "str", "budget": "str"},
        constraints=["local options only", "respect energy level"],
        decision_rules=[
            "Check time and location",
            "Filter activities by energy compatibility",
            "Apply budget filter",
            "Check for conflicts with next day",
            "Present 2-3 options with tradeoffs",
        ],
        tools=["location_intel", "calendar", "preferences"],
        outputs={"plan": "dict", "alternatives": "list"},
        verification="user selection",
        approval_boundary="suggest only",
        failure_handling={"retry": "expand radius", "fallback": "default"},
        context_refs=["calendar", "preferences", "location_history"],
        version=1,
    ),
    "roadtrip": SkillDefinition(
        id="skill_roadtrip",
        objective="Plan multi-day road trip with stops, fuel, and activities",
        inputs={"origin": "str", "destination": "str", "days": "int", "preferences": "dict"},
        constraints=["realistic driving times", "fuel stops within range"],
        decision_rules=[
            "Compute route with waypoints",
            "Insert fuel stops at 70% range",
            "Add meal/activity stops by preference",
            "Check overnight options",
            "Validate total daily drive time < 8h",
        ],
        tools=["maps", "fuel", "location_intel"],
        outputs={"itinerary": "list[dict]", "fuel_stops": "list", "estimated_cost": "float"},
        verification="route validation",
        approval_boundary="plan only",
        failure_handling={"retry": "adjust stops", "fallback": "shorter days"},
        context_refs=["vehicle_range", "preferences", "traffic_patterns"],
        version=1,
    ),
    "fuel-stop-stack": SkillDefinition(
        id="skill_fuel_stop_stack",
        objective="Optimize fuel stop sequence along a route",
        inputs={"route": "list[dict]", "vehicle_range_km": "int", "fuel_price_tolerance": "float"},
        constraints=["stops within range", "minimize detour"],
        decision_rules=[
            "Segment route by vehicle range",
            "Find stations at segment boundaries",
            "Rank by price + detour distance",
            "Select Pareto-optimal stops",
        ],
        tools=["maps", "fuel_prices"],
        outputs={"stops": "list[dict]", "total_cost_estimate": "float"},
        verification="route feasibility",
        approval_boundary="suggest only",
        failure_handling={"retry": "extend range", "fallback": "any station"},
        context_refs=["vehicle_profile", "fuel_history"],
        version=1,
    ),
    "weather-departure": SkillDefinition(
        id="skill_weather_departure",
        objective="Determine optimal departure time based on weather",
        inputs={"route": "list[dict]", "departure_window": "str", "risk_tolerance": "str"},
        constraints=["safety first", "respect time window"],
        decision_rules=[
            "Fetch weather along route",
            "Identify hazardous segments",
            "Score departure times by risk",
            "Recommend earliest safe departure",
        ],
        tools=["weather", "maps"],
        outputs={"recommended_departure": "str", "risk_assessment": "dict"},
        verification="forecast confidence",
        approval_boundary="recommend only",
        failure_handling={"retry": "wider window", "fallback": "delay"},
        context_refs=["weather_provider", "route"],
        version=1,
    ),
    "sunset": SkillDefinition(
        id="skill_sunset",
        objective="Find optimal sunset viewing location",
        inputs={"location": "str", "date": "str", "preferences": "dict"},
        constraints=["accessible", "safe"],
        decision_rules=[
            "Compute sunset time for location/date",
            "Find elevated/west-facing spots",
            "Filter by accessibility and preferences",
            "Rank by view quality score",
        ],
        tools=["maps", "astronomy", "location_intel"],
        outputs={"location": "dict", "sunset_time": "str", "view_score": "float"},
        verification="user confirmation",
        approval_boundary="suggest only",
        failure_handling={"retry": "next day", "fallback": "generic"},
        context_refs=["elevation_data", "accessibility"],
        version=1,
    ),
    "weekly-city-plan": SkillDefinition(
        id="skill_weekly_city_plan",
        objective="Generate weekly plan for a city visit",
        inputs={"city": "str", "dates": "list[str]", "preferences": "dict", "budget": "str"},
        constraints=["respect energy levels", "group by geography"],
        decision_rules=[
            "Fetch city attractions and events",
            "Cluster by neighborhood/day",
            "Balance activity types",
            "Insert meal/reserve buffers",
            "Validate against budget and preferences",
        ],
        tools=["location_intel", "events", "preferences"],
        outputs={"daily_plans": "list[dict]", "reservations_needed": "list"},
        verification="user review",
        approval_boundary="plan only",
        failure_handling={"retry": "simplify", "fallback": "highlights only"},
        context_refs=["city_data", "preferences", "calendar"],
        version=1,
    ),
    "workforce-health": SkillDefinition(
        id="skill_workforce_health",
        objective="Audit workforce health - roles, autonomy, incidents, metrics",
        inputs={"role_registry": "dict", "time_window": "str"},
        constraints=["read-only", "no state changes"],
        decision_rules=[
            "Load all role autonomy records",
            "Check promotion readiness",
            "Identify stalled/suspended roles",
            "Summarize incident clusters",
            "Report metrics: utilization, health, cost",
        ],
        tools=["autonomy_model", "fleet_monitor", "metrics"],
        outputs={"report": "dict", "recommendations": "list", "alerts": "list"},
        verification="cross-reference with executor_metrics",
        approval_boundary="read-only",
        failure_handling={"retry": "partial", "fallback": "cached"},
        context_refs=["role_registry", "executor_metrics", "incidents"],
        version=1,
    ),
    "project-reality": SkillDefinition(
        id="skill_project_reality",
        objective="Audit project reality - state, capabilities, gaps, drift",
        inputs={"project_id": "str", "sources": "list[str]"},
        constraints=["read-only", "evidence-based"],
        decision_rules=[
            "Load declared project state",
            "Collect actual state from sources",
            "Diff: declared vs actual",
            "Identify drift: capabilities, config, data",
            "Classify gaps by severity",
        ],
        tools=["store", "ecosystem_scout", "reusable_workflows"],
        outputs={"reality_report": "dict", "gaps": "list[dict]", "drift_score": "float"},
        verification="source cross-check",
        approval_boundary="read-only",
        failure_handling={"retry": "partial", "fallback": "last known"},
        context_refs=["project_state", "capability_registry", "workflow_catalog"],
        version=1,
    ),
}


class SkillModel:
    """Manages skill definitions and execution."""

    def __init__(self, store: Store):
        self.store = store
        self._load_builtins()

    def _load_builtins(self) -> None:
        """Ensure built-in skills are in store."""
        for skill_id, skill in SKILL_TEMPLATES.items():
            existing = self.store.get_skill(skill.id)
            if not existing:
                self.store.save_skill(skill)

    def get_skill(self, skill_id: str) -> SkillDefinition | None:
        return self.store.get_skill(skill_id)

    def get_skill_by_objective(self, objective: str) -> SkillDefinition | None:
        skills = self.store.list_skills(objective=objective)
        return skills[0] if skills else None

    def list_skills(self) -> list[SkillDefinition]:
        return self.store.list_skills()

    def execute_skill(self, skill_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute a skill - returns structured result for verification."""
        skill = self.store.get_skill(skill_id)
        if not skill:
            return {"ok": False, "error": f"Skill not found: {skill_id}"}

        # In real system, this would execute the skill's decision rules
        # For now, return a structured result that can be verified
        return {
            "ok": True,
            "skill_id": skill_id,
            "objective": skill.objective,
            "inputs": inputs,
            "decision_rules_applied": skill.decision_rules,
            "tools_used": skill.tools,
            "outputs_schema": skill.outputs,
            "verification": skill.verification,
        }

    def create_skill(self, skill: SkillDefinition) -> None:
        self.store.save_skill(skill)

    def update_skill(self, skill_id: str, updates: dict[str, Any]) -> SkillDefinition | None:
        skill = self.store.get_skill(skill_id)
        if not skill:
            return None

        for key, value in updates.items():
            if hasattr(skill, key):
                setattr(skill, key, value)
        skill.version = skill.version + 1
        skill.updated_at = utc_now_iso()
        self.store.save_skill(skill)
        return skill

    def recommend_skill(self, task_class: str, context: dict[str, Any]) -> SkillDefinition | None:
        """Recommend a skill for a task class."""
        skills = self.list_skills()
        for skill in skills:
            if task_class in skill.objective.lower() or any(
                k in skill.objective.lower() for k in task_class.split("_")
            ):
                return skill
        return None