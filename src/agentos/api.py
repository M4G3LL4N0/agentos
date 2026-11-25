"""Lightweight HTTP API over the AgentOS core (stdlib only).

Same service methods as the CLI — no duplicated business logic. Small,
stable surface: health, status, capabilities, objectives, executions
(via inspect), events, run, verify, recover, delegate. Binds loopback by
default; remote binds require bearer authentication. Read tokens can access GET routes only.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from agentos import __version__
from agentos.engine import AgentOSError
from agentos.services import AgentOS


class APIError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    try:
        raw = handler.rfile.read(length).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise APIError(400, f"unreadable request body: {exc}")
    try:
        body = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise APIError(400, f"invalid JSON body: {exc}")
    if not isinstance(body, dict):
        raise APIError(400, "JSON body must be an object")
    return body


class AgentOSHandler(BaseHTTPRequestHandler):
    """Request handler. The AgentOS instance is injected as ``server.app``."""

    server_version = f"AgentOS/{__version__}"

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
        pass

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        if status == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="AgentOS API"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @property
    def app(self) -> AgentOS:
        return self.server.app  # type: ignore[attr-defined]

    def _authorize(self, *, read_only: bool) -> None:
        token = getattr(self.server, "auth_token", None)
        if token is None:
            return
        presented = self.headers.get("Authorization", "")
        scheme, separator, credential = presented.partition(" ")
        accepted = [token]
        if read_only:
            read_token = getattr(self.server, "read_token", None)
            if read_token is not None:
                accepted.append(read_token)
        if not separator or scheme.lower() != "bearer" or not credential:
            raise APIError(401, "authentication required")
        if not any(secrets.compare_digest(credential, value) for value in accepted):
            raise APIError(401, "authentication required")

    def do_GET(self) -> None:
        try:
            self._authorize(read_only=True)
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            payload = self._route_get(parsed.path, query)
            self._send(200, {"ok": True, **payload})
        except APIError as exc:
            self._send(exc.status, {"ok": False, "error": exc.message})
        except AgentOSError as exc:
            self._send(404, {"ok": False, "error": str(exc)})
        except Exception as exc:  # never leak tracebacks; report honestly
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self) -> None:
        try:
            self._authorize(read_only=False)
            parsed = urlparse(self.path)
            body = _read_json(self)
            payload, status = self._route_post(parsed.path, body)
            self._send(status, {"ok": True, **payload})
        except APIError as exc:
            self._send(exc.status, {"ok": False, "error": exc.message})
        except AgentOSError as exc:
            self._send(404, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    # ------------------------------------------------------------------
    def _route_get(self, path: str, query: dict[str, list[str]]) -> dict[str, Any]:
        if path == "/health":
            return {"healthy": True, "version": __version__}
        if path == "/status":
            return self.app.status()
        if path == "/dashboard":
            return self.app.dashboard()
        if path == "/capabilities":
            return {"capabilities": [c.to_dict() for c in self.app.capabilities()]}
        if path == "/agents":
            return {"agents": [c.to_dict() for c in self.app.agents()]}
        if path == "/objectives":
            status = query.get("status", [None])[0]
            return {
                "objectives": [o.to_dict() for o in self.app.list_objectives(status=status)]
            }
        if path == "/events":
            objective = query.get("objective", [None])[0]
            limit = int(query.get("limit", ["200"])[0])
            return {"events": self.app.events(objective_id=objective, limit=limit)}
        if path == "/agent-card":
            # A2A serving stays OFF by default; only an explicit operator
            # flag exposes the minimal read-only card on loopback.
            if not ((self.app.config.get("a2a") or {}).get("serve_card")):
                raise APIError(404, "agent card serving is off (config.a2a.serve_card)")
            return {"card": self.app.a2a_card()}
        if path == "/federation/snapshot":
            return self.app.paios_snapshot()
        if path == "/plans":
            objective = query.get("objective", [None])[0]
            return {"plans": self.app.plans(objective)}
        if path == "/patterns":
            return {"patterns": self.app.patterns(limit=100)}
        if path == "/gaps":
            return {"gaps": self.app.gaps()}
        match = re.fullmatch(r"/objectives/([^/]+)", path)
        if match:
            return self.app.inspect(match.group(1))
        match = re.fullmatch(r"/objectives/([^/]+)/quality", path)
        if match:
            return {"quality": self.app.quality(match.group(1))}
        match = re.fullmatch(r"/objectives/([^/]+)/gaps", path)
        if match:
            return {"gaps": self.app.gaps(match.group(1))}
        raise APIError(404, f"unknown route {path!r}")

    def _route_post(self, path: str, body: dict[str, Any]) -> tuple[dict[str, Any], int]:
        if path == "/objectives":
            title = body.get("title")
            if not title:
                raise APIError(400, "field 'title' is required")
            objective = self.app.create_objective(
                str(title),
                description=str(body.get("description", "")),
                priority=str(body.get("priority", "MEDIUM")),
                operation=body.get("operation"),
                params=body.get("params"),
                constraints=body.get("constraints"),
                parent_id=body.get("parent_id"),
                extra_context=body.get("context"),
            )
            return {"objective": objective.to_dict()}, 201
        if path == "/delegate":
            title = body.get("title")
            if not title:
                raise APIError(400, "field 'title' is required")
            report = self.app.delegate(
                str(title),
                description=str(body.get("description", "")),
                operation=body.get("operation"),
                params=body.get("params"),
                constraints=body.get("constraints"),
                priority=str(body.get("priority", "MEDIUM")),
                max_attempts=body.get("max_attempts"),
                timeout_seconds=body.get("timeout_seconds"),
            )
            return {"report": report.to_dict()}, (
                200 if report.state.value == "COMPLETED" else 422
            )
        match = re.fullmatch(r"/objectives/([^/]+)/run", path)
        if match:
            report = self.app.run(
                match.group(1),
                operation=body.get("operation"),
                max_attempts=body.get("max_attempts"),
                timeout_seconds=body.get("timeout_seconds"),
                force=bool(body.get("force", False)),
            )
            return {"report": report.to_dict()}, (
                200 if report.state.value == "COMPLETED" else 422
            )
        match = re.fullmatch(r"/objectives/([^/]+)/verify", path)
        if match:
            verification = self.app.verify(match.group(1))
            return {"verification": verification.to_dict()}, (
                200 if verification.verified else 422
            )
        match = re.fullmatch(r"/objectives/([^/]+)/recover", path)
        if match:
            report = self.app.recover(
                match.group(1),
                max_attempts=body.get("max_attempts"),
                operation=body.get("operation"),
            )
            return {"report": report.to_dict()}, (
                200 if report.state.value == "COMPLETED" else 422
            )
        match = re.fullmatch(r"/objectives/([^/]+)/cancel", path)
        if match:
            objective = self.app.cancel_objective(match.group(1))
            return {"objective": objective.to_dict()}, 200
        raise APIError(404, f"unknown route {path!r}")


def _is_loopback_host(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def _validate_api_token(token: str | None, name: str) -> None:
    if token is None:
        return
    if len(token) < 32 or not token or any(character.isspace() for character in token):
        raise ValueError(f"{name} must be at least 32 non-whitespace characters")


def create_server(
    app: AgentOS,
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    auth_token: str | None = None,
    read_token: str | None = None,
) -> ThreadingHTTPServer:
    """Create the API server with optional bearer authentication.

    Tokens default to AGENTOS_API_TOKEN and AGENTOS_API_READ_TOKEN. A read
    token authorizes GET routes only; the main token authorizes all routes.
    This stdlib server is plain HTTP and refuses non-loopback binds. For
    remote access, put it behind an encrypted private transport (for example,
    a private mesh or TLS proxy) and keep this server bound to loopback.
    """
    if auth_token is None:
        auth_token = os.environ.get("AGENTOS_API_TOKEN") or None
    if read_token is None:
        read_token = os.environ.get("AGENTOS_API_READ_TOKEN") or None
    _validate_api_token(auth_token, "AGENTOS_API_TOKEN")
    _validate_api_token(read_token, "AGENTOS_API_READ_TOKEN")
    if read_token is not None and auth_token is None:
        raise ValueError("AGENTOS_API_TOKEN is required when a read-only token is configured")
    if not _is_loopback_host(host):
        raise ValueError(
            "AgentOS HTTP API only supports loopback binds; use a TLS/private-network proxy for remote access"
        )

    server = ThreadingHTTPServer((host, port), AgentOSHandler)
    server.app = app  # type: ignore[attr-defined]
    server.auth_token = auth_token  # type: ignore[attr-defined]
    server.read_token = read_token  # type: ignore[attr-defined]
    server.daemon_threads = True
    return server
