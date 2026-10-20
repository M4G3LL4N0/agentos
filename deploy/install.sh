#!/bin/bash
# AgentOS systemd service installer
set -euo pipefail

AGENTOS_USER="agentos"
CODE_ROOT="/srv/noaerth/agentos"
AGENTOS_HOME="/var/lib/agentos"
AGENTOS_LOG_DIR="/var/log/agentos"
AGENTOS_CONFIG_DIR="/etc/agentos"
AGENTOS_USER_HOME="/home/agentos"

if ! id "$AGENTOS_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin "$AGENTOS_USER"
fi

# service account home (needed by the JanitorOS knowledge store)
if [ ! -d "$AGENTOS_USER_HOME" ]; then
    mkdir -p "$AGENTOS_USER_HOME"
    chown "$AGENTOS_USER:$AGENTOS_USER" "$AGENTOS_USER_HOME"
    chmod 750 "$AGENTOS_USER_HOME"
fi

# separated state / log / config locations
mkdir -p "$AGENTOS_HOME" "$AGENTOS_LOG_DIR" "$AGENTOS_CONFIG_DIR"
chown "$AGENTOS_USER:$AGENTOS_USER" "$AGENTOS_HOME" "$AGENTOS_LOG_DIR"
chmod 750 "$AGENTOS_HOME" "$AGENTOS_LOG_DIR"
chmod 755 "$AGENTOS_CONFIG_DIR"

# seed config into /etc/agentos when the tree provides one
if [ -f "$CODE_ROOT/config.json" ] && [ ! -f "$AGENTOS_CONFIG_DIR/config.json" ]; then
    install -o root -g "$AGENTOS_USER" -m 660 "$CODE_ROOT/config.json" "$AGENTOS_CONFIG_DIR/config.json"
fi

# shared JanitorOS admission binary (used by the AgentOS service account)
if [ -x /home/portfolio/.local/bin/janitor ] && [ ! -x /usr/local/bin/janitor ]; then
    install -o root -g root -m 755 "$(readlink -f /home/portfolio/.local/bin/janitor)" /usr/local/bin/janitor
fi

pip3 install -e "$CODE_ROOT"

chown -R "$AGENTOS_USER:$AGENTOS_USER" "$AGENTOS_HOME"

cp "$CODE_ROOT/deploy/agentos.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable agentos.service

echo "AgentOS service installed. Start with: systemctl start agentos"
