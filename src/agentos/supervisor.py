"""Supervisor operating model (documented + minimally implemented, honest).

Canonical chain (additive, nothing fabricated):

    USER -> PAIOS -> AgentOS -> ChiefOfStaff -> domain supervisor ->
    elastic worker fabric -> verifier -> supervisor -> ChiefOfStaff -> user

GrokBot (external) is valuable for persistent context, queues, browser
ownership, supervision, monitoring, escalation and final synthesis — but the
model makes explicit that GrokBot does NOT perform every worker task: the
elastic worker fabric includes other runtimes. Nothing here pretends a
live supervisor, ChiefOfStaff, or verified pipeline exists where it does not.
"""

from __future__ import annotations

from typing import Any

from agentos.resource_governor import separation_profile

OPERATING_CHAIN = [
    "USER",
    "PAIOS",
    "AgentOS",
    "ChiefOfStaff",
    "domain supervisor",
    "elastic worker fabric",
    "verifier",
    "supervisor",
    "ChiefOfStaff",
    "user",
]

CHAIN_DETAILS: dict[str, str] = {
    "USER": "human principal; sole source of goals and cost authorization",
    "PAIOS": "venture portfolio plane; high-level mandate, ports context in",
    "AgentOS": "provider-neutral control plane executing this model",
    "ChiefOfStaff": "cross-domain coordinator (role, not a model)",
    "domain supervisor": "domain owner/planner; supervises one domain",
    "elastic worker fabric": "mix of runtimes (local tools, shell, grok, opencode, peers, ...)",
    "verifier": "separation when meaningful; deterministic evidence never wastes a model call",
    "supervisor": "reconciles verifier output with worker claims",
    "ChiefOfStaff": "synthesizes and reports back up",
    "user": "final authority",
}

#: The external GrokBot surface is NOT the whole worker fabric.
GROKBOT_SCOPE = (
    "GrokBot (external system agent) is valuable for persistent context, "
    "queues, browser ownership, supervision, monitoring, escalation and "
    "final synthesis. It does NOT perform every worker task."
)
GROKBOT_EXCLUSION = (
    "The elastic worker fabric is provider-neutral: it includes other "
    "runtimes (local tools, shell, filesystem, echo, a2a peers, etc.) "
    "alongside Grok. A Grok identity never implies a Grok model or a Grok "
    "computer (four-way separation)."
)


def operating_model() -> dict[str, Any]:
    """Summary of the operating model itself (status: IMPLEMENTED)."""
    return {
        "name": "supervisor-operating-model",
        "status": "IMPLEMENTED",
        "chain": list(OPERATING_CHAIN),
        "chain_detail": {step: CHAIN_DETAILS[step] for step in OPERATING_CHAIN},
        "grokbot_scope": GROKBOT_SCOPE,
        "grokbot_exclusion": GROKBOT_EXCLUSION,
        "honesty": (
            "status words below are IMPLEMENTED/UNCONFIGURED/UNKNOWN: nothing "
            "is claimed live or verified without evidence"
        ),
    }


def supervisor_status(agentos: Any) -> dict[str, Any]:
    """Honest status of the supervisor model in one running AgentOS.

    Reports the documented model, how many cells carry explicit supervisor/
    worker/verification policy, per-cell four-way separation, and which
    chain layers are actually configured vs DEFERRED/UNCONFIGURED.
    """
    cells = list(agentos._federation_cells())
    model = operating_model()
    supervised = [
        c.id
        for c in cells
        if getattr(c, "supervisor", None) or (c.config or {}).get("supervisor")
    ]
    with_worker_policy = [
        c.id
        for c in cells
        if getattr(c, "workerPolicy", None) or (c.config or {}).get("workerPolicy")
    ]
    with_verification_policy = [
        c.id
        for c in cells
        if getattr(c, "verificationPolicy", None)
        or (c.config or {}).get("verificationPolicy")
    ]
    grok = [c for c in cells if c.provider == "grokbot-office" or "grok" in c.id.lower()]
    separation = [separation_profile(c) for c in cells]
    distinct_providers = sorted({c.provider for c in cells})
    runtime_count = len(distinct_providers)
    return {
        "model": model,
        "agentos_role": "AgentOS executes the chain; it is control plane, not a substitute layer",
        "chain_layer_status": {
            "USER": "ACTIVE (human principal)",
            "PAIOS": "UNCONFIGURED (no PA/OS live link configured)",
            "AgentOS": "IMPLEMENTED (this process)",
            "ChiefOfStaff": "DEFERRED (no ChiefOfStaff role configured)",
            "domain supervisor": (
                "UNCONFIGURED" if not supervised else f"CONFIGURED on {len(supervised)} cells"
            ),
            "elastic worker fabric": f"IMPLEMENTED ({runtime_count} runtimes: {', '.join(distinct_providers) or 'none'})",
            "verifier": (
                "UNCONFIGURED"
                if not with_verification_policy
                else f"POLICY on {len(with_verification_policy)} cells"
            ),
            "supervisor": "UNCONFIGURED (no live supervisor loop)",
            "ChiefOfStaff (recv)": "UNCONFIGURED",
            "user": "ACTIVE (final authority)",
        },
        "cells": [
            {
                "id": c.id,
                "provider": c.provider,
                "health": c.health,
                "separation": separation_profile(c),
            }
            for c in cells
        ],
        "supervised_cells": supervised,
        "worker_policy_cells": with_worker_policy,
        "verifier_policy_cells": with_verification_policy,
        "grokbot_present": bool(grok),
        "grokbot_role": GROKBOT_SCOPE,
        "grokbot_exclusion": GROKBOT_EXCLUSION,
        "verified_live": False,
        "honesty_note": (
            "no live supervisor loop, ChiefOfStaff, or model synthesis is "
            "claimed; these layers are DEFERRED/UNCONFIGURED until configured"
        ),
    }