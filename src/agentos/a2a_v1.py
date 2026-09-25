"""A2A v1.0 remote-interop surface (Python stdlib only).

Canonical stable behavior only (A2A 1.0.0; ``0.x`` shapes are rejected,
never emitted):

- Agent Card discovery at ``/.well-known/agent-card.json`` with
  ``Accept: application/a2a+json`` and an ``A2A-Version: 1.0`` header.
- JSON-RPC 2.0 transport (``SendMessage``/``GetTask``/``CancelTask``) over
  the card's first JSONRPC interface. No gRPC, no streaming: a peer that
  is asked to stream gets ``UnsupportedOperation``.
- Version negotiation is strict: this client only speaks ``1.0`` and the
  bundled peer only serves ``1.0``. Anything else is an honest
  ``V1VersionNotSupported`` — never a silent downgrade.
- Trust is a local decision: every discovered card is ``untrusted`` until
  a local caller approves it with explicit evidence.
- Auth-metadata boundary: envelopes carrying secret-looking values are
  rejected client-side and never touch the wire.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin
from uuid import uuid4

from agentos.federation import contains_secret

PROTOCOL_VERSION = "1.0"
CARD_PATH = "/.well-known/agent-card.json"
CARD_MEDIA_TYPE = "application/a2a+json"

# A2A v1.0 error taxonomy (JSON-RPC codes used by this surface).
TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002
UNSUPPORTED_OPERATION = -32004
VERSION_NOT_SUPPORTED = -32009

TERMINAL_STATES = ("COMPLETED", "FAILED", "CANCELED", "REJECTED")
UNTRUSTED = "untrusted"
TRUSTED = "trusted"


class V1Error(Exception):
    """A v1.0 interaction failed (transport, HTTP status, or peer error)."""


class V1PeerUnreachable(V1Error):
    """Nothing is listening at the peer URL (never reported as healthy)."""


class V1InvalidCard(V1Error):
    """The peer did not return a valid v1.0 agent card."""


class V1VersionNotSupported(V1Error):
    """Peer and client could not agree on protocol version 1.0."""


class V1TaskNotFound(V1Error):
    """The peer has no such task."""


class V1TaskNotCancelable(V1Error):
    """The task already reached a terminal state."""


class V1UnsupportedOperation(V1Error):
    """The peer does not offer the requested skill/operation."""


@dataclass
class V1Interface:
    url: str
    protocol_binding: str = "JSONRPC"
    protocol_version: str = PROTOCOL_VERSION


@dataclass
class V1Skill:
    skill_id: str
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class V1AgentCard:
    """A parsed v1.0 agent card. ``trust`` is always local, never from wire."""

    name: str
    version: str = "0.0.0"
    description: str = ""
    interfaces: list[V1Interface] = field(default_factory=list)
    capabilities: dict[str, Any] = field(default_factory=dict)
    default_input_modes: list[str] = field(default_factory=lambda: ["text"])
    default_output_modes: list[str] = field(default_factory=lambda: ["text"])
    skills: list[V1Skill] = field(default_factory=list)
    trust: str = UNTRUSTED

    @property
    def protocol_version(self) -> str:
        return self.interfaces[0].protocol_version if self.interfaces else ""

    @classmethod
    def from_dict(cls, data: Any) -> "V1AgentCard":
        if not isinstance(data, dict):
            raise V1InvalidCard("agent card payload is not a JSON object")
        # Obsolete 0.x shapes are rejected, never adapted.
        for obsolete in ("url", "preferredTransport", "additionalInterfaces"):
            if obsolete in data:
                raise V1InvalidCard(
                    f"agent card uses obsolete 0.x field {obsolete!r}; "
                    "v1.0 supportedInterfaces required"
                )
        name = str(data.get("name", "")).strip()
        if not name:
            raise V1InvalidCard("agent card is missing required field 'name'")
        raw_ifaces = data.get("supportedInterfaces")
        if not isinstance(raw_ifaces, list) or not raw_ifaces:
            raise V1InvalidCard(
                "agent card is missing required 'supportedInterfaces'"
            )
        interfaces: list[V1Interface] = []
        for entry in raw_ifaces:
            if not isinstance(entry, dict):
                raise V1InvalidCard("supportedInterfaces entries must be objects")
            url = str(entry.get("url", "")).strip()
            binding = str(entry.get("protocolBinding", "")).strip()
            version = str(entry.get("protocolVersion", "")).strip()
            if not (url and binding and version):
                raise V1InvalidCard(
                    "supportedInterfaces entries require url, "
                    "protocolBinding and protocolVersion"
                )
            interfaces.append(
                V1Interface(
                    url=url, protocol_binding=binding, protocol_version=version
                )
            )
        raw_skills = data.get("skills")
        if not isinstance(raw_skills, list) or not raw_skills:
            raise V1InvalidCard("agent card is missing required 'skills'")
        skills: list[V1Skill] = []
        for entry in raw_skills:
            if not isinstance(entry, dict):
                raise V1InvalidCard("skill entries must be objects")
            skill_id = str(entry.get("id", "")).strip()
            skill_name = str(entry.get("name", "")).strip()
            tags = entry.get("tags")
            if not (skill_id and skill_name) or not isinstance(tags, list):
                raise V1InvalidCard(
                    "skills require id, name and tags (v1.0)"
                )
            skills.append(
                V1Skill(
                    skill_id=skill_id,
                    name=skill_name,
                    description=str(entry.get("description", "") or ""),
                    tags=[str(t) for t in tags],
                )
            )
        if contains_secret(json.dumps(data, default=str)):
            raise V1InvalidCard("agent card carries secret-looking values")
        return cls(
            name=name,
            version=str(data.get("version", "0.0.0") or "0.0.0"),
            description=str(data.get("description", "") or ""),
            interfaces=interfaces,
            capabilities=dict(data.get("capabilities") or {}),
            default_input_modes=list(data.get("defaultInputModes") or ["text"]),
            default_output_modes=list(data.get("defaultOutputModes") or ["text"]),
            skills=skills,
            trust=UNTRUSTED,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "supportedInterfaces": [
                {
                    "url": i.url,
                    "protocolBinding": i.protocol_binding,
                    "protocolVersion": i.protocol_version,
                }
                for i in self.interfaces
            ],
            "capabilities": dict(self.capabilities),
            "defaultInputModes": list(self.default_input_modes),
            "defaultOutputModes": list(self.default_output_modes),
            "skills": [
                {
                    "id": s.skill_id,
                    "name": s.name,
                    "description": s.description,
                    "tags": list(s.tags),
                }
                for s in self.skills
            ],
        }


@dataclass
class V1Task:
    task_id: str
    state: str
    context_id: str = ""


def _http(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    protocol_version: str = PROTOCOL_VERSION,
    timeout: float = 10.0,
) -> tuple[int, dict[str, str], Any]:
    data = (
        json.dumps(payload, default=str).encode("utf-8")
        if payload is not None
        else None
    )
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    request.add_header("Accept", f"{CARD_MEDIA_TYPE}, application/json")
    request.add_header("A2A-Version", protocol_version)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            body = json.loads(raw) if raw.strip() else {}
            return response.status, dict(response.headers), body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            body = {"error": raw or f"HTTP {exc.code}"}
        return exc.code, dict(exc.headers), body
    except (urllib.error.URLError, OSError, TimeoutError, ConnectionError) as exc:
        raise V1PeerUnreachable(
            f"peer unreachable at {url} (not listening?): {type(exc).__name__}"
        ) from exc


def _check_version_error(status: int, body: Any) -> None:
    text = json.dumps(body, default=str)
    if status in (400, 409, 422) and "VersionNotSupported" in text:
        raise V1VersionNotSupported(f"peer refused protocol version: {text[:200]}")


def discover_card(
    base_url: str,
    protocol_version: str = PROTOCOL_VERSION,
    timeout: float = 10.0,
) -> V1AgentCard:
    """Fetch the well-known v1.0 card. Always untrusted until approved."""
    url = base_url.rstrip("/") + CARD_PATH
    status, _, body = _http(
        "GET", url, protocol_version=protocol_version, timeout=timeout
    )
    _check_version_error(status, body)
    if not 200 <= status <= 299:
        raise V1Error(f"card discovery failed: HTTP {status}")
    try:
        return V1AgentCard.from_dict(body)
    except V1InvalidCard:
        raise
    except Exception as exc:
        raise V1InvalidCard(f"unparseable agent card: {exc}") from exc


def approve_card(card: V1AgentCard, evidence: Any) -> V1AgentCard:
    """Trust a card only on explicit local evidence (never wire claims)."""
    from dataclasses import replace

    if not evidence:
        return replace(card, trust=UNTRUSTED)
    return replace(card, trust=TRUSTED)


def _rpc(
    card: V1AgentCard,
    method: str,
    params: dict[str, Any],
    protocol_version: str = PROTOCOL_VERSION,
    timeout: float = 10.0,
) -> Any:
    iface = next(
        (
            i
            for i in card.interfaces
            if i.protocol_binding.upper() == "JSONRPC"
            and i.protocol_version == PROTOCOL_VERSION
        ),
        None,
    )
    if iface is None:
        raise V1VersionNotSupported("card offers no JSONRPC 1.0 interface")
    envelope = {
        "jsonrpc": "2.0",
        "id": uuid4().hex[:12],
        "method": method,
        "params": params,
    }
    status, _, body = _http(
        "POST", iface.url, payload=envelope,
        protocol_version=protocol_version, timeout=timeout,
    )
    if not isinstance(body, dict):
        raise V1Error(f"peer returned malformed RPC envelope (HTTP {status})")
    error = body.get("error")
    if isinstance(error, dict):
        raise _error_for(error)
    if not 200 <= status <= 299:
        raise V1Error(f"peer RPC failed: HTTP {status}")
    return body.get("result")


def _error_for(error: dict[str, Any]) -> V1Error:
    code = error.get("code")
    message = str(error.get("message", "peer error"))
    if code == TASK_NOT_FOUND:
        return V1TaskNotFound(message)
    if code == TASK_NOT_CANCELABLE:
        return V1TaskNotCancelable(message)
    if code == UNSUPPORTED_OPERATION:
        return V1UnsupportedOperation(message)
    if code == VERSION_NOT_SUPPORTED:
        return V1VersionNotSupported(message)
    return V1Error(f"{message} (code={code})")


def send_message(
    card: V1AgentCard,
    skill: str,
    text: str,
    protocol_version: str = PROTOCOL_VERSION,
    timeout: float = 10.0,
) -> V1Task:
    """Submit one user message to the peer's skill. Secrets never leave."""
    if contains_secret(text) or contains_secret(skill):
        raise V1Error("refusing to send secret-looking values to a peer")
    result = _rpc(
        card,
        "SendMessage",
        {
            "message": {
                "messageId": uuid4().hex,
                "role": "ROLE_USER",
                "parts": [{"text": text}],
            },
            "metadata": {"skill": skill},
        },
        protocol_version=protocol_version,
        timeout=timeout,
    )
    if not isinstance(result, dict) or not result.get("id"):
        raise V1Error("peer returned malformed task acknowledgement")
    status = result.get("status") or {}
    return V1Task(
        task_id=str(result["id"]),
        state=str(status.get("state", "SUBMITTED")),
        context_id=str(result.get("contextId", "")),
    )


def get_task(
    card: V1AgentCard,
    task_id: str,
    protocol_version: str = PROTOCOL_VERSION,
    timeout: float = 10.0,
) -> V1Task:
    result = _rpc(
        card, "GetTask", {"id": task_id},
        protocol_version=protocol_version, timeout=timeout,
    )
    if not isinstance(result, dict) or not result.get("id"):
        raise V1Error("peer returned malformed task record")
    status = result.get("status") or {}
    return V1Task(
        task_id=str(result["id"]),
        state=str(status.get("state", "SUBMITTED")),
        context_id=str(result.get("contextId", "")),
    )


def wait_task(
    card: V1AgentCard,
    task_id: str,
    protocol_version: str = PROTOCOL_VERSION,
    timeout: float = 10.0,
    interval: float = 0.02,
) -> V1Task:
    """Bounded poll until a terminal state (never an unbounded loop)."""
    deadline = time.monotonic() + max(0.1, timeout)
    while True:
        task = get_task(
            card, task_id, protocol_version=protocol_version, timeout=timeout
        )
        if task.state in TERMINAL_STATES:
            return task
        if time.monotonic() >= deadline:
            raise V1Error(f"task {task_id} did not finish within {timeout}s")
        time.sleep(max(0.0, interval))


def fetch_artifacts(
    card: V1AgentCard,
    task_id: str,
    protocol_version: str = PROTOCOL_VERSION,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    result = _rpc(
        card, "GetTask", {"id": task_id},
        protocol_version=protocol_version, timeout=timeout,
    )
    if not isinstance(result, dict):
        raise V1Error("peer returned malformed task record")
    status = (result.get("status") or {}).get("state")
    if status != "COMPLETED":
        raise V1Error(f"task {task_id} is not completed (state={status!r})")
    artifacts = result.get("artifacts") or []
    if not isinstance(artifacts, list) or not artifacts:
        raise V1Error(f"task {task_id} completed without artifacts")
    return [dict(a) for a in artifacts if isinstance(a, dict)]


def cancel_task(
    card: V1AgentCard,
    task_id: str,
    protocol_version: str = PROTOCOL_VERSION,
    timeout: float = 10.0,
) -> V1Task:
    result = _rpc(
        card, "CancelTask", {"id": task_id},
        protocol_version=protocol_version, timeout=timeout,
    )
    if not isinstance(result, dict) or not result.get("id"):
        raise V1Error("peer returned malformed cancellation record")
    status = result.get("status") or {}
    return V1Task(
        task_id=str(result["id"]),
        state=str(status.get("state", "CANCELED")),
        context_id=str(result.get("contextId", "")),
    )


def peer_to_cell(card: V1AgentCard, base_url: str) -> dict[str, Any]:
    """Describe a remote peer as an ExecutorCell dict (UNTRUSTED, HS-capped)."""
    return {
        "id": f"a2a:{card.name}",
        "provider": "a2a-remote",
        "description": f"remote A2A v1.0 peer {card.name} (untrusted by default)",
        "capability_ids": [s.skill_id for s in card.skills],
        "supported_operations": [s.skill_id for s in card.skills],
        "adapter": "a2a-remote",
        "health": "UNKNOWN",
        "tier": 6,
        "data_class_max": "INTERNAL",
        "quality_ladder": "BEST_EFFORT",
        "latency_class": "STANDARD",
        "persistence": False,
        "is_external": True,
        "config": {"base_url": base_url, "trust": UNTRUSTED},
    }


# ---------------------------------------------------------------------------
# AgentOS's own card: minimal, read-only, non-sensitive by construction.
# ---------------------------------------------------------------------------

#: Skills AgentOS may advertise. Explicit allowlist: read-only, harmless,
#: non-sensitive. Shell, filesystem, browser internals, model execution,
#: paid/credential-bearing and private capabilities are never exported.
DEFAULT_EXPORTED_SKILLS = ("echo",)


def agentos_card(
    base_url: str = "",
    exports: tuple[str, ...] = DEFAULT_EXPORTED_SKILLS,
) -> V1AgentCard:
    """Build AgentOS's own minimal card (no server started, no secrets)."""
    from agentos import __version__

    skills = [
        V1Skill(
            skill_id="echo",
            name="echo",
            description="Echo a short text payload back verbatim (read-only).",
            tags=["read-only", "deterministic"],
        )
        for skill in exports
        if skill == "echo"
    ]
    return V1AgentCard(
        name="agentos",
        version=__version__,
        description=(
            "AgentOS execution control plane (minimal read-only A2A surface)."
        ),
        interfaces=[
            V1Interface(
                url=urljoin(base_url + "/", ".") if base_url else "",
                protocol_binding="JSONRPC",
                protocol_version=PROTOCOL_VERSION,
            )
        ],
        capabilities={
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=skills,
        trust=UNTRUSTED,
    )
