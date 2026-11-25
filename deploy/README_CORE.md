# Core 01: staged AgentOS systemd deployment kit

**Status:** reviewable packaging only; not installed on Core 01.  
**API bind:** `127.0.0.1:8765` only. The service unit never opens a public listener.  
**Current source pin:** `bdd960cb17249c364225394ceff7d4ce7eed71f2` (AgentOS main after the APN contract and API-auth merges). Recheck the reviewed main commit before deployment; do not deploy a moving branch name.

This kit makes a first Core deployment repeatable and easier to audit. It does not provision a remote tunnel, modify a firewall, connect Forge or Storage, create Telegram messages, or claim that a fleet task has run.

## What it installs

- A dedicated `agentos` service account.
- The pinned AgentOS source in a root-owned release directory and a stable Python virtual environment.
- A systemd service with a private writable state directory, bounded restart policy, and standard Linux hardening.
- Separate high-entropy main and read-only API tokens in `/etc/agentos/agentos.env`, created with mode `0600` and never printed.
- A health checker that uses the main token only against the fixed local endpoint `http://127.0.0.1:8765/health`. It bypasses proxies and does not follow redirects.

The AgentOS read-only token authorizes all current GET routes. Do not give it to Vercel, Telegram, or another broad audience until a separate redacted, scope-limited status API exists.

## Preconditions

1. Review the exact AgentOS commit and this PR; verify its tests, source provenance, and rollback plan.
2. Confirm a restorable backup of Core state and available disk space. Keep the existing installation intact as a rollback target.
3. Confirm the service account and system paths do not already exist, or adapt the steps after inspection.
4. Keep the API bound to loopback. Do not open port 8765 in a firewall or bind it to a public/private interface. Remote access requires a separately reviewed encrypted private transport and per-action authorization.
5. Do not enable unattended task execution from the API. The first deployment gate is authenticated health/status only.

## Install outline (Core 01 only)

Run these steps from a reviewed checkout as an administrator. They are instructions for a human operator; this repository change does not execute them.

```sh
REV=bdd960cb17249c364225394ceff7d4ce7eed71f2
sudo useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin agentos
sudo install -d -o root -g root -m 0755 /opt/agentos/releases /etc/agentos
sudo git clone https://github.com/M4G3LL4N0/agentos.git "/opt/agentos/releases/$REV"
sudo git -C "/opt/agentos/releases/$REV" checkout --detach "$REV"
sudo python3 -m venv /opt/agentos/venv
sudo /opt/agentos/venv/bin/pip install "/opt/agentos/releases/$REV"
sudo ln -sfn "/opt/agentos/releases/$REV" /opt/agentos/current
sudo /usr/bin/python3 /opt/agentos/current/scripts/create-agentos-secrets.py
sudo install -o root -g root -m 0644 /opt/agentos/current/deploy/systemd/agentos.service /etc/systemd/system/agentos.service
sudo systemctl daemon-reload
sudo systemctl enable --now agentos.service
sudo /usr/bin/python3 /opt/agentos/current/scripts/agentos-healthcheck.py
```

The source and virtual environment are root-owned and read-only to the service account. `StateDirectory=agentos` gives the service its persistent writable state at `/var/lib/agentos`. The environment file is root-owned and readable only by root/systemd; the process receives the tokens through its service environment.

## Verify and recover

```sh
sudo systemctl --no-pager --full status agentos.service
sudo /usr/bin/python3 /opt/agentos/current/scripts/agentos-healthcheck.py
sudo journalctl -u agentos.service --since today --no-pager
```

The health checker prints only PASS/FAIL and never prints a token or response body. If it fails, stop here; do not open a network port. Check unit state, file permissions, service logs, and local disk before deciding on recovery. Keep recovery bounded: investigate first, restore the previous release pointer only after validating it, and preserve a verified backup before touching the SQLite state.

After this local Core gate passes, separately verify authenticated GET-only access from an approved local monitor. Do not treat that as the end-to-end APN proof. The remaining gates are a real Core lease, isolated Forge execution, independent verification, authenticated Storage receipt, Core reconciliation, Telegram delivery receipt, and a tested restore/fault path.
