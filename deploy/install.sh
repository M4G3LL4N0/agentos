#!/bin/bash
# AgentOS systemd service installer
set -euo pipefail

AGENTOS_USER="agentos"
AGENTOS_HOME="/srv/noaerth/agentos"

if ! id "$AGENTOS_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$AGENTOS_USER"
fi

mkdir -p "$AGENTOS_HOME"
mkdir -p "$AGENTOS_HOME/state"
mkdir -p "$AGENTOS_HOME/logs"

pip3 install -e /srv/noaerth/agentos

chown -R "$AGENTOS_USER:$AGENTOS_USER" "$AGENTOS_HOME"

cp /srv/noaerth/agentos/deploy/agentos.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable agentos.service

echo "AgentOS service installed. Start with: systemctl start agentos"
