# Supervisor Operating Model

Elastic worker-fabric supervision, documented + minimally implemented in a
provider-neutral control plane (`supervisor.py`). Additive; part of the
Resource Governor mission (v0.4.1).

## Status

- **IMPLEMENTED + VERIFIED**: canonical operating chain, honest chain-layer
  status reporting, per-cell four-way separation, and the GrokBot-scope
  exclusion are real code exercised by tests and CLI.
- **DEFERRED / UNCONFIGURED, stated honestly**: no live supervisor loop, no
  ChiefOfStaff, no domain supervisor role, no model synthesis; they are
  reported as such, never claimed live.

## Canonical chain (documented + reported)

```
USER -> PAIOS -> AgentOS -> ChiefOfStaff -> domain supervisor ->
elastic worker fabric -> verifier -> supervisor -> ChiefOfStaff -> user
```

- USER: human principal; sole source of goals and cost authorization.
- PAIOS: venture portfolio plane; high-level mandate.
- AgentOS: provider-neutral control plane executing this model.
- ChiefOfStaff: cross-domain coordinator (a role, not a model).
- domain supervisor: plans/supervises one domain.
- elastic worker fabric: mix of runtimes (local tools, shell, echo, a2a
  peers, … alongside Grok).
- verifier: separated when meaningful; deterministic evidence never wastes a
  model call.
- supervisor: reconciles verifier output with worker claims.

## GrokBot scope (explicit)

GrokBot (external system agent) is valuable for **persistent context,
queues, browser ownership, supervision, monitoring, escalation, and final
synthesis**. It does **not** perform every worker task: the elastic worker
fabric is provider-neutral and includes other runtimes. A Grok identity
never implies a Grok model or a Grok computer (four-way separation).

## Honesty contract

- `chain_layer_status` uses ACTIVE / IMPLEMENTED / UNCONFIGURED / DEFERRED
  exactly per evidence.
- `supervisor_status().verified_live` is `False` until a live loop exists.
- The model itself is IMPLEMENTED (it is real code); the *operating layers*
  are mostly UNCONFIGURED/DEFERRED until configured.

## CLI

`agentos supervisor status` prints the chain, per-layer status, and per-cell
separation. `--json` returns the full structure.