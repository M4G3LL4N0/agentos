"""Provider-neutral bounded task-prompt builder.

Pure: no I/O, no subprocesses, no timestamps, no randomness, no
provider-specific logic. Consumed by all adapters to turn an
:class:`~agentos.models.Objective` (plus ``PlanStep`` / ``Capability`` /
strategy context) into a bounded, deterministic task prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentos.models import Capability, Objective, PlanStep, Strategy

INSTRUCTION = (
    "INSPECT FIRST, DON'T REBUILD WORKING SYSTEMS, ADD/EXTEND/CONNECT, "
    "TEST, RUNTIME-VERIFY, REPORT ACTUAL RESULTS, NO SUCCESS WITHOUT EVIDENCE"
)

#: Per-section cap; 8 sections x cap + fixed headers stay under 8000 chars.
SECTION_CAP = 800

TRUNCATION_MARKER = "[truncated]"


@dataclass
class ProjectContext:
    """Plain project data for the builder. No I/O in this module."""

    paths: list[str] = field(default_factory=list)
    git_root: str = ""
    baseline_docs: list[str] = field(default_factory=list)
    locked_dirs: list[str] = field(default_factory=list)
    current_state: str = ""


def _truncate(text: str, cap: int = SECTION_CAP) -> str:
    text = str(text or "")
    if len(text) <= cap:
        return text
    return text[:cap] + "\n" + TRUNCATION_MARKER


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return [str(v) for v in value]


def _strategy_text(objective: Objective, strategy: Any) -> str:
    raw = strategy if strategy is not None else getattr(objective, "strategy", None)
    if raw is None:
        return ""
    name = str(getattr(raw, "value", raw))
    try:
        return Strategy(name).value
    except ValueError:
        return name


def build_task(
    objective: Objective,
    context: ProjectContext,
    constraints: list[str] | str | None,
    expected_behavior: str,
    do_not: list[str] | str | None,
    *,
    steps: list[PlanStep] | None = None,
    capabilities: list[Capability] | None = None,
    strategy: Any = None,
) -> str:
    """Build a bounded, deterministic task prompt string."""
    merged_constraints = list(getattr(objective, "constraints", None) or []) + _as_list(
        constraints
    )
    preserve = list(getattr(context, "locked_dirs", None) or []) + _as_list(do_not)

    current_state = str(getattr(context, "current_state", "") or "")
    if not current_state:
        obj_ctx = getattr(objective, "context", None) or {}
        if isinstance(obj_ctx, dict):
            current_state = str(obj_ctx.get("current_state", "") or "")
            if not current_state and obj_ctx:
                current_state = "; ".join(
                    f"{k}={v}" for k, v in sorted(obj_ctx.items())
                )
    step_lines = [
        f"{s.id}: {s.kind}/{s.operation}"
        + (f" via {s.capability_id}" if s.capability_id else "")
        for s in (steps or [])
    ]
    if step_lines:
        current_state = (current_state + "\n" if current_state else "") + "\n".join(
            step_lines
        )
    cap_lines = [f"{c.id}: {c.name} ({c.type.value})" for c in (capabilities or [])]
    if cap_lines:
        current_state = (current_state + "\n" if current_state else "") + "\n".join(
            cap_lines
        )

    project_body = (
        f"root: {context.git_root}\n"
        f"paths: {'; '.join(_as_list(context.paths)) or '(none)'}\n"
        f"baselines: {'; '.join(_as_list(context.baseline_docs)) or '(none)'}"
    )
    objective_body = (
        f"title: {objective.title}\n"
        f"description: {objective.description}\n"
        f"strategy: {_strategy_text(objective, strategy) or '(none)'}"
    )

    sections = [
        ("PROJECT", project_body),
        ("OBJECTIVE", objective_body),
        ("CURRENT STATE", current_state or "(unknown — inspect first)"),
        (
            "CONSTRAINTS",
            "\n".join(f"- {c}" for c in merged_constraints) or "(none)",
        ),
        (
            "FILES & SYSTEMS TO PRESERVE",
            "\n".join(f"- {p}" for p in preserve) or "(none)",
        ),
        ("EXPECTED BEHAVIOR", str(expected_behavior or "")),
        (
            "VERIFICATION",
            "Run the relevant test suite and report pass/fail with exact "
            "commands and output. Runtime-verify behavior; do not claim "
            "success from reading code alone.",
        ),
        (
            "DEFINITION OF DONE",
            "Done only with passing tests plus runtime evidence. "
            "NO SUCCESS WITHOUT EVIDENCE.",
        ),
    ]
    lines = [INSTRUCTION, ""]
    for header, body in sections:
        lines.append(f"== {header} ==")
        lines.append(_truncate(body))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
