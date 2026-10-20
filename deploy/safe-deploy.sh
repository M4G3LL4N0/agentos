#!/bin/bash
# AgentOS safe deployment (Phase 3 §8).
#
# Guarantees, in order:
#   1. preflight (local tests unless skipped, remote health, storage admission)
#   2. release snapshot (code tar + SQLite-backup-API db copy) BEFORE any sync
#   3. code sync that NEVER touches state (state lives outside the code tree
#      since Phase 3 §6; legacy rollback artifacts in the tree are excluded)
#   4. --delete restricted to the pure-code subtrees src/ and tests/
#   5. health gate after restart; automatic rollback to the snapshot on failure
#
# Rules honored: no rsync --delete on the live tree root, no state deletion,
# no sync without a snapshot, never push, never paid model calls.
set -euo pipefail

HOST="${AGENTOS_DEPLOY_HOST:-root@100.127.182.126}"
SSH_KEY="${AGENTOS_DEPLOY_KEY:-$HOME/.ssh/noaerth_cloud}"
REMOTE_CODE="/srv/noaerth/agentos"
RELEASE_DIR="/var/backups/agentos/releases"
SKIP_TESTS=0
DRY_RUN=0
ALLOW_DIRTY=0

while [ $# -gt 0 ]; do
    case "$1" in
        --skip-tests) SKIP_TESTS=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --allow-dirty) ALLOW_DIRTY=1; shift ;;
        *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

SSH=(ssh -i "$SSH_KEY" -o ConnectTimeout=10 "$HOST")
RSYNC_RSH="ssh -i $SSH_KEY"
REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
TS=$(date -u +%Y%m%dT%H%M%SZ)
RELEASE_TAR="$RELEASE_DIR/$TS.code.tar.gz"

SSH_RUN() { "${SSH[@]}" "$@"; }

fail() { echo "DEPLOY_FAIL: $*" >&2; exit 1; }

echo "== preflight (local) =="
[ -d "$REPO_DIR/src/agentos" ] || fail "repo layout unexpected: $REPO_DIR"
if [ "$ALLOW_DIRTY" != 1 ] && [ -n "$(git -C "$REPO_DIR" status --porcelain)" ]; then
    git -C "$REPO_DIR" status --porcelain | head -20 >&2
    fail "working tree dirty (commit first or --allow-dirty)"
fi
if [ "$SKIP_TESTS" != 1 ]; then
    (cd "$REPO_DIR" && python3 -m unittest discover -s tests 2>&1 | tail -2)
fi

echo "== preflight (remote) =="
SSH_RUN "systemctl is-active agentos" >/dev/null || fail "agentos.service not active"
SSH_RUN "curl -sf http://127.0.0.1:8765/health" | grep -q '"healthy": *true' \
    || SSH_RUN "curl -sf http://127.0.0.1:8765/health" | grep -q '"healthy":true' \
    || fail "health endpoint unhealthy"
SSH_RUN "df -P /var/lib | awk 'NR==2{exit (\$4+0 < 20480)}'" || fail "less than 20GB free on /var/lib"
# storage admission gate via the repaired JanitorOS binary (service identity)
ADMIT=$(SSH_RUN "sudo -u agentos -H /usr/local/bin/janitor space admit --bytes 524288000 --json" || true)
echo "$ADMIT" | grep -q '"admit": *true' || echo "$ADMIT" | grep -q '"admit":true' \
    || fail "storage admission refused: $ADMIT"
echo "admission: ok"

if [ "$DRY_RUN" = 1 ]; then
    echo "DRY_RUN_OK: preflight passed; snapshot+sync not executed"
    exit 0
fi

