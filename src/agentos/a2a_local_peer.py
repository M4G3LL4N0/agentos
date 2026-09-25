"""A2A loopback test peer (Python stdlib only).

A tiny ``http.server``-based service bound to 127.0.0.1 on an ephemeral port.
It advertises an agent card, accepts tasks, executes them for real (an
iterated SHA-256 digest of the envelope, with real measured wall time), and
reports an honest per-task state machine: ``queued -> running -> done`` or
``queued/running -> cancelled``. Cancelling a task that already finished is
an honest 409. Latency is never faked: the only delay is the peer's own real
computation.
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from agentos.a2a import A2AAgentCard, UNTRUSTED

DEFAULT_NAME = "a2a-test-peer"
DEFAULT_VERSION = "0.5.0"
DEFAULT_OPERATION = "a2a.test"
DEFAULT_CARD_TYPE = "test-peer"

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"

#: Local state -> A2A v1.0 TASK_STATE_* mapping (honest, no invention).
V1_STATE_MAP = {
    STATUS_QUEUED: "SUBMITTED",
    STATUS_RUNNING: "WORKING",
    STATUS_DONE: "COMPLETED",
    STATUS_CANCELLED: "CANCELED",
}

DEFAULT_WORK_ROUNDS = 20_000
MAX_WORK_ROUNDS = 1_000_000
MAX_BODY_BYTES = 1_000_000

#: JSON-RPC error codes served by the v1 surface (stable A2A v1.0 codes).
_V1_PARSE_ERROR = -32700
_V1_INVALID_PARAMS = -32602
_V1_METHOD_NOT_FOUND = -32601
_V1_TASK_NOT_FOUND = -32001
_V1_TASK_NOT_CANCELABLE = -32002
_V1_UNSUPPORTED_OPERATION = -32004
_V1_VERSION_NOT_SUPPORTED = -32009


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _task_id() -> str:
    return f"task_{uuid4().hex[:12]}"


class A2ALocalPeer:
    """Lifecycle of a single loopback A2A peer with one worker thread."""

    def __init__(
        self,
        port: int = 0,
        name: str = DEFAULT_NAME,
        version: str = DEFAULT_VERSION,
        operation: str = DEFAULT_OPERATION,
        host: str = "127.0.0.1",
    ) -> None:
        self.port = port
        self.name = name
        self.version = version
        self.operation = operation
        self.host = host
        self.base_url = ""
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._queue: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()
        self._httpd: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._worker: threading.Thread | None = None

    def agent_card(self) -> A2AAgentCard:
        return A2AAgentCard(
            name=self.name,
            version=self.version,
            description=(
                f"loopback A2A test peer advertising operation {self.operation!r}"
            ),
            capabilities=[self.operation],
            operations=[self.operation],
            url=self.base_url,
            trust=UNTRUSTED,
            type=DEFAULT_CARD_TYPE,
        )

    # ---------------- peer control -----------------------------------

    def start(self) -> str:
        if self._httpd is not None:
            return self.base_url
        httpd = ThreadingHTTPServer((self.host, self.port), _PeerHandler)
        httpd.daemon_threads = True
        httpd.peer = self  # type: ignore[attr-defined]
        self._httpd = httpd
        self.base_url = f"http://{httpd.server_address[0]}:{httpd.server_address[1]}"
        self._server_thread = threading.Thread(
            target=httpd.serve_forever,
            name=f"a2a-peer-{self.name}",
            daemon=True,
        )
        self._server_thread.start()
        self._worker = threading.Thread(
            target=self._drain_queue,
            name=f"a2a-peer-{self.name}-worker",
            daemon=True,
        )
        self._worker.start()
        return self.base_url

    def stop(self) -> None:
        self._queue.put(None)
        if self._worker is not None:
            self._worker.join(timeout=5.0)
            self._worker = None
        httpd = self._httpd
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if self._server_thread is not None:
            self._server_thread.join(timeout=5.0)
            self._server_thread = None
        self._httpd = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    # ---------------- task handling -----------------------------------

    def enqueue(self, envelope: dict[str, Any]) -> dict[str, Any]:
        task = {
            "task_id": _task_id(),
            "status": STATUS_QUEUED,
            "received_at": _utc_now(),
            "envelope": dict(envelope),
            "cancel_requested": False,
            "result": None,
            "error": None,
            "cancelled_at": None,
            "completed_at": None,
        }
        with self._lock:
            self._tasks[task["task_id"]] = task
        self._queue.put((task["task_id"], envelope))
        return {"task_id": task["task_id"], "status": STATUS_QUEUED}

    def status_of(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            return {
                "task_id": task_id,
                "status": task["status"],
                "result": None if task["result"] is None else dict(task["result"]),
                "error": task["error"],
                "received_at": task["received_at"],
                "completed_at": task["completed_at"],
                "cancelled_at": task["cancelled_at"],
            }

    def cancel(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return {"status": 404, "body": {"error": f"unknown task {task_id}"}}
            if task["status"] == STATUS_DONE:
                return {
                    "status": 409,
                    "body": {
                        "error": (f"task {task_id} already done; cannot cancel"),
                        "task_id": task_id,
                        "status": task["status"],
                    },
                }
            task["status"] = STATUS_CANCELLED
            task["cancel_requested"] = True
            task["cancelled_at"] = _utc_now()
            return {
                "status": 200,
                "body": {
                    "task_id": task_id,
                    "status": STATUS_CANCELLED,
                    "cancelled_at": task["cancelled_at"],
                },
            }

    # ---------------- real execution ----------------------------------

    def _drain_queue(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            task_id, envelope = item
            with self._lock:
                task = self._tasks.get(task_id)
                if task is None or task["cancel_requested"]:
                    continue
                task["status"] = STATUS_RUNNING
            result = self._execute(task_id, envelope)
            with self._lock:
                task = self._tasks.get(task_id)
                if task is not None and task["status"] == STATUS_RUNNING:
                    task["status"] = STATUS_DONE
                    task["result"] = result
                    task["completed_at"] = result["completed_at"]

    def _execute(self, task_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        payload = envelope.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        raw_work = payload.get("work")
        try:
            work = int(raw_work) if raw_work is not None else DEFAULT_WORK_ROUNDS
        except (TypeError, ValueError):
            work = DEFAULT_WORK_ROUNDS
        work = max(0, min(work, MAX_WORK_ROUNDS))

        started = time.monotonic()
        digest = hashlib.sha256(
            json.dumps(envelope, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        for _ in range(work):
            digest = hashlib.sha256(digest.encode("utf-8")).hexdigest()
        duration_ms = round((time.monotonic() - started) * 1000.0, 3)

        received_at = _utc_now()
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                received_at = task["received_at"]
        completed_at = _utc_now()
        return {
            "status": STATUS_DONE,
            "echo": dict(payload),
            "digest": digest,
            "digest_rounds": work,
            "duration_ms": duration_ms,
            "received_at": received_at,
            "completed_at": completed_at,
            "operation": str(envelope.get("operation", "")),
            "task_id": task_id,
            "worker": self.name,
        }


def start_peer(
    port: int = 0,
    name: str = DEFAULT_NAME,
    version: str = DEFAULT_VERSION,
    operation: str = DEFAULT_OPERATION,
) -> tuple[threading.Thread, str]:
    """Start a loopback A2A test peer on an ephemeral (default) port.

    Returns ``(httpd_thread, base_url)``. Stop it with :func:`stop_peer` or
    ``thread.peer.stop()``; both are idempotent and close the socket.
    """
    peer = A2ALocalPeer(
        port=port,
        name=name,
        version=version,
        operation=operation,
    )
    base_url = peer.start()
    thread = peer._server_thread  # type: ignore[assignment]
    thread.peer = peer  # type: ignore[attr-defined]
    thread.httpd = peer._httpd  # type: ignore[attr-defined]
    return thread, base_url


def stop_peer(thread: threading.Thread) -> None:
    """Cleanly shut down a peer started with :func:`start_peer`."""
    peer = getattr(thread, "peer", None)
    if peer is not None:
        peer.stop()


class _PeerHandler(BaseHTTPRequestHandler):
    server_version = "AgentOS-A2A-Peer/1.0"
    protocol_version = "HTTP/1.0"

    @property
    def peer(self) -> A2ALocalPeer:  # type: ignore[override]
        return self.server.peer  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    # ---------------- plumbing -----------------------------------------

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            return {}
        try:
            raw = self.rfile.read(length).decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return {}
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return {}
        return body if isinstance(body, dict) else {}

    # ---------------- routing ------------------------------------------

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/.well-known/agent-card.json":
                self._serve_v1_card()
                return
            if path in ("", "/"):
                peer = self.peer
                self._send(
                    200,
                    {
                        "name": peer.name,
                        "version": peer.version,
                        "description": "loopback A2A test peer",
                    },
                )
                return
            if path == "/agent":
                self._send(200, self.peer.agent_card().to_dict())
                return
            if path.startswith("/tasks/"):
                task_id = path[len("/tasks/") :].split("/")[0]
                record = self.peer.status_of(task_id)
                if record is None:
                    self._send(404, {"error": f"unknown task {task_id}"})
                    return
                self._send(200, record)
                return
            self._send(404, {"error": "not found"})
        except Exception as exc:
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/":
                self._serve_v1_rpc()
                return
            if path != "/tasks":
                self._send(404, {"error": "not found"})
                return
            envelope = self._read_body()
            if not envelope:
                self._send(400, {"error": "request body must be a JSON object"})
                return
            operation = str(envelope.get("operation", "") or "").strip()
            if not operation:
                self._send(
                    400,
                    {"error": "missing required field 'operation' in task envelope"},
                )
                return
            if operation != self.peer.operation:
                self._send(
                    422,
                    {
                        "error": (
                            f"unsupported operation {operation!r}; "
                            f"peer advertises {self.peer.operation!r}"
                        )
                    },
                )
                return
            if not isinstance(envelope.get("payload"), dict):
                self._send(
                    400,
                    {"error": "missing required field 'payload' (object)"},
                )
                return
            ack = self.peer.enqueue(envelope)
            self._send(201, ack)
        except Exception as exc:
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    # ---------------- A2A v1.0 surface ---------------------------------
    # Canonical stable behavior only (JSON-RPC binding). This peer speaks
    # protocol 1.0 exclusively: any other A2A-Version is an honest
    # VersionNotSupported, never a silent downgrade to 0.x.

    def _v1_version_ok(self) -> str | None:
        """Return an error string when the request is not v1.0, else None."""
        version = (self.headers.get("A2A-Version") or "").strip()
        if version != "1.0":
            return (
                "VersionNotSupported: this peer speaks A2A 1.0 only "
                f"(got {version!r})"
            )
        return None

    def _serve_v1_card(self) -> None:
        problem = self._v1_version_ok()
        if problem:
            self._send(400, {"error": problem})
            return
        peer = self.peer
        self._send(
            200,
            {
                "name": peer.name,
                "description": (
                    "loopback A2A v1.0 test peer "
                    f"advertising skill {peer.operation!r}"
                ),
                "version": peer.version,
                "supportedInterfaces": [
                    {
                        "url": peer.base_url.rstrip("/") + "/",
                        "protocolBinding": "JSONRPC",
                        "protocolVersion": "1.0",
                    }
                ],
                "capabilities": {
                    "streaming": False,
                    "pushNotifications": False,
                    "extendedAgentCard": False,
                },
                "defaultInputModes": ["text"],
                "defaultOutputModes": ["text"],
                "skills": [
                    {
                        "id": peer.operation,
                        "name": peer.operation,
                        "description": "Deterministic digest worker (read-only).",
                        "tags": ["test"],
                    }
                ],
            },
        )

    def _rpc_result(self, rpc_id: Any, result: dict[str, Any]) -> None:
        self._send(200, {"jsonrpc": "2.0", "id": rpc_id, "result": result})

    def _rpc_error(self, rpc_id: Any, code: int, message: str) -> None:
        self._send(
            200, {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
        )

    def _v1_task_record(self, task_id: str) -> dict[str, Any] | None:
        record = self.peer.status_of(task_id)
        if record is None:
            return None
        out: dict[str, Any] = {
            "id": task_id,
            "contextId": "",
            "status": {
                "state": V1_STATE_MAP.get(record["status"], "SUBMITTED"),
                "timestamp": record.get("completed_at")
                or record.get("cancelled_at")
                or record.get("received_at"),
            },
        }
        if record["status"] == STATUS_DONE and isinstance(record.get("result"), dict):
            out["artifacts"] = [
                {"name": "result", "parts": [{"data": record["result"]}]}
            ]
        return out

    def _serve_v1_rpc(self) -> None:
        body = self._read_body()
        rpc_id = body.get("id") if isinstance(body, dict) else None
        problem = self._v1_version_ok()
        if problem:
            self._rpc_error(rpc_id, _V1_VERSION_NOT_SUPPORTED, problem)
            return
        if (
            not isinstance(body, dict)
            or body.get("jsonrpc") != "2.0"
            or not isinstance(body.get("method"), str)
        ):
            self._rpc_error(rpc_id, _V1_PARSE_ERROR, "malformed JSON-RPC envelope")
            return
        method = body["method"]
        params = body.get("params")
        if not isinstance(params, dict):
            params = {}
        if method == "SendMessage":
            message = params.get("message")
            if not isinstance(message, dict) or not isinstance(
                message.get("parts"), list
            ):
                self._rpc_error(
                    rpc_id, _V1_INVALID_PARAMS, "SendMessage requires message.parts"
                )
                return
            config = params.get("configuration") or {}
            if isinstance(config, dict) and config.get("streaming"):
                self._rpc_error(
                    rpc_id,
                    _V1_UNSUPPORTED_OPERATION,
                    "streaming is not offered by this peer",
                )
                return
            metadata = params.get("metadata") or {}
            skill = str(metadata.get("skill", "") or "").strip() if isinstance(
                metadata, dict
            ) else ""
            if skill != self.peer.operation:
                self._rpc_error(
                    rpc_id,
                    _V1_UNSUPPORTED_OPERATION,
                    f"unsupported skill {skill!r}; peer offers {self.peer.operation!r}",
                )
                return
            text = " ".join(
                str(part.get("text", ""))
                for part in message["parts"]
                if isinstance(part, dict)
            )
            ack = self.peer.enqueue(
                {"operation": self.peer.operation, "payload": {"message": text}}
            )
            self._rpc_result(
                rpc_id,
                {
                    "id": ack["task_id"],
                    "contextId": "",
                    "status": {"state": "SUBMITTED", "timestamp": _utc_now()},
                },
            )
            return
        if method == "GetTask":
            task_id = str(params.get("id", "") or "")
            record = self._v1_task_record(task_id)
            if record is None:
                self._rpc_error(
                    rpc_id, _V1_TASK_NOT_FOUND, f"unknown task {task_id}"
                )
                return
            self._rpc_result(rpc_id, record)
            return
        if method == "CancelTask":
            task_id = str(params.get("id", "") or "")
            outcome = self.peer.cancel(task_id)
            if outcome["status"] == 404:
                self._rpc_error(
                    rpc_id, _V1_TASK_NOT_FOUND, f"unknown task {task_id}"
                )
                return
            if outcome["status"] == 409:
                self._rpc_error(
                    rpc_id,
                    _V1_TASK_NOT_CANCELABLE,
                    f"task {task_id} already done; cannot cancel",
                )
                return
            record = self._v1_task_record(task_id)
            self._rpc_result(
                rpc_id,
                record
                or {"id": task_id, "contextId": "", "status": {"state": "CANCELED"}},
            )
            return
        self._rpc_error(rpc_id, _V1_METHOD_NOT_FOUND, f"unknown method {method!r}")

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        try:
            if not path.startswith("/tasks/"):
                self._send(404, {"error": "not found"})
                return
            task_id = path[len("/tasks/") :].split("/")[0]
            outcome = self.peer.cancel(task_id)
            self._send(outcome["status"], outcome["body"])
        except Exception as exc:
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})