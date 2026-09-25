"""Capability registry: registration, discovery and selection.

Discovery covers real sources:

1. AgentOS-native local capabilities (shell, filesystem, echo) backed by
   working local adapters.
2. Detected external capabilities (opencode, github, xai, grok, openclaw,
   langgraph): registered with honestly probed health. UNAVAILABLE /
   AUTH_REQUIRED is a real signal the router uses — never a fake.
3. Locally configured capabilities: user-registered CLI capabilities whose
   ``config.command`` is executed for real through the shell adapter.

Future sources plug in as new capability records plus adapters; selection
logic does not change.
"""

from __future__ import annotations

from typing import Any

from agentos.adapters import (
    Adapter,
    BrowserHarnessAdapter,
    EchoAdapter,
    FilesystemAdapter,
    GrokAdapter,
    GrokBotOfficeAdapter,
    GitHubAdapter,
    HermesAdapter,
    LangGraphAdapter,
    OpenClawAdapter,
    OpenCodeAdapter,
    OpenHandsAdapter,
    ShellAdapter,
    XAIAdapter,
)
from agentos.events import EventBus
from agentos.models import (
    READINESS_STATES,
    Capability,
    CapabilityType,
    EventType,
    HealthStatus,
    ReadinessState,
    evolve_readiness,
    readiness,
    utc_now_iso,
)
from agentos.policies import ExecutionPolicy
from agentos.store import Store

# Re-exported for consumers (later tasks import these from the registry):
# ReadinessState, READINESS_STATES, readiness, evolve_readiness.
__all__ = [
    "READINESS_STATES",
    "CapabilityRegistry",
    "ReadinessState",
    "evolve_readiness",
    "native_capabilities",
    "readiness",
]


def native_capabilities() -> list[Capability]:
    """The built-in capability catalog. Each one names a real adapter.

    External entries are *detected*, not assumed: their health comes from
    probes, and the router avoids unusable ones.
    """
    return [
        Capability(
            id="shell",
            name="Local shell",
            type=CapabilityType.CLI,
            description="Execute shell commands on the local machine with captured output and exit codes.",
            operations=["shell.run"],
            adapter="shell",
            availability=True,
            health=HealthStatus.UNKNOWN,
            source="native",
            inputs={
                "command": "shell command string to execute",
                "argv": "argument vector (no shell) as an alternative to command",
                "cwd": "optional working directory",
                "timeout": "optional timeout in seconds",
                "env": "optional extra environment variables",
                "operation_class": "standard|destructive|production|external_write|privileged",
            },
            outputs={
                "stdout": "captured standard output",
                "stderr": "captured standard error (secrets redacted)",
                "exit_code": "process exit code",
                "duration_ms": "wall-clock duration",
            },
            permissions={"executes_local_commands": True},
            constraints={"timeout_enforced": True, "policy_enforced": True},
            verification={"method": "exit_code"},
            config={},
        ),
        Capability(
            id="filesystem",
            name="Local filesystem",
            type=CapabilityType.FILESYSTEM,
            description="Read, write, stat and list files on the local filesystem.",
            operations=["fs.read", "fs.write", "fs.exists", "fs.list"],
            adapter="filesystem",
            availability=True,
            health=HealthStatus.UNKNOWN,
            source="native",
            inputs={
                "path": "file or directory path",
                "content": "text content (fs.write only)",
            },
            outputs={
                "path": "resolved path",
                "content": "file content (fs.read only)",
                "exists": "existence flag (fs.exists only)",
                "entries": "directory entries (fs.list only)",
            },
            permissions={"reads_local_files": True, "writes_local_files": True},
            verification={"method": "structural"},
            config={},
        ),
        Capability(
            id="echo",
            name="Echo",
            type=CapabilityType.TOOL,
            description="Returns the input payload unchanged. Useful for tests and demos.",
            operations=["echo"],
            adapter="echo",
            availability=True,
            health=HealthStatus.UNKNOWN,
            source="native",
            inputs={"any": "payload returned unchanged"},
            outputs={"echo": "the input payload"},
            verification={"method": "structural"},
            config={},
        ),
        Capability(
            id="opencode",
            name="OpenCode agent",
            type=CapabilityType.AGENT,
            description=(
                "Delegates software-implementation work to the OpenCode CLI "
                "(`opencode run --format json --dir <project> <prompt>`). "
                "Selected by the router for implementation-class objectives."
            ),
            operations=["opencode.run"],
            adapter="opencode",
            availability=True,
            health=HealthStatus.UNKNOWN,
            source="native",
            inputs={
                "prompt": "task prompt for the coding agent (required)",
                "project": "workspace directory to run in",
                "model": "optional provider/model override",
                "agent": "optional opencode agent override",
                "title": "optional session title",
                "files": "optional files to attach",
                "timeout": "optional timeout in seconds (default 600)",
            },
            outputs={
                "text": "agent result text",
                "exit_code": "opencode exit code",
                "duration_ms": "wall-clock duration",
            },
            latency={"note": "agent runs take minutes; prefer meaningful batches"},
            permissions={"executes_agent": True, "may_invoke_models": True},
            verification={"method": "structural"},
            config={},
        ),
        Capability(
            id="github",
            name="GitHub context",
            type=CapabilityType.REPOSITORY,
            description=(
                "Read-oriented GitHub inspection via the authenticated gh CLI: "
                "repository, branch, file, commit, issue and pull-request reads. "
                "No write operations exist — GitHub is project context, not a mutation target."
            ),
            operations=[
                "github.repo",
                "github.branch",
                "github.file",
                "github.commit",
                "github.issue",
                "github.pr",
            ],
            adapter="github",
            availability=True,
            health=HealthStatus.UNKNOWN,
            source="native",
            inputs={
                "repo": "owner/name (required)",
                "path": "file path (github.file)",
                "ref": "branch/tag/sha ref",
                "branch": "branch name (github.branch)",
                "sha": "commit sha (github.commit)",
                "number": "issue/PR number",
            },
            outputs={"data": "parsed JSON payload", "content": "decoded file text"},
            permissions={"reads_remote_repositories": True},
            verification={"method": "structural"},
            config={},
        ),
        Capability(
            id="xai",
            name="xAI Grok model",
            type=CapabilityType.MODEL,
            description=(
                "Grok as one reasoning/execution capability among many, via the "
                "official OpenAI-compatible chat-completions interface. The model "
                "must be named explicitly per call; nothing is assumed. "
                "Requires XAI_API_KEY."
            ),
            operations=["xai.chat"],
            adapter="xai",
            availability=True,
            health=HealthStatus.UNKNOWN,
            source="native",
            inputs={
                "model": "explicit model id (required)",
                "prompt": "user prompt (or messages[])",
                "messages": "explicit chat messages",
                "system": "optional system prompt",
                "temperature": "optional",
                "max_tokens": "optional",
                "tools": "optional function tools passthrough",
            },
            outputs={
                "text": "first choice text",
                "finish_reason": "completion finish reason",
                "usage": "token usage when reported",
            },
            permissions={"invokes_remote_models": True},
            verification={"method": "structural"},
            config={},
        ),
        Capability(
            id="grok",
            name="Grok Build CLI",
            type=CapabilityType.AGENT,
            description=(
                "Single-turn headless execution through the Grok CLI "
                "(`grok -p <prompt>`). Permission modes are never escalated."
            ),
            operations=["grok.run"],
            adapter="grok",
            availability=True,
            health=HealthStatus.UNKNOWN,
            source="native",
            inputs={
                "prompt": "single-turn prompt (required)",
                "cwd": "optional working directory",
                "agent": "optional agent profile",
            },
            outputs={"text": "response text", "exit_code": "exit code"},
            latency={"note": "agent runs take minutes; prefer meaningful batches"},
            permissions={"executes_agent": True, "may_invoke_models": True},
            verification={"method": "structural"},
            config={},
        ),
        Capability(
            id="openclaw",
            name="OpenClaw agent",
            type=CapabilityType.AGENT,
            description=(
                "One agent turn via the OpenClaw CLI (`openclaw agent --message "
                "<text> --json`). OpenClaw is an execution substrate, not AgentOS. "
                "Bot identities sharing an execution environment are NOT security isolation."
            ),
            operations=["openclaw.run"],
            adapter="openclaw",
            availability=True,
            health=HealthStatus.UNKNOWN,
            source="native",
            inputs={
                "message": "agent message (required)",
                "session_id": "optional durable session",
                "local": "run embedded locally (needs provider keys)",
                "agent": "optional agent id override",
                "timeout": "optional timeout in seconds (default 600)",
            },
            outputs={"text": "agent reply", "exit_code": "exit code"},
            constraints={"no_security_isolation_between_bots": True},
            permissions={"executes_agent": True},
            verification={"method": "structural"},
            config={},
        ),
        Capability(
            id="langgraph",
            name="LangGraph orchestration",
            type=CapabilityType.WORKFLOW,
            description=(
                "Reserved boundary for graph-based execution of stateful/complex "
                "orchestration. Not installed here; the router falls back to "
                "sequential execution with rationale until it is usable."
            ),
            operations=["graph.run"],
            adapter="langgraph",
            availability=True,
            health=HealthStatus.UNKNOWN,
            source="native",
            inputs={"graph": "graph specification (future)"},
            outputs={},
            verification={"method": "structural"},
            config={},
        ),
        Capability(
            id="grokbot-office",
            name="GrokBot Office orchestration",
            type=CapabilityType.AGENT,
            description=(
                "Read-only bridge to the grokbot-office control plane "
                "(`tsx src/cli.ts <cmd>`). Routes work under the GrokBot "
                "office usage governor, never mutating its usage or "
                "materialization state. Availability is probed honestly: no "
                "repository or runtime -> UNAVAILABLE."
            ),
            operations=[
                "grokbot-office.validate",
                "grokbot-office.route",
                "grokbot-office.should-create",
                "grokbot-office.allowed",
                "grokbot-office.mode",
                "grokbot-office.usage",
                "grokbot-office.roster",
                "grokbot-office.bridge-check",
            ],
            adapter="grokbot-office",
            availability=True,
            health=HealthStatus.UNKNOWN,
            source="native",
            inputs={
                "subject": "task subject for route / should-create / allowed",
            },
            outputs={
                "stdout": "captured CLI output",
                "exit_code": "process exit code",
            },
            constraints={"read_only_bridge": True},
            verification={"method": "exit_code"},
            config={},
        ),
        Capability(
            id="openhands",
            name="OpenHands executor",
            type=CapabilityType.AGENT,
            description=(
                "One OpenHands turn via the OpenHands CLI (`openhands`), "
                "executed as a real subprocess with exit-code and duration "
                "evidence. Availability probed honestly at runtime; the "
                "router never assumes a turn succeeded without measured "
                "output."
            ),
            operations=["openhands.run"],
            adapter="openhands",
            availability=True,
            health=HealthStatus.UNKNOWN,
            source="native",
            inputs={"message": "agent prompt (required)", "cwd": "optional cwd"},
            outputs={
                "text": "adapter result text",
                "exit_code": "process exit code",
            },
            permissions={"executes_agent": True, "may_invoke_models": True},
            verification={"method": "structural"},
            config={},
        ),
        Capability(
            id="hermes",
            name="Hermes executor",
            type=CapabilityType.AGENT,
            description=(
                "One Hermes turn via the Hermes CLI (`hermes`), executed as a "
                "real subprocess with exit-code and duration evidence. "
                "Availability probed honestly at runtime; never fabricated."
            ),
            operations=["hermes.run"],
            adapter="hermes",
            availability=True,
            health=HealthStatus.UNKNOWN,
            source="native",
            inputs={"message": "agent prompt (required)", "cwd": "optional cwd"},
            outputs={
                "text": "adapter result text",
                "exit_code": "process exit code",
            },
            permissions={"executes_agent": True, "may_invoke_models": True},
            verification={"method": "structural"},
            config={},
        ),
        Capability(
            id="browser-harness",
            name="Browser harness executor",
            type=CapabilityType.BROWSER,
            description=(
                "One browser-harness turn through the local headless Chrome "
                "(CDP over the bundled Node driver). Reads, navigates, clicks, "
                "types, fills forms, screenshots, downloads and uploads local "
                "files — it never reads cookies, session tokens or "
                "authentication material, and never performs destructive or "
                "authenticated writes without explicit approval evidence. "
                "Availability probed honestly via the driver; never fabricated."
            ),
            operations=["browser-harness.run"],
            adapter="browser-harness",
            availability=True,
            health=HealthStatus.UNKNOWN,
            source="native",
            inputs={
                "message": "task prompt (required)",
                "url": "page to navigate/read",
                "debugPort": "optional CDP debug port (default 9222)",
            },
            outputs={
                "text": "page text captured from the browser",
                "screenshot": "screenshot path when requested",
            },
            permissions={"executes_browser": True},
            constraints={"no_credential_reads": True, "approval_gated_writes": True},
            verification={"method": "structural"},
            config={},
        ),
    ]