echo "== release snapshot (rollback artifact) =="
SSH_RUN "mkdir -p $RELEASE_DIR && tar -czf $RELEASE_TAR -C $REMOTE_CODE \
    --exclude=agentos.db --exclude=agentos.db-wal --exclude=agentos.db-shm \
    --exclude=loop_state.json --exclude=loop_limits.json \
    --exclude=state --exclude=logs --exclude=contracts \
    --exclude=__pycache__ --exclude='*.pyc' --exclude=.venv \
    ." || fail "release snapshot failed"
SSH_RUN "python3 - <<'PY'
import sqlite3
src = sqlite3.connect('file:/var/lib/agentos/agentos.db?mode=ro', uri=True)
dst = sqlite3.connect('$RELEASE_DIR/$TS.db')
with dst:
    src.backup(dst)
assert dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
src.close(); dst.close()
print('db snapshot ok')
PY" || fail "db snapshot failed"
SSH_RUN "ls -la $RELEASE_TAR $RELEASE_DIR/$TS.db"
# keep the 3 newest releases
SSH_RUN "ls -1t $RELEASE_DIR/*.code.tar.gz | tail -n +4 | xargs -r rm --; ls -1t $RELEASE_DIR/*.db | tail -n +4 | xargs -r rm --"

rollback() {
    echo "== ROLLING BACK to $TS =="
    "${SSH[@]}" "set -e
        systemctl stop agentos || true
        tar -xzf $RELEASE_TAR -C $REMOTE_CODE
        chown -R agentos:agentos $REMOTE_CODE
        systemctl start agentos
        sleep 3
        curl -sf http://127.0.0.1:8765/health" || echo "ROLLBACK HEALTH CHECK FAILED" >&2
    echo "ROLLBACK_DONE"
}
trap 'rollback' ERR

echo "== sync code (state excluded; --delete only in src/ and tests/) =="
rsync -a -e "$RSYNC_RSH" \
    --exclude 'agentos.db*' --exclude 'loop_state.json' --exclude 'loop_limits.json' \
    --exclude 'state/' --exclude 'logs/' --exclude 'contracts/' --exclude 'config.json' \
    --exclude '__pycache__' --exclude '*.pyc' --exclude '.venv' \
    "$REPO_DIR/src/" "$HOST:$REMOTE_CODE/src/"
rsync -a --delete -e "$RSYNC_RSH" \
    --exclude '__pycache__' --exclude '*.pyc' \
    "$REPO_DIR/src/" "$HOST:$REMOTE_CODE/src/"
rsync -a --delete -e "$RSYNC_RSH" \
    --exclude '__pycache__' --exclude '*.pyc' \
    "$REPO_DIR/tests/" "$HOST:$REMOTE_CODE/tests/"
rsync -a -e "$RSYNC_RSH" \
    "$REPO_DIR/deploy/" "$HOST:$REMOTE_CODE/deploy/"
rsync -a -e "$RSYNC_RSH" \
    --exclude '__pycache__' --exclude '*.pyc' \
    "$REPO_DIR/product.json" "$REPO_DIR/pyproject.toml" "$HOST:$REMOTE_CODE/"

echo "== install + ownership =="
SSH_RUN "chown -R agentos:agentos $REMOTE_CODE/src $REMOTE_CODE/tests $REMOTE_CODE/deploy $REMOTE_CODE/product.json $REMOTE_CODE/pyproject.toml"
# --break-system-packages matches this server's established install practice
# (PEP 668 externally-managed environment; editable install, no deps pulled).
SSH_RUN "pip3 install -q --no-deps --break-system-packages -e $REMOTE_CODE"

echo "== restart + health gate =="
SSH_RUN "systemctl restart agentos"
for i in $(seq 1 15); do
    if SSH_RUN "curl -sf http://127.0.0.1:8765/health" | grep -qE '"healthy": ?true'; then
        echo "health: ok (attempt $i)"
        trap - ERR
        echo "DEPLOY_OK ts=$TS release=$RELEASE_TAR"
        exit 0
    fi
    sleep 2
done
rollback
trap - ERR
fail "health gate never passed after restart (rolled back to $TS)"
