# NOAERTH — Next Execution Notes (post Phase 4B)

Generated 2026-10-20T22:00Z. Phase 4B (governor integration) PASS; Phases
3 and 4A PASS. 28+ local commits ahead of origin — **push only on explicit
operator request**.

## Operator decisions pending

1. **Temp-dir approval manifest** (`PHASE4A_TMP_APPROVAL_MANIFEST.md`):
   394 confirmed-disposable paths (~92MB) still await explicit sign-off.
   Nothing deleted.
2. **Push decision**: repo is 30+ commits ahead; recommend reviewing
   `PHASE4B_GOVERNOR_INTEGRATION.md` + acceptance records before push.

## Watch items (no action needed now)

- **AOS governor SEVERE / allow_new_work=false** — the primary brake is
  active; ServerOS capacity reports 0 and refuses heavyweight admission by
  design. When AutonomousOS lifts the hold and the band returns HEALTHY,
  capacity advice rises automatically (2) with no ServerOS change.
- **disk_growth UNKNOWN** — JanitorOS forecast needs more samples; the
  field populates itself. Do not fabricate a value.
- **admission-backoff.json** (autonomousos state) — a verification run
  recorded failures=1 (advisory only; cleared by the next successful
  admission). Leave it.
- **Collector import hazard** — `control/collector.py` runs its 2s loop at
  import time. Never import it casually; test via live.json or systemctl
  restart of `noaerth-ui-collector`.

## If you are continuing Phase 5+ work

- Snapshot consumers are additive-only by contract
  (`NOAERTH_RESOURCE_CONTRACT.json`). Any new consumer should do an O(1)
  read of `/var/lib/agentos/resource-snapshot.json` — never a subprocess,
  never a second sample loop.
- Optional (their-side, not started): mesh `admission.py` could consume the
  snapshot's `cpu_budget`/capacity for lease-time caps. That is the mesh
  owners' call; the contract fields already carry everything needed.
- Scratch leases: callers should `governor lease register` at task start
  and `lease complete` on verified completion; retention_policy decides
  reap vs retain.
- Server-side tests to keep green after ANY server edit:
  `python3 -m unittest discover -s tests` from `/srv/noaerth/agentos`
  as user agentos with `AGENTOS_HOME=/var/lib/agentos` (940 tests).
- Local: `.venv`-less `python3 -m unittest discover -s tests` (940; the 8
  server-only hive-gate tests skip locally by design).

## Handy commands

```bash
# publish/read the contract
noaerth agentos governor snapshot --publish
noaerth agentos governor snapshot --read
# four-question admission
noaerth agentos governor admit-worker --profile heavyweight_coding --bytes 4096 --level HIGH
# scratch lease lifecycle
noaerth agentos governor lease register --task T-1 --scratch /path --bytes 104857600 --owner me
noaerth agentos governor lease complete --task T-1
# consumers
curl -s http://127.0.0.1:8765/resource | head -c 400
python3 -m mesh doctor --json | python3 -c "import json,sys; print(json.load(sys.stdin)['resource_health'])"
python3 -c "import json; print(json.load(open('/srv/noaerth/control/state/live.json'))['serveros'])"
```