class CapabilityRegistry:
    """Owns adapters, registers capabilities, discovers and selects them."""

    def __init__(
        self,
        store: Store,
        bus: EventBus,
        config: dict[str, Any] | None = None,
        policy: ExecutionPolicy | None = None,
    ) -> None:
        self.store = store
        self.bus = bus
        self.config = dict(config or {})
        self.policy = policy or ExecutionPolicy()
        adapter_config = self.config.get("adapters", {})
        self.adapters: dict[str, Adapter] = {
            ShellAdapter.name: ShellAdapter(policy=self.policy),
            FilesystemAdapter.name: FilesystemAdapter(policy=self.policy),
            EchoAdapter.name: EchoAdapter(),
            OpenCodeAdapter.name: OpenCodeAdapter(adapter_config.get("opencode")),
            GitHubAdapter.name: GitHubAdapter(adapter_config.get("github")),
            XAIAdapter.name: XAIAdapter(adapter_config.get("xai")),
            GrokAdapter.name: GrokAdapter(adapter_config.get("grok")),
            OpenClawAdapter.name: OpenClawAdapter(adapter_config.get("openclaw")),
            OpenHandsAdapter.name: OpenHandsAdapter(adapter_config.get("openhands")),
            HermesAdapter.name: HermesAdapter(adapter_config.get("hermes")),
            BrowserHarnessAdapter.name: BrowserHarnessAdapter(
                adapter_config.get("browser-harness")
            ),
            LangGraphAdapter.name: LangGraphAdapter(adapter_config.get("langgraph")),
            GrokBotOfficeAdapter.name: GrokBotOfficeAdapter(
                adapter_config.get("grokbot-office")
            ),
        }

    # ------------------------------------------------------------------
    # adapter boundary
    # ------------------------------------------------------------------
    def adapter_for(self, capability: Capability) -> Adapter | None:
        return self.adapters.get(capability.adapter)

    def register_adapter(self, adapter: Adapter) -> None:
        """Extension point: plug in a new adapter without touching the core."""
        self.adapters[adapter.name] = adapter

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------
    def register(self, capability: Capability) -> Capability:
        existing = self.store.get_capability(capability.id)
        self.store.save_capability(capability)
        self.bus.emit(
            EventType.CAPABILITY_REGISTERED,
            payload={"capability_id": capability.id, "new": existing is None},
        )
        return capability

    def remove(self, capability_id: str) -> bool:
        removed = self.store.delete_capability(capability_id)
        if removed:
            self.bus.emit(
                EventType.CAPABILITY_REMOVED,
                payload={"capability_id": capability_id},
            )
        return removed

    # ------------------------------------------------------------------
    # discovery
    # ------------------------------------------------------------------
    def discover(self, refresh_health: bool = False) -> list[Capability]:
        """Ensure native capabilities are registered; probe health on demand.

        Returns the full known capability list (native + configured).
        """
        known = {c.id: c for c in self.store.list_capabilities()}
        for native in native_capabilities():
            if native.id not in known:
                self.store.save_capability(native)
                self.bus.emit(
                    EventType.CAPABILITY_DISCOVERED,
                    payload={
                        "capability_id": native.id,
                        "source": "native",
                        "operations": native.operations,
                    },
                )
                known[native.id] = native
        capabilities = self.store.list_capabilities()
        if refresh_health:
            for capability in capabilities:
                self.refresh_health(capability)
        return capabilities

    def refresh_health(self, capability: Capability) -> Capability:
        """Probe one capability and persist its health + readiness + probe time.

        Single probe per call: the measured outcome feeds both ``health``
        and ``readiness`` (via ``models.readiness`` with the adapter's
        binary executability). Bounded by the adapter probe contract.
        """
        health = self.probe(capability)
        capability.health = health
        capability.readiness = readiness(self.adapter_for(capability), health)
        capability.last_probe = utc_now_iso()
        self.store.save_capability(capability)
        return capability

    def probe(self, capability: Capability) -> HealthStatus:
        adapter = self.adapter_for(capability)
        if adapter is None:
            return HealthStatus.UNKNOWN
        probe = getattr(adapter, "probe", None)
        if probe is None:
            return HealthStatus.UNKNOWN
        try:
            result = probe()
        except Exception:
            return HealthStatus.DOWN
        if isinstance(result, HealthStatus):
            return result
        try:
            return HealthStatus.OK if bool(result) else HealthStatus.DOWN
        except Exception:
            return HealthStatus.DOWN

    # ------------------------------------------------------------------
    # execution evidence ledger (lives ON the Capability — single source)
    # ------------------------------------------------------------------
    def record_execution(
        self,
        capability_id: str,
        ok: bool,
        error: str | None = None,
        duration_ms: float | None = None,
    ) -> Capability:
        """Persist one execution attempt on the capability ledger.

        Every attempt is recorded: counters, last success/failure
        timestamps, running average duration, last error, and readiness
        evolution (success is executable evidence -> READY; failure
        demotes toward DEGRADED/FAILED or records AUTH/CONFIG signals).
        Raises KeyError for unknown capability ids — attempts are never
        silently dropped.
        """
        capability = self.store.get_capability(capability_id)
        if capability is None:
            raise KeyError(f"unknown capability: {capability_id}")
        capability.execution_count += 1
        if ok:
            capability.success_count += 1
            capability.last_success = utc_now_iso()
            capability.readiness = evolve_readiness(
                capability.readiness, "executed ok"
            )
        else:
            capability.failure_count += 1
            capability.last_failed = utc_now_iso()
            if error is not None:
                capability.last_error = str(error)
            signal = f"failed: {error}" if error else "failed"
            capability.readiness = evolve_readiness(capability.readiness, signal)
        if duration_ms is not None:
            try:
                sample = float(duration_ms)
            except (TypeError, ValueError):
                sample = -1.0
            if sample >= 0:
                previous = capability.execution_count - 1
                prior_avg = capability.avg_duration_ms or 0.0
                capability.avg_duration_ms = (
                    prior_avg * previous + sample
                ) / capability.execution_count
        self.store.save_capability(capability)
        return capability

    # ------------------------------------------------------------------
    # selection
    # ------------------------------------------------------------------
    def select(self, operation: str) -> Capability | None:
        """Pick the best usable capability for an operation.

        Deterministic: usable (available, not down/unavailable/disabled)
        first, healthy before unknown, then stable id order. Health UNKNOWN
        is treated as usable until a probe says otherwise.
        """
        candidates = [
            c
            for c in self.store.list_capabilities()
            if c.supports(operation) and c.availability
        ]
        if not candidates:
            return None
        usable = [c for c in candidates if c.is_usable()]
        pool = usable or candidates
        pool.sort(
            key=lambda c: (
                0
                if c.health in (HealthStatus.AVAILABLE, HealthStatus.OK)
                else 1,
                c.id,
            )
        )
        return pool[0]

    # ------------------------------------------------------------------
    # configured (user-registered) CLI capabilities
    # ------------------------------------------------------------------
    def register_configured_cli(
        self,
        capability_id: str,
        name: str,
        command: str,
        description: str = "",
        verify_method: str = "exit_code",
        verify_expected: str | None = None,
    ) -> Capability:
        """Register a real locally-configured CLI capability.

        The command is executed for real through the shell adapter on every
        run; nothing is faked. ``verify_expected`` adds a ``contains`` check
        on top of the exit-code check.
        """
        operation = f"{capability_id}.run"
        verification: dict[str, object] = {"method": verify_method}
        if verify_expected is not None:
            verification["expected"] = verify_expected
        capability = Capability(
            id=capability_id,
            name=name,
            type=CapabilityType.CLI,
            description=description or f"Configured local command: {command}",
            operations=[operation],
            adapter="shell",
            availability=True,
            health=HealthStatus.UNKNOWN,
            source="configured",
            inputs={"params": "optional params merged into the command environment"},
            outputs={"stdout": "captured output", "exit_code": "process exit code"},
            permissions={"executes_local_commands": True},
            verification=verification,
            config={"command": command},
        )
        return self.register(capability)