# Durable Execution

Status: **IMPLEMENTED** (native backend + smoke + status). Temporal
**DEFERRED** with exact reason (below) — no service, no dependency.

`DurableExecutionBackend` (`src/agentos/durable.py`): start (persists a
durable job record), checkpoint (appends resumable state into the
stored envelope), resume (re-reads it — crash recovery is reopening
the DB and resuming). Default and only backend: AgentOS native
persistence. Used only when a job needs multi-hour/day execution,
external-event waiting, retries, resumability, crash recovery,
conditional transitions or scheduled continuation; simple commands
stay native and non-durable.

Temporal evaluation (`durable.temporal_status()` → `DEFERRED`):
stdlib-only build with no Temporal SDK dependency, no Temporal
service, and no explicit need for one. Temporal's agent integrations
can run model calls as Activities (so replay does not rerun completed
model calls) — acknowledged and respected — but experimental
integration packages are not pulled in for novelty. The backend
interface above is the seam an `OPTIONAL_BACKEND` adapter would
implement. No persistent Temporal service is started.

Smoke (`durable.durable_smoke`, also via `durable status` CLI):
start → 2 checkpoints → resume, asserting both survive, plus a
close/reopen restart round-trip in tests. Deterministic, zero cost.

Tests: `tests/test_fabric.py::DurableTests` (3). **VERIFIED**.
