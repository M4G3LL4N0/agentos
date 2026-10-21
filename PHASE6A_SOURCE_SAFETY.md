# NOAERTH Phase 6A — Source & Backup Safety

Date: 2026-10-21 · Verified against tip `6afdf68`.

## Source integrity

| Check | Result |
|---|---|
| Local HEAD | `6afdf68` ("tests: sync 3 governor gate tests from server; verify release rollback targets") |
| Unpushed commits preserved | **40 ahead of origin/main — not pushed, not rewritten** |
| Recovery bundle | `/var/backups/agentos/agentos-full-recovery.bundle` (770,620 B) — **tip `6afdf68`, 47 commits, bare-clone verified** |
| Server deploy tree | `/srv/noaerth/agentos` is a deploy tree (not a git repo); src/ drift vs local: none (only egg-info metadata); test trees converged at 63 files / 958 tests |
| History rewrites | none performed |
| git push | none performed |

## Backup landscape (same host)

- Daily `noaerth-agentos-backup` (root cron 03:17): layered `dr-*.tar.gz` +
  portable `agentos_backup_*.tar.gz` into `/var/backups/agentos/` —
  **retention bounded** (`RETENTION` prune of aged archives, verified in
  script). Latest: `dr-20261021T011701Z.tar.gz` + portable twin, both OK.
- These are **same-disk copies — not disaster recovery**. They do not count
  as off-host redundancy.

## Off-host status

**No authorized off-host encrypted backup exists.** Verified absent:
restic/borg/rclone binaries and configs (root + portfolio), `/etc/restic`,
rclone config dirs. No credentials or history were transferred.

### Prepared off-host recovery option (operator-run only; nothing executed)

Requires an operator-provided endpoint + passphrase (never handled here):

```bash
# operator generates passphrase out-of-band, then:
apt-get install -y restic            # or static binary
export RESTIC_REPOSITORY="sftp:backup@ENDPOINT:/restic/agentos"
export RESTIC_PASSWORD_FILE=/root/.secrets/restic.pass   # operator-provided
restic init
restic backup /var/backups/agentos/agentos-full-recovery.bundle \
  --exclude-caches --tag noaerth-agentos
restic check
```

Private git history leaves this host **only** under that explicit operator
action. Credentials are never stored in the repo or deliverables.

## Explicit non-actions (safety proof)

- No `git push`, no `git rebase/reset/filter` on any repo.
- No deletion of: source trees, venture repos, databases, saves, releases,
  backups, preserved Studio evidence, worktrees, rollback artifacts.
- Bundle refresh deferred until next code change (bundle already matches
  tip; refreshing without changes would add no safety).
