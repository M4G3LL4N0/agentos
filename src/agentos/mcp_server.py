"""AgentOS as MCP infrastructure: a stdio JSON-RPC MCP server (stdlib only).

An external AI connects AgentOS as an execution/control capability over the
Model Context Protocol (newline-delimited JSON-RPC 2.0 on stdio):

- ``initialize`` / ``notifications/initialized`` / ``ping``
- ``tools/list`` — the AgentOS tool surface
- ``tools/call`` — every call routes into the same ``services.AgentOS``
  methods the CLI uses. No duplicated business logic.

Configure any MCP client with::

    {"command": "agentos", "args": ["mcp"], "env": {"AGENTOS_HOME": "..."}}

Tools: status, state (dashboard), capabilities, agents, objectives,
create_objective, inspect, run, delegate, verify, recover, events,
plans, patterns, gaps.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from agentos import __version__
from agentos.engine import AgentOSError
from agentos.services import AgentOS

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agentos"
SERVER_VERSION = __version__

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "status",
        "description": "AgentOS engine + state summary (counts, objectives by state, capabilities).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "state",
        "description": "Operational dashboard: active objectives, stuck work, current strategy, capabilities, recent events, pending approvals.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "capabilities",
        "description": "List capabilities AgentOS can use, with operations, health and adapters.",
        "inputSchema": {
            "type": "object",
            "properties": {"refresh": {"type": "boolean", "description": "re-probe health"}},
        },
    },
    {
        "name": "agents",
        "description": "List agent/model capabilities (may be empty until real ones are registered).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "objectives",
        "description": "List objectives, optionally filtered by state.",
        "inputSchema": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
        },
    },
    {
        "name": "create_objective",
        "description": "Create an objective: title plus optional description, operation, params, constraints, priority, parent.",
        "inputSchema": {
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "operation": {"type": "string"},
                "params": {"type": "object"},
                "constraints": {"type": "array", "items": {"type": "string"}},
                "priority": {"type": "string"},
                "parent_id": {"type": ["string", "null"]},
                "context": {"type": "object"},
            },
        },
    },
    {
        "name": "inspect",
        "description": "Full detail for an objective: executions, verifications, failures, evidence, events.",
        "inputSchema": {
            "type": "object",
            "required": ["objective_id"],
            "properties": {"objective_id": {"type": "string"}},
        },
    },
    {
        "name": "run",
        "description": "Execute an objective through the engine (discover, select, execute, verify, recover).",
        "inputSchema": {
            "type": "object",
            "required": ["objective_id"],
            "properties": {
                "objective_id": {"type": "string"},
                "operation": {"type": ["string", "null"]},
                "max_attempts": {"type": ["integer", "null"]},
                "force": {"type": "boolean"},
            },
        },
    },
    {
        "name": "delegate",
        "description": "Express an objective and let AgentOS handle orchestration (create + run). Use this to say: execute this objective using available capabilities.",
        "inputSchema": {
            "type": "object",
            "required": ["title"],
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "operation": {"type": ["string", "null"]},
                "params": {"type": "object"},
                "constraints": {"type": "array", "items": {"type": "string"}},
                "priority": {"type": "string"},
                "max_attempts": {"type": ["integer", "null"]},
            },
        },
    },
    {
        "name": "verify",
        "description": "Re-verify the latest execution of an objective.",
        "inputSchema": {
            "type": "object",
            "required": ["objective_id"],
            "properties": {"objective_id": {"type": "string"}},
        },
    },
    {
        "name": "recover",
        "description": "Recover a FAILED or BLOCKED objective with bounded retries.",
        "inputSchema": {
            "type": "object",
            "required": ["objective_id"],
            "properties": {
                "objective_id": {"type": "string"},
                "max_attempts": {"type": ["integer", "null"]},
            },
        },
    },
    {
        "name": "events",
        "description": "Recorded events, optionally filtered by objective.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "objective_id": {"type": ["string", "null"]},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "plans",
        "description": "Execution plans with strategy, steps, roles and rationale.",
        "inputSchema": {
            "type": "object",
            "properties": {"objective_id": {"type": ["string", "null"]}},
        },
    },
    {
        "name": "patterns",
        "description": "Learned execution patterns (verified outcomes only).",
        "inputSchema": {
            "type": "object",
            "properties": {"objective_class": {"type": ["string", "null"]}},
        },
    },
    {
        "name": "gaps",
        "description": "Capability-gap analyses for unsupported objectives.",
        "inputSchema": {
            "type": "object",
            "properties": {"objective_id": {"type": ["string", "null"]}},
        },
    },
]


class MCPError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _text_result(payload: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}]
    }


def call_tool(app: AgentOS, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Route one MCP tool call into the shared service core."""
    try:
        if name == "status":
            return _text_result(app.status())
        if name == "state":
            return _text_result(app.dashboard())
        if name == "capabilities":
            return _text_result(
                [c.to_dict() for c in app.capabilities(refresh=bool(args.get("refresh", False)))]
            )
        if name == "agents":
            return _text_result([c.to_dict() for c in app.agents()])
        if name == "objectives":
            return _text_result(
                [o.to_dict() for o in app.list_objectives(status=args.get("status"))]
            )
        if name == "create_objective":
            if not args.get("title"):
                raise MCPError(-32602, "create_objective requires 'title'")
            objective = app.create_objective(
                str(args["title"]),
                description=str(args.get("description", "")),
                priority=str(args.get("priority", "MEDIUM")),
                operation=args.get("operation"),
                params=args.get("params"),
                constraints=args.get("constraints"),
                parent_id=args.get("parent_id"),
                extra_context=args.get("context"),
            )
            return _text_result(objective.to_dict())
        if name == "inspect":
            return _text_result(app.inspect(str(args["objective_id"])))
        if name == "run":
            report = app.run(
                str(args["objective_id"]),
                operation=args.get("operation"),
                max_attempts=args.get("max_attempts"),
                force=bool(args.get("force", False)),
            )
            return _text_result(report.to_dict())
        if name == "delegate":
            if not args.get("title"):
                raise MCPError(-32602, "delegate requires 'title'")
            report = app.delegate(
                str(args["title"]),
                description=str(args.get("description", "")),
                operation=args.get("operation"),
                params=args.get("params"),
                constraints=args.get("constraints"),
                priority=str(args.get("priority", "MEDIUM")),
                max_attempts=args.get("max_attempts"),
            )
            return _text_result(report.to_dict())
        if name == "verify":
            return _text_result(app.verify(str(args["objective_id"])).to_dict())
        if name == "recover":
            report = app.recover(
                str(args["objective_id"]), max_attempts=args.get("max_attempts")
            )
            return _text_result(report.to_dict())
        if name == "events":
            return _text_result(
                app.events(
                    objective_id=args.get("objective_id"),
                    limit=int(args.get("limit", 50)),
                )
            )
        if name == "plans":
            return _text_result(app.plans(args.get("objective_id")))
        if name == "patterns":
            return _text_result(app.patterns(args.get("objective_class")))
        if name == "gaps":
            return _text_result(app.gaps(args.get("objective_id")))
    except AgentOSError as exc:
        raise MCPError(-32000, str(exc))
    raise MCPError(-32601, f"unknown tool {name!r}")


def handle_message(app: AgentOS, message: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC message; returns a response or None (notification)."""
    msg_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    def _response(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _error(code: int, text: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}

    try:
        if method == "initialize":
            return _response(
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                }
            )
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "ping":
            return _response({})
        if method == "tools/list":
            return _response({"tools": TOOL_DEFS})
        if method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments") or {}
            if not isinstance(tool_args, dict):
                return _error(-32602, "tool arguments must be an object")
            try:
                return _response(call_tool(app, tool_name, tool_args))
            except MCPError as exc:
                return _error(exc.code, exc.message)
        return _error(-32601, f"unknown method {method!r}")
    except Exception as exc:  # protocol must never die on a bad message
        if msg_id is None:
            return None
        return _error(-32603, f"{type(exc).__name__}: {exc}")


def serve_stdio(app: AgentOS) -> int:
    """Serve MCP over stdio until EOF. Logs go to stderr, never stdout."""
    stdin = sys.stdin
    stdout = sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        response = handle_message(app, message)
        if response is not None:
            stdout.write(json.dumps(response, default=str) + "\n")
            stdout.flush()
    return 0
