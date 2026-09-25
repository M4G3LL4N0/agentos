"""A2A-style wire surface for local interop (Python stdlib only).

A thin, honest Agent-to-Agent (A2A 1.0 flavored) client surface: agent card
discovery, task submission, status polling, result fetching and cancellation,
all over plain UTF-8 JSON on loopback HTTP. No TLS, no third-party deps.

Trust model: a remote agent is ALWAYS reported as ``untrusted`` unless an
explicit, locally-supplied approval artifact (non-empty evidence) is present.
A card that *claims* trust on the wire is downgraded back to untrusted during
discovery. Availability is never fabricated: a peer that is not listening is
reported as unreachable (``A2APeerUnreachable``), never as down-but-fine.
"""

from __future__ import annotations

import dataclasses
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

UNTRUSTED = "untrusted"
TRUSTED = "trusted"
DEFAULT_CARD_TYPE = "test-peer"
A2A_VERSION = "1.0"

_HTTP_OK_RANGES = (200, 299)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class A2AOperation(StrEnum):
    """The A2A operations this surface speaks."""

    DISCOVER = "discover"   # resolve an agent card
    SUBMIT = "submit"       # submit a task / AgentOS JobEnvelope
    STATUS = "status"       # poll a task's status
    RESULT = "result"       # fetch a completed result / artifact
    CANCEL = "cancel"       # cancel a task


class A2AError(Exception):
    """An A2A interaction failed (transport, HTTP status, or malformed data)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class A2APeerUnreachable(A2AError):
    """The peer is not listening/answering at the given URL."""


class A2AInvalidCard(A2AError):
    """The peer did not return a valid agent card."""


class A2ANotDoneError(A2AError):
    """A result was requested before the task reached a terminal state."""


@dataclass
class A2AAgentCard:
    """An agent's self-description as exchanged over the A2A surface.

    ``trust`` never survives the wire: anything coming from a remote peer is
    ``untrusted`` until a local caller explicitly approves it with evidence.
    """

    name: str
    version: str = "0.0.0"
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)
    url: str = ""
    trust: str = UNTRUSTED
    type: str = DEFAULT_CARD_TYPE

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "operations": list(self.operations),
            "url": self.url,
            "trust": self.trust,
            "type": self.type,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        source_url: str = "",
    ) -> "A2AAgentCard":
        if not isinstance(data, dict):
            raise A2AInvalidCard("agent payload is not a JSON object")
        name = str(data.get("name", "")).strip()
        if not name:
            raise A2AInvalidCard("agent card is missing required field 'name'")
        # Trust is a local decision: never inherit a wire-side trust claim.
        return cls(
            name=name,
            version=str(data.get("version", "0.0.0") or "0.0.0"),
            description=str(data.get("description", "") or ""),
            capabilities=[
                str(c) for c in (data.get("capabilities") or []) if str(c).strip()
            ],
            operations=[
                str(o) for o in (data.get("operations") or []) if str(o).strip()
            ],
            url=str(source_url or data.get("url", "") or ""),
            trust=UNTRUSTED,
            type=str(data.get("type", DEFAULT_CARD_TYPE) or DEFAULT_CARD_TYPE),
        )


class A2ADiscovery:
    """Resolves an agent card and enforces the untrusted-by-default posture."""

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def discover(self, agent_url: str) -> A2AAgentCard:
        """Fetch and parse an agent card from *agent_url*.

        Fetching a bare base URL (path ``""`` or ``"/"``) appends ``/agent``.
        A peer that refuses the connection is reported as unreachable.
        """
        parsed = urlparse(agent_url)
        if parsed.path in ("", "/"):
            agent_url = agent_url.rstrip("/") + "/agent"
        status, body = _http_json("GET", agent_url, timeout=self.timeout)
        if not _is_success(status):
            raise A2AError(
                f"expected 2xx from {agent_url}, got HTTP {status}",
                status_code=status,
            )
        payload = _unwrap_card(body)
        # Report where the agent actually lives: strip the implicit /agent
        # endpoint we appended so the card's url equals the agent's base URL.
        base_url = (
            agent_url[: -len("/agent")] if agent_url.endswith("/agent") else agent_url
        )
        return self.from_payload(payload, source_url=base_url)

    def from_payload(
        self,
        payload: dict[str, Any],
        source_url: str = "",
    ) -> A2AAgentCard:
        """Build a card from an already-fetched JSON payload (always untrusted)."""
        return A2AAgentCard.from_dict(_unwrap_card(payload), source_url=source_url)

    def approve(
        self,
        card: A2AAgentCard,
        evidence: Any,
    ) -> A2AAgentCard:
        """Promote a card to trusted ONLY when explicit approval evidence exists.

        Empty/falsy evidence keeps the card untrusted. Returns a copy; the
        original card is not mutated.
        """
        if not evidence:
            return dataclasses.replace(card, trust=UNTRUSTED)
        return dataclasses.replace(card, trust=TRUSTED)


def discover(agent_url: str, timeout: float = 10.0) -> A2AAgentCard:
    """Convenience: one-shot card discovery (untrusted unless approved)."""
    return A2ADiscovery(timeout=timeout).discover(agent_url)


def build_task_envelope(
    operation: str,
    payload: dict[str, Any],
    requester: str = "agentos",
    a2a_version: str = A2A_VERSION,
) -> dict[str, Any]:
    """Build a minimal A2A task envelope (std framing, no secrets)."""
    return {
        "a2aVersion": a2a_version,
        "operation": operation,
        "requester": requester,
        "payload": dict(payload),
        "sentAt": utc_now_iso(),
    }


def validate_task_envelope(envelope: dict[str, Any]) -> None:
    """Client-side honesty check of a task envelope before it leaves.

    Raises ``A2AError`` (400-shaped) when a required field is missing. Note
    ``submit_task`` does NOT call this: thin transports must let the remote
    peer enforce its own contract so a violation surfaces as a real 4xx.
    """
    if not isinstance(envelope, dict):
        raise A2AError("task envelope must be a JSON object", status_code=400)
    if not str(envelope.get("operation", "")).strip():
        raise A2AError(
            "missing required field 'operation' in task envelope",
            status_code=400,
        )
    if not isinstance(envelope.get("payload"), dict):
        raise A2AError(
            "missing required field 'payload' (object) in task envelope",
            status_code=400,
        )


def submit_task(
    base_url: str,
    envelope: dict[str, Any],
    timeout: float = 10.0,
) -> dict[str, Any]:
    """POST *envelope* to the peer's task queue. Returns ``{task_id, status}``.

    Thin transport: the peer decides what is well-formed. A rejected envelope
    surfaces as ``A2AError`` carrying the peer's HTTP status code.
    """
    url = base_url.rstrip("/") + "/tasks"
    status, body = _http_json("POST", url, payload=envelope, timeout=timeout)
    if not _is_success(status):
        raise A2AError(_error_text(body, status), status_code=status)
    if not isinstance(body, dict) or not str(body.get("task_id", "")).strip():
        raise A2AError(
            "peer returned malformed task acknowledgement",
            status_code=status,
        )
    return dict(body)


def poll_task(
    base_url: str,
    task_id: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """GET the peer's status/result record for *task_id*."""
    url = f"{base_url.rstrip('/')}/tasks/{task_id}"
    status, body = _http_json("GET", url, timeout=timeout)
    if not _is_success(status):
        raise A2AError(_error_text(body, status), status_code=status)
    if not isinstance(body, dict):
        raise A2AError("peer returned malformed task status", status_code=status)
    return dict(body)


def fetch_result(
    base_url: str,
    task_id: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Fetch a completed task's result artifact.

    Raises ``A2ANotDoneError`` while the task is queued/running and
    ``A2AError`` if it ended without a result (cancelled, errored).
    """
    body = poll_task(base_url, task_id, timeout=timeout)
    state = body.get("status")
    if state == "done":
        result = body.get("result")
        if not isinstance(result, dict):
            raise A2AError(
                f"task {task_id} is done but has no result artifact",
                status_code=200,
            )
        return dict(result)
    if state == "cancelled":
        raise A2AError(
            f"task {task_id} was cancelled; no result exists",
            status_code=200,
        )
    raise A2ANotDoneError(
        f"task {task_id} has no result yet (status={state!r})",
        status_code=200,
    )


def cancel_task(
    base_url: str,
    task_id: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """DELETE (cancel) a task. Raises ``A2AError`` with a 409 when already done."""
    url = f"{base_url.rstrip('/')}/tasks/{task_id}"
    status, body = _http_json("DELETE", url, timeout=timeout)
    if not _is_success(status):
        raise A2AError(_error_text(body, status), status_code=status)
    return dict(body)


def wait_for_result(
    base_url: str,
    task_id: str,
    max_attempts: int = 50,
    interval_seconds: float = 0.01,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Bounded poll until a task reaches a terminal state.

    No busy-wait: sleeps ``interval_seconds`` between polls and gives up
    after ``max_attempts``. Returns the peer's final status record.
    """
    for _ in range(max(1, max_attempts)):
        body = poll_task(base_url, task_id, timeout=timeout)
        if body.get("status") in ("done", "cancelled", "error"):
            return body
        time.sleep(max(0.0, interval_seconds))
    raise A2AError(
        f"task {task_id} did not finish within {max_attempts} bounded polls"
    )


def _unwrap_card(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise A2AInvalidCard("agent payload is not a JSON object")
    for key, kind in (("agent", dict), ("card", dict)):
        nested = payload.get(key)
        if isinstance(nested, kind):
            return dict(nested)
    return dict(payload)


def _is_success(status: int) -> bool:
    return any(lo <= status <= hi for lo, hi in [_HTTP_OK_RANGES])


def _error_text(body: dict[str, Any], status: int) -> str:
    if isinstance(body, dict) and body.get("error"):
        return f"peer rejected request (HTTP {status}): {body['error']}"
    return f"peer rejected request (HTTP {status})"


def _http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> tuple[int, Any]:
    data = (
        json.dumps(payload, default=str).encode("utf-8")
        if payload is not None
        else None
    )
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            body = json.loads(raw) if raw.strip() else {}
            return response.status, body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            body = {"error": raw or f"HTTP {exc.code}"}
        return exc.code, body
    except (urllib.error.URLError, OSError, TimeoutError, ConnectionError) as exc:
        raise A2APeerUnreachable(
            f"peer unreachable at {url} (not listening?): {type(exc).__name__}"
        ) from exc