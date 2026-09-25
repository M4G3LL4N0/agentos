"""xAI API adapter: Grok as one reasoning/execution capability among many.

Uses the official OpenAI-compatible interface (``POST {base}/chat/completions``,
default base ``https://api.x.ai/v1``). No model name is assumed: the caller
must provide ``model`` explicitly (param or adapter config). Without an API
key the adapter reports AUTH_REQUIRED and every execution fails honestly —
nothing is fabricated.

Tool/function calling passes through ``tools``/``tool_choice`` verbatim;
MCP connectivity on the xAI side is out of scope for this adapter.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from agentos.adapters.base import AdapterResult, ExecutionRequest
from agentos.models import Evidence, HealthStatus
from agentos.policies import redact_secrets
from agentos.safety import gate_allows

CHAT_OPERATION = "xai.chat"
DEFAULT_BASE_URL = "https://api.x.ai/v1"


class XAIAdapter:
    """Invokes xAI chat-completions for reasoning/execution tasks."""

    name = "xai"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    def api_key(self) -> str | None:
        return (
            self.config.get("api_key")
            or os.environ.get("XAI_API_KEY")
            or os.environ.get("XAI_KEY")
        )

    def base_url(self) -> str:
        return str(
            self.config.get("base_url")
            or os.environ.get("XAI_BASE_URL")
            or DEFAULT_BASE_URL
        ).rstrip("/")

    def execute(self, operation: str, request: ExecutionRequest) -> AdapterResult:
        allowed, reason = gate_allows(request.mode, request.authorization)
        if not allowed:
            return AdapterResult(
                ok=False,
                error=reason,
                evidence=[
                    Evidence(kind="policy_block", detail=reason, source="xai")
                ],
            )
        if operation != CHAT_OPERATION:
            return AdapterResult(
                ok=False,
                error=f"xai adapter does not support operation {operation!r}",
                evidence=[
                    Evidence(
                        kind="unsupported_operation",
                        detail=f"xai adapter received {operation!r}",
                        source="xai",
                    )
                ],
            )
        key = self.api_key()
        if not key:
            return AdapterResult(
                ok=False,
                error="xAI API key missing (set XAI_API_KEY); adapter is AUTH_REQUIRED",
                evidence=[
                    Evidence(
                        kind="auth_required",
                        detail="no XAI_API_KEY available",
                        source="xai",
                    )
                ],
            )
        params = request.params
        model = params.get("model") or self.config.get("model")
        if not model:
            return AdapterResult(
                ok=False,
                error="xai.chat requires an explicit 'model' param (no default assumed)",
                evidence=[
                    Evidence(
                        kind="missing_input",
                        detail="no model specified; AgentOS does not assume a model",
                        source="xai",
                    )
                ],
            )
        prompt = params.get("prompt") or params.get("message")
        messages = params.get("messages")
        if messages is None:
            if not prompt or not str(prompt).strip():
                return AdapterResult(
                    ok=False,
                    error="xai.chat requires 'prompt' (or 'messages')",
                    evidence=[
                        Evidence(
                            kind="missing_input", detail="no prompt provided", source="xai"
                        )
                    ],
                )
            system = params.get("system")
            messages = (
                [{"role": "system", "content": str(system)}, {"role": "user", "content": str(prompt)}]
                if system
                else [{"role": "user", "content": str(prompt)}]
            )
        body: dict[str, Any] = {"model": str(model), "messages": messages}
        for key_name in ("temperature", "max_tokens", "tools", "tool_choice", "response_format"):
            if params.get(key_name) is not None:
                body[key_name] = params[key_name]
        timeout = float(
            params.get("timeout_seconds")
            or params.get("timeout")
            or self.config.get("timeout_seconds", 120)
            or 120
        )
        data = json.dumps(body).encode("utf-8")
        http_request = urllib.request.Request(
            f"{self.base_url()}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            if exc.code in (401, 403):
                return AdapterResult(
                    ok=False,
                    error=f"xAI auth failed (HTTP {exc.code}); check XAI_API_KEY",
                    evidence=[
                        Evidence(
                            kind="auth",
                            detail=f"HTTP {exc.code}: {redact_secrets(detail)}",
                            source="xai",
                        )
                    ],
                )
            retryable = exc.code == 429 or 500 <= exc.code < 600
            return AdapterResult(
                ok=False,
                error=f"xAI request failed (HTTP {exc.code}): {detail}",
                evidence=[
                    Evidence(
                        kind="connectivity" if retryable else "execution",
                        detail=f"HTTP {exc.code}",
                        source="xai",
                    )
                ],
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return AdapterResult(
                ok=False,
                error=f"xAI request failed: {exc}",
                evidence=[
                    Evidence(kind="connectivity", detail=str(exc), source="xai")
                ],
            )
        try:
            choice = (payload.get("choices") or [])[0]
            message = choice.get("message", {})
            text = message.get("content") or ""
            output = {
                "text": text,
                "finish_reason": choice.get("finish_reason"),
                "tool_calls": message.get("tool_calls"),
                "usage": payload.get("usage"),
                "model": payload.get("model", model),
                "exit_code": 0,
            }
        except (IndexError, AttributeError) as exc:
            return AdapterResult(
                ok=False,
                error=f"xAI returned an unparseable payload: {exc}",
                evidence=[
                    Evidence(kind="execution", detail="unparseable payload", source="xai")
                ],
            )
        if text or output["tool_calls"]:
            return AdapterResult(
                ok=True,
                output=output,
                exit_code=0,
                evidence=[
                    Evidence(
                        kind="xai_chat",
                        detail=(
                            f"model={output['model']} "
                            f"finish={output['finish_reason']} "
                            f"chars={len(text)}"
                        ),
                        source="xai",
                    )
                ],
            )
        return AdapterResult(
            ok=False,
            output=output,
            error="xAI returned an empty completion",
            evidence=[
                Evidence(kind="execution", detail="empty completion", source="xai")
            ],
        )

    def probe(self) -> HealthStatus:
        if not self.api_key():
            return HealthStatus.AUTH_REQUIRED
        return HealthStatus.AVAILABLE