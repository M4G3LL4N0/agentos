"""Objective decomposition: turn one opaque objective into a dependency-aware
graph of smaller, independently verifiable child objectives.

This module is *pure*: no store, no adapter, no I/O. It answers, given an
objective and the capabilities AgentOS can actually drive:

- What children should exist?
- What must happen before what (``depends_on``)?
- What can run at the same time (``parallelizable``)?
- In what order should the whole graph run (topological, then merged into
  parallel batches)?
- Which children can we NOT honestly do because no usable capability exists
  (a capability gap, never a silent skip)?

Everything here follows deterministic rules so the same inputs always produce
the same graph — reproducibility, not heuristics with hidden state.

The engine consumes this module: it maps ``DecomposedChild`` into child
objectives (persisted as real objectives with ``parent_id``), and runs the
batches through the normal adapter->execute->verify discipline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentos.models import (
    HealthStatus,
    Objective,
    ObjectiveState,
    new_id,
    utc_now_iso,
)

# Work-phase classification for choosing an adaptive shape. These are signal
# words, not free-text parsing: deterministic and unit-testable.
_BUILD_WORDS = (
    "implement",
    "build",
    "refactor",
    "fix the",
    "add support for",
    "create the",
    "write the code",
    "feature",
    "change the",
)
_VERIFY_WORDS = (
    "verify",
    "test",
    "make sure the",
    "ensure the",
    "run the tests",
    "check that",
)
_RESEARCH_WORDS = (
    "research",
    "analyze",
    "investigate",
    "compare",
    "understand the",
    "assess",
    "review the",
    "explore",
)


@dataclass
class DecomposedChild:
    """A single child of a decomposition.

    Matches the persistence shape so the engine can turn it into a real
    child objective verbatim.
    """

    id: str
    title: str
    operation: str
    capability_id: str | None = None
    depends_on: list[str] = field(default_factory=list)
    parallelizable: bool = True
    params: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""


@dataclass
class Decomposition:
    """A decomposed objective: children plus a runnable batch order."""

    id: str
    objective_id: str
    children: list[DecomposedChild]
    order: list[list[str]]  # batches of child ids; each batch runs in parallel
    gaps: list[dict[str, Any]]  # children with no usable capability
    rationale: str = ""

    @classmethod
    def empty(cls, objective: Objective, rationale: str = "") -> "Decomposition":
        """A trivially-runnable decomposition with no fragmentation."""
        return cls(
            id=new_id("decomp"),
            objective_id=objective.id,
            children=[],
            order=[],
            gaps=[],
            rationale=rationale or "no decomposition: objective is already atomic",
        )

    @property
    def parallelizable_count(self) -> int:
        return sum(1 for c in self.children if c.parallelizable and not c.depends_on)


# ----------------------------------------------------------------------
# pure graph helpers (independent of objective/capability details)
# ----------------------------------------------------------------------
def topo_batches(
    children: list[DecomposedChild],
) -> tuple[list[list[str]], list[str]]:
    """Topologically order children into parallel batches.

    Returns ``(batches, errors)`` where each ``batch`` is a list of child ids
    that can run concurrently (no intra-batch dependency), and ``errors``
    lists ids involved in a dependency cycle (never silently dropped).
    """
    by_id = {c.id: c for c in children}
    remaining = {c.id for c in children}
    batches: list[list[str]] = []
    cycle_ids: list[str] = []
    while remaining:
        ready = sorted(
            c.id
            for c in children
            if c.id in remaining and not set(c.depends_on) & remaining
        )
        if not ready:
            # cycle or dangling reference: surface it, never hide it.
            cycle_ids.extend(c.id for c in children if c.id in remaining)
            remaining = set()
            break
        batches.append(ready)
        remaining -= set(ready)
    return batches, cycle_ids


def _gap(
    child: DecomposedChild,
    capability_ids: set[str],
    usable_ids: set[str],
    reason: str,
) -> dict[str, Any]:
    return {
        "child_id": child.id,
        "operation": child.operation,
        "requires_capability": child.capability_id,
        "known_but_unusable": bool(
            child.capability_id
            and child.capability_id in capability_ids
            and child.capability_id not in usable_ids
        ),
        "reason": reason,
    }


def _uses_token_only(
    child: DecomposedChild, usable_ids: set[str]
) -> bool:
    return child.capability_id is not None and child.capability_id not in usable_ids


# ----------------------------------------------------------------------
# decomposition strategies
# ----------------------------------------------------------------------
def _build_children(
    objective: Objective,
    capabilities_by_id: dict[str, Any],
    operation: str,
) -> list[DecomposedChild]:
    """Children for build/verify work: plan -> implement -> verify.

    ``verify`` depends on ``implement`` (you can only verify what exists);
    everything else is independent.
    """
    children = [
        DecomposedChild(
            id=new_id("child"),
            title="Plan the change",
            operation=operation,
            capability_id=_pick(capabilities_by_id, "router.plan"),
            parallelizable=True,
            rationale="enumerate approach before touching anything",
        )
    ]
    implement = DecomposedChild(
        id=new_id("child"),
        title=f"Implement {objective.title}",
        operation=operation,
        capability_id=_pick(capabilities_by_id, "opencode.run", "echo"),
        depends_on=[children[0].id],
        parallelizable=False,
        params=dict(objective.context.get("params", {}) or {}),
        rationale="the actual change is the delivered artifact",
    )
    verify = DecomposedChild(
        id=new_id("child"),
        title=f"Verify {objective.title}",
        operation="project.verify",
        capability_id=_pick(capabilities_by_id, "project.verify", "shell.run"),
        depends_on=[implement.id],
        parallelizable=False,
        rationale="independent verification that the deliverable exists and works",
    )
    children += [implement, verify]
    return children


def _research_children(objective: Objective, capabilities_by_id: dict[str, Any]) -> list[DecomposedChild]:
    research = DecomposedChild(
        id=new_id("child"),
        title=f"Research: {objective.title}",
        operation="research",
        capability_id=_pick(capabilities_by_id, "grok.run", "xai.chat", "filesystem"),
        parallelizable=True,
        params=dict(objective.context.get("params", {}) or {}),
        rationale="gather context before committing to an approach",
    )
    return [research]


def _default_children(objective: Objective) -> list[DecomposedChild]:
    return [
        DecomposedChild(
            id=new_id("child"),
            title=objective.title,
            operation="echo",
            capability_id=None,
            parallelizable=True,
            rationale="opaque objective: single independently-run child",
        )
    ]


def _pick(
    capabilities_by_id: dict[str, Any], *preferred: str, default: str | None = None
) -> str | None:
    """First preferred id that exists and is usable, else first usable, else None."""
    usable = [c for c in capabilities_by_id.values() if _usable(c)] or [
        c for c in capabilities_by_id.values()
    ]
    for p in preferred:
        for c in usable:
            if c.id == p:
                return c.id
    return usable[0].id if usable else default


def _usable(cap: Any) -> bool:
    health = getattr(cap, "health", None)
    availability = getattr(cap, "availability", True)
    return bool(availability) and health in (HealthStatus.AVAILABLE, HealthStatus.OK)


# ----------------------------------------------------------------------
# main entry point
# ----------------------------------------------------------------------
def decompose_objective(
    objective: Objective,
    capabilities_by_id: dict[str, Any],
    *,
    force_graph: bool = False,
    bound: int = 4,
) -> Decomposition:
    """Decompose ``objective`` into a runnable child graph.

    Adaptive: the shape follows the work phase detected in the objective,
    not a single template. Returns a ``Decomposition`` whose ``gaps`` list is
    non-empty whenever a child cannot be honestly executed — the caller must
    surface those gaps, never fake the work.
    """
    usable_ids = {c.id for c in capabilities_by_id.values() if _usable(c)}
    known_ids = set(capabilities_by_id)

    text = " ".join(
        p for p in (objective.title, objective.description) if isinstance(p, str)
    ).lower()

    if force_graph or _any_of(text, "build", "implement", "feature", "fix the"):
        children = _build_children(objective, capabilities_by_id, _op_for(objective))
    elif _any_of(text, "research", "investigate", "analyze"):
        children = _research_children(objective, capabilities_by_id)
    else:
        children = _default_children(objective)

    children = children[:bound]

    batches, cycle_ids = topo_batches(children)
    gaps = []

    for child in children:
        if not child.capability_id:
            # opaque: record the capability requirement honestly.
            gaps.append(
                _gap(child, known_ids, usable_ids, "no capability matched; opaque objective")
            )
            continue
        if child.capability_id not in known_ids:
            gaps.append(
                _gap(child, known_ids, usable_ids, "capability does not exist in this runtime")
            )
        elif child.capability_id not in usable_ids:
            gaps.append(
                _gap(child, known_ids, usable_ids, "capability present but not usable right now")
            )

    if cycle_ids:
        gaps.append(
            {
                "child_ids": cycle_ids,
                "reason": "dependency cycle detected; decomposition cannot run",
            }
        )

    rationale = (
        f"phase={_phase_of(objective)} strategy=DECOMPOSE "
        f"children={len(children)} batches={len(batches)} "
        f"gaps={len(gaps)}"
    )
    return Decomposition(
        id=new_id("decomp"),
        objective_id=objective.id,
        children=children,
        order=batches,
        gaps=gaps,
        rationale=rationale,
    )


def _op_for(objective: Objective) -> str:
    return str(objective.context.get("operation") or "implement")


def _phase_of(objective: Objective) -> str:
    if objective.strategy:
        return str(objective.strategy)
    return "opaque"


def _any_of(text: str, *words: str) -> bool:
    return any(w in text for w in words)


# ----------------------------------------------------------------------
# dependency gating + persistence (consumed by engine/services/later tasks)
# ----------------------------------------------------------------------
def objective_is_unblocked(
    child: Objective, completed_ids: set[str]
) -> tuple[bool, str | None]:
    deps = child.parents_data.get("depends_on") or []
    blocked = [d for d in deps if d not in completed_ids]
    if blocked:
        return False, f"blocked by incomplete dependencies: {blocked}"
    return True, None


def create_child_objectives(
    decomposition: Decomposition, parent_id: str, store: Any
) -> list[Objective]:
    """Persist each decomposed child as a real child objective.

    ``depends_on`` entries reference ``DecomposedChild`` ids; they are
    translated to the persisted ``Objective`` ids so
    :func:`objective_is_unblocked` works directly on stored objectives.
    Unknown ``depends_on`` ids raise ``AgentOSError`` (fail loud on
    dangling dependencies — never silently rewire the graph).
    The parent records the completed-child bookkeeping (child id list).
    """
    # Lazy import: agentos.engine imports this module, so importing it at
    # module top would be circular (same pattern as safety.py).
    from agentos.engine import AgentOSError

    key_to_objective_id: dict[str, str] = {}
    for child in decomposition.children:
        key_to_objective_id[child.id] = new_id("obj")

    for child in decomposition.children:
        unknown = [
            d for d in (child.depends_on or []) if d not in key_to_objective_id
        ]
        if unknown:
            raise AgentOSError(
                f"unknown depends_on ids {unknown} on child {child.id!r}: "
                "dangling dependencies are never silently dropped"
            )

    children: list[Objective] = []
    for child in decomposition.children:
        objective = Objective(
            id=key_to_objective_id[child.id],
            title=child.title,
            description=child.rationale or child.title,
            status=ObjectiveState.READY,
            context={
                "operation": child.operation,
                "params": dict(child.params or {}),
            },
            parent_id=parent_id,
            parents_data={
                "depends_on": [
                    key_to_objective_id[d] for d in (child.depends_on or [])
                ],
                "child_key": child.id,
                "decomposition_id": decomposition.id,
                "parallelizable": bool(child.parallelizable),
                "capability_id": child.capability_id,
            },
        )
        store.save_objective(objective)
        children.append(objective)

    parent = store.get_objective(parent_id)
    if parent is not None:
        context = dict(parent.context or {})
        recorded = list(context.get("children") or [])
        for objective in children:
            if objective.id not in recorded:
                recorded.append(objective.id)
        context["children"] = recorded
        context["decomposition_id"] = decomposition.id
        parent.context = context
        parent.mark_updated()
        store.save_objective(parent)
    return children
