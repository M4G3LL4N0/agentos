#!/bin/bash
# Phase 2 sync: push source to the AgentOS server, reinstall, restart, verify.
# Usage: ./deploy/sync-phase2.sh [user@host]
set -euo pipefail

HOST="${1:-root@100.127.182.126}"
KEY="${AGENTOS_SSH_KEY:-$HOME/.ssh/noaerth_cloud}"
DEST="/srv/noaerth/agentos"

echo "== syncing source to ${HOST}:${DEST} =="
rsync -az --delete \
  -e "ssh -i ${KEY} -o ConnectTimeout=20" \
  --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' \
  --exclude '.venv' --exclude 'dist' --exclude 'build' \
  ./ "${HOST}:${DEST}/"

echo "== reinstalling package =="
ssh -i "${KEY}" "${HOST}" \
  "pip3 install -e ${DEST} --break-system-packages -q && python3 -m unittest discover -s ${DEST}/tests -q"

echo "== restarting service =="
ssh -i "${KEY}" "${HOST}" "systemctl restart agentos && sleep 2 && systemctl is-active agentos"

echo "== health check =="
ssh -i "${KEY}" "${HOST}" "curl -sf http://127.0.0.1:8765/health && echo"

echo "sync complete"
