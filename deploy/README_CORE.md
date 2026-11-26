# Core 01: staged AgentOS systemd deployment kit

**Status:** reviewable packaging only; not installed on Core 01.  
**API bind:** `127.0.0.1:8765` only. The service unit never opens a public listener.  
**API-auth base:** `bdd960cb17249c364225394ceff7d4ce7eed71f2`. The deployment kit is only available in PR #6. Do not install until that PR has passed review and is merged. Then pin the exact full SHA of the reviewed main commit that contains both AgentOS and the kit; never deploy a moving branch name or the base commit alone.

This kit makes a first Core deployment repeatable and easier to audit. It does not provision a remote tunnel, modify a firewall, connect Forge or Storage, create Telegram messages, or claim that a fleet task has run.

## What it installs

- A dedicated `agentos` service account.
- The pinned AgentOS source in a root-owned release directory and a per-release Python virtual environment.
- A systemd service with a private writable state directory, bounded restart policy, and standard Linux hardening.
- Separate high-entropy `AGENTOS_API_TOKEN`, broad `AGENTOS_API_READ_TOKEN`, and `/health`-only `AGENTOS_API_HEALTH_TOKEN` values in `/etc/agentos/agentos.env`, created with mode `0600` and never printed.
- A health checker that first confirms `agentos.service` is active, then uses only the `/health`-scoped token against fixed `127.0.0.1:8765`. It bypasses proxies and does not follow redirects.

The AgentOS read-only token authorizes all current GET routes. Do not give it to Vercel, Telegram, or another broad audience until a separate redacted, scope-limited status API exists.

## Preconditions

1. Review the exact AgentOS commit and this PR; verify its tests, source provenance, and rollback plan.
2. Confirm a restorable backup of Core state and available disk space. Keep the existing installation intact as a rollback target.
3. Confirm the service account and system paths do not already exist, or adapt the steps after inspection. The install command uses `--user-group` because the unit requires the matching `agentos` group.
4. Keep the API bound to loopback. Do not open port 8765 in a firewall or bind it to a public/private interface. Remote access requires a separately reviewed encrypted private transport and per-action authorization.
5. Do not enable unattended task execution from the API. The first deployment gate is authenticated health/status only.

## Install outline (Core 01 only)

Run these steps only after PR #6 is independently reviewed and merged. Set `REV` to the exact full SHA of the reviewed main commit containing the kit and the authenticated API. These are instructions for an operator; this change does not execute them.

```sh
REV=<approved-merged-main-commit-sha>
sudo useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin agentos
sudo install -d -o root -g root -m 0755 /opt/agentos/releases /etc/agentos
sudo git clone https://github.com/M4G3LL4N0/agentos.git "/opt/agentos/releases/$REV"
sudo git -C "/opt/agentos/releases/$REV" checkout --detach "$REV"
CHECKED_OUT="$(sudo git -C "/opt/agentos/releases/$REV" rev-parse HEAD)"
test "$CHECKED_OUT" = "$REV"
sudo test -f "/opt/agentos/releases/$REV/scripts/create-agentos-secrets.py"
sudo test -f "/opt/agentos/releases/$REV/deploy/systemd/agentos.service"
sudo test -f "/opt/agentos/releases/$REV/scripts/agentos-healthcheck.py"
sudo python3 -m venv "/opt/agentos/releases/$REV/.venv"
sudo "/opt/agentos/releases/$REV/.venv/bin/pip" install "/opt/agentos/releases/$REV"
sudo ln -s "/opt/agentos/releases/$REV" "/opt/agentos/.current.$REV.next"
sudo mv -Tf "/opt/agentos/.current.$REV.next" /opt/agentos/current
sudo /usr/bin/python3 /opt/agentos/current/scripts/create-agentos-secrets.py
sudo install -o root -g root -m 0644 /opt/agentos/current/deploy/systemd/agentos.service /etc/systemd/system/agentos.service
sudo systemctl daemon-reload
sudo systemctl enable --now agentos.service
sudo /usr/bin/python3 /opt/agentos/current/scripts/agentos-healthcheck.py
```

The source and per-release virtual environment are root-owned and read-only to the service account. `StateDirectory=agentos` gives the service its persistent writable state at `/var/lib/agentos`. The environment file is root-owned and readable only by root/systemd; the process receives the tokens through its service environment.

This outline is for a first install. For an upgrade, prepare the new exact-SHA release and its `.venv` without changing the live pointer. Record and validate the current target with `OLD_TARGET="$(readlink -f /opt/agentos/current)"`; retain that release and its `.venv`. Create a temporary symlink in `/opt/agentos` and atomically replace `current` with same-directory rename:

```sh
sudo ln -s "/opt/agentos/releases/$REV" "/opt/agentos/.current.$REV.next"
sudo mv -Tf "/opt/agentos/.current.$REV.next" /opt/agentos/current
sudo systemctl restart agentos.service
sudo systemctl show -p MainPID -p ExecStart agentos.service
sudo /usr/bin/python3 /opt/agentos/current/scripts/agentos-healthcheck.py
```

Confirm the recorded MainPID/ExecStart and authenticated health response before calling the new release active. The checker also requires `agentos.service` active before sending its health-only token. If the upgrade fails, atomically point `current` back to `$OLD_TARGET` using a new temporary symlink and `mv -Tf`, then restart and verify. Keep both per-release virtual environments for rollback.

## Verify and recover

```sh
sudo systemctl --no-pager --full status agentos.service
sudo /usr/bin/python3 /opt/agentos/current/scripts/agentos-healthcheck.py
sudo journalctl -u agentos.service --since today --no-pager
```

The health checker requires the service to be active before reading or sending its health-only token. That token authorizes only `GET /health`; it cannot access other GET routes or write actions. The checker prints only PASS/FAIL and never prints a token or response body. If it fails, stop here; do not open a network port. Check unit state, file permissions, service logs, and local disk before deciding on recovery. Keep recovery bounded: investigate first, restore the previous release pointer only after validating it, and preserve a verified backup before touching the SQLite state.

After this local Core gate passes, separately verify authenticated GET-only access from an approved local monitor. Do not treat that as the end-to-end APN proof. The remaining gates are a real Core lease, isolated Forge execution, independent verification, authenticated Storage receipt, Core reconciliation, Telegram delivery receipt, and a tested restore/fault path.
