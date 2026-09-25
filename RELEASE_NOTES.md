# AgentOS 0.5.0 Release Notes

BASELINE_TESTS: 639 total; 626 passed; 2 failures; 11 errors; 0 skips.

ROOT_CAUSES: missing engine lifecycle wiring; incomplete model/store round-trips; verifier selection and autonomy transition defects; nonexistent routine timestamp; stale workflow persistence/matching; service/CLI signature drift; unsafe restore identifier/archive handling; retry telemetry counting failed attempts as recoveries; test HTTP servers missing `server_close()`; incomplete secret redaction in raw learning records; backup archives not rejecting unlisted members; backup dry-run not checking target schema; malformed persisted JSON silently reverting to defaults.

FIXES: integrated existing intelligence, workflow, verification, coach, routing, fleet, routine, and backup components into the real engine/service/CLI paths; completed JSON persistence; enforced hard routing gates; bounded retry/alternate/supervisor/human paths; added safe backup validation and rollback; corrected measured metrics; added deterministic benchmark and integration coverage; hardened recursive and raw-field secret redaction; made persisted JSON failures explicit; rejected extra backup members; removed source paths from manifests; added target schema compatibility to backup dry-run; made learned-routing decision persistence real.

FINAL_TEST_COUNT: 710 passed of 710.

FAILURES: 0.

ERRORS: 0.

SKIPS: 0.

ENGINE_PATH: `objective → trace → exact cache → deterministic/state fast path → eligible workflow or fresh plan → capability execution → verification → cache/coach/workflow/metrics learning → bounded terminal outcome`.

ZERO_AGENT_FAST_PATH: exact cache hits, safe arithmetic, and state/database lookup complete with 0 workers, 0 model calls, 0 premium calls, and 0 Grok calls.

WORKFLOW_FAST_PATH: observed/verified/certified workflows with satisfied capability, input, and approval preconditions rebuild a bounded execution plan and record reuse/maturity evidence.

VERIFICATION: actual outcomes select accept, retry, alternate worker, supervisor, or human escalation; attempts remain explicitly bounded; malformed persisted verification state fails closed.

COACH: routine success and deterministic/cache paths skip coaching; meaningful success, retries, and failure create measured lesson candidates.

LEARNING: lesson candidates, workflow maturity/use metrics, executor metrics, and job traces persist across process boundaries and feed later behavior; raw learning records are redacted before persistence.

LEARNED_ROUTER: measured success, verification, latency, retries, and corrections modify rank only after hard health/security/node/approval gates; routing decisions and explanations persist when recorded.

FLEET: normal completion is `no_action`; repeated failure signatures cluster without creating duplicate open incidents.

INCIDENTS: repeated failures update one incident, redundant retries are suppressed while open, and resolution is explicit.

ROUTINES: cheap detectors return `NO_ACTION` without work when unchanged; changed paths invoke existing workflows.

BACKUP: create/inspect/verify/restore/reconstruct work through services and CLI; restore rejects unsafe members, unlisted files/tables, row-count mismatches, secret payloads, and unknown columns before mutation; manifests omit private source paths; dry-run reports target table/column incompatibility.

TRACE: redacted request, decisions, failures, actual retries, verification, escalation, cache/workflow changes, workers, and measured usage persist per objective.

BENCHMARK: 11 deterministic local tasks; 2 cache hits; 1 workflow reuse; 6 workers; 10 verification calls; 0 retries; 0 model/premium/Grok calls; 0 human escalations; 2 duplicate requests prevented; 143 ms elapsed in the final measured run.

PYC_SOURCE_STATUS: 69 first-party `.py` sources; 69 current CPython 3.12 caches; 29 cross-version CPython 3.14 caches; 0 source-less `.pyc`; `.py` is authoritative. The public release is prepared from a dedicated repository rather than the parent history.

RELEASE_VERSION: 0.5.0 (`pyproject.toml`, package metadata, CLI, MCP import path, and local A2A default).

RELEASE_GATE: PASS for the local AgentOS code gate: 710/710 tests passed with 0 failures, 0 errors, and 0 skips; fresh CLI, benchmark, backup, doctor, source, and bytecode checks are green. The dedicated public remote and GitHub Actions validation are verified in `PUBLICATION_AUDIT.md`.

## Release highlights

### Architecture

AgentOS is a provider-neutral execution and orchestration control plane. CLI, loopback HTTP, and MCP stdio share `services.AgentOS`; SQLite persistence, capability discovery, bounded adapters, verification, and recovery sit behind one core. GrokBot Office remains a separate workforce configuration layer, not an AgentOS dependency.

### Efficiency and ResourceGovernor

The engine uses exact cache, deterministic and state fast paths, verified workflow reuse, compact deltas, and smallest-capable-team planning. `ResourceGovernor` constrains live eligibility, specialist fan-out, groups, routines, and experimental work from recorded usage signals; it never fabricates usage or authorizes spend.

### Federation, A2A, and MCP

Executor cells, typed job/result envelopes, plan-before-premium routing, bounded failover, and the read-only GrokBot Office bridge are implemented and tested. A2A v1 discovery/interop and MCP candidate discovery/certification are explicit lifecycle surfaces; remote peers and MCP transports remain unconfigured until reviewed credentials and approvals exist.

### Verification and learning

The local release gate is 710/710 tests with zero failures, errors, or skips. Verification selects bounded retry, alternate, supervisor, or human paths; cache, coach lessons, workflow maturity, executor metrics, learned routing, fleet incidents, routines, and job traces persist as measured state.

### Limitations

LIVE provider calls, production API authentication, execution sandboxing, a first-class MCP client, and live external-worker claims are not part of this release. A capability marked AVAILABLE is not proof of a live external execution.

### Setup

```bash
git clone https://github.com/M4G3LL4N0/agentos.git
cd agentos
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests
.venv/bin/agentos init
```

NEXT: the public v0.5.0 release is published from the verified code commit; no deployment is authorized.
