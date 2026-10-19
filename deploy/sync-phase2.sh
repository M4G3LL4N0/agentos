#!/bin/bash
# Phase 2 sync: push source to the AgentOS server, reinstall, restart, verify.
# Usage: ./deploy/sync-phase2.sh [user@host]
set -euo pipefail

HOST="${1:-root@100.127.182.126}"
KEY="${AGENTOS_SSH_KEY:-$HOME/.ssh/noaerth_cloud}"
DEST="/srv/noaerth/agentos"

echo "== syncing source to ${HOST}:${DEST} =="
# NOTE: never use --delete here. The destination holds live service state
# (agentos.db, state/, logs/) that does not exist in the local repo; deleting
# it destroyed Phase 1 state on 2026-10-19.
rsync -az \
  -e "ssh -i ${KEY} -o ConnectTimeout=20" \
  --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
  --exclude '.venv' --exclude 'dist' --exclude 'build' \
  --exclude 'agentos.db*' --exclude 'state/' --exclude 'logs/' \
  --exclude 'loop_state.json' --exclude 'loop_limits.json' --exclude 'contracts/' \
  ./ "${HOST}:${DEST}/"

echo "== reinstalling package and running server-side tests =="
ssh -i "${KEY}" "${HOST}" \
  "pip3 install -e ${DEST} --break-system-packages -q && python3 -m unittest discover -s ${DEST}/tests -q"

echo "== fixing ownership and restarting service =="
ssh -i "${KEY}" "${HOST}" "
  mkdir -p ${DEST}/state ${DEST}/logs &&
  chown -R agentos:agentos ${DEST} &&
  systemctl restart agentos &&
  for i in \$(seq 1 15); do
    state=\$(systemctl is-active agentos)
    if [ \"\$state\" = \"active\" ]; then break; fi
    sleep 2
  done
  systemctl is-active agentos"

echo "== health check =="
ssh -i "${KEY}" "${HOST}" "curl -sf http://127.0.0.1:8765/health && echo"

echo "sync complete"
