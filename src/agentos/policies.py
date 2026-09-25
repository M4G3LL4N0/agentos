"""Lightweight execution policies for AgentOS.

Policies decide what an execution is allowed to do *before* an adapter runs:

- blocked command patterns (destructive / exfiltration-prone shell)
- allowed path roots for filesystem writes (with a safe default)
- approval requirements for risky operation classes
- destructive-operation gating (never implicit)
- secret redaction for anything shown to humans or stored in readable form

This is deliberately the smallest useful mechanism, not an enterprise
framework. Policies live in ``config.json`` under ``policy`` and can be
tightened per home directory.
"""

from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

DEFAULT_BLOCKED_COMMAND_PATTERNS = [
    "*rm -rf /*",
    "*rm -rf ~*",
    "*rm -rf $HOME*",
    "*mkfs*",
    "*dd if=*of=/dev/*",
    "*:(){:|:&};*",
    "*curl*|*sh*",
    "*wget*|*sh*",
    "*chmod -R 777 /*",
    "*shutdown*",
    "*reboot*",
    "*halt*",
]

APPROVAL_OPERATION_CLASSES = {
    "destructive",
    "production",
    "external_write",
    "privileged",
}

SECRET_NAME_HINTS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "AUTH",
    "PRIVATE",
)


@dataclass
class PolicyDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    requires_approval: bool = False
    redacted_command: str | None = None


@dataclass
class ExecutionPolicy:
    """Evaluated against every shell/filesystem execution request."""

    blocked_command_patterns: list[str] = field(
        default_factory=lambda: list(DEFAULT_BLOCKED_COMMAND_PATTERNS)
    )
    allowed_write_roots: list[str] = field(default_factory=list)
    approval_required_classes: list[str] = field(
        default_factory=lambda: sorted(APPROVAL_OPERATION_CLASSES)
    )
    allow_destructive: bool = False
    allow_network: bool = True
    approvals_granted: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "blocked_command_patterns": list(self.blocked_command_patterns),
            "allowed_write_roots": list(self.allowed_write_roots),
            "approval_required_classes": list(self.approval_required_classes),
            "allow_destructive": self.allow_destructive,
            "allow_network": self.allow_network,
            "approvals_granted": list(self.approvals_granted),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ExecutionPolicy":
        data = data or {}
        policy = cls()
        if isinstance(data.get("blocked_command_patterns"), list):
            policy.blocked_command_patterns = [
                str(p) for p in data["blocked_command_patterns"]
            ]
        if isinstance(data.get("allowed_write_roots"), list):
            policy.allowed_write_roots = [
                str(p) for p in data["allowed_write_roots"]
            ]
        if isinstance(data.get("approval_required_classes"), list):
            policy.approval_required_classes = [
                str(p) for p in data["approval_required_classes"]
            ]
        policy.allow_destructive = bool(data.get("allow_destructive", False))
        policy.allow_network = bool(data.get("allow_network", True))
        if isinstance(data.get("approvals_granted"), list):
            policy.approvals_granted = [str(a) for a in data["approvals_granted"]]
        return policy

    # ------------------------------------------------------------------
    def check_shell(self, command: str, operation_class: str = "standard") -> PolicyDecision:
        reasons: list[str] = []
        normalized = " ".join(str(command).split())
        for pattern in self.blocked_command_patterns:
            if fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(
                normalized.lower(), pattern.lower()
            ):
                reasons.append(f"blocked by pattern {pattern!r}")
        destructive = self._looks_destructive(normalized)
        if destructive and not self.allow_destructive:
            reasons.append("destructive operation not allowed by policy")
        requires_approval = operation_class in self.approval_required_classes or (
            destructive and "destructive" in self.approval_required_classes
        )
        if requires_approval and operation_class not in self.approvals_granted:
            reasons.append(f"operation class {operation_class!r} requires approval")
        return PolicyDecision(
            allowed=not reasons,
            reasons=reasons,
            requires_approval=requires_approval and not reasons,
            redacted_command=redact_secrets(normalized),
        )

    def check_write_path(self, path: str) -> PolicyDecision:
        if not self.allowed_write_roots:
            return PolicyDecision(allowed=True, reasons=[])
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            return PolicyDecision(
                allowed=False, reasons=[f"cannot resolve path {path!r}"]
            )
        for root in self.allowed_write_roots:
            try:
                root_resolved = Path(root).expanduser().resolve()
            except OSError:
                continue
            try:
                resolved.relative_to(root_resolved)
                return PolicyDecision(allowed=True, reasons=[])
            except ValueError:
                continue
        return PolicyDecision(
            allowed=False,
            reasons=[f"path {path!r} outside allowed write roots"],
        )

    @staticmethod
    def _looks_destructive(command: str) -> bool:
        lowered = command.lower()
        markers = ("rm -rf", "mkfs", "dd ", "shutdown", "reboot", ":(){", "del /f")
        return any(m in lowered for m in markers)


_SECRET_NAME_PATTERN = (
    r"(?<![a-z0-9])(?:[a-z0-9]+[_-])*"
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|session[_-]?token|"
    r"client[_-]?secret|private[_-]?key|recovery[_-]?code|token|secret|"
    r"password|passwd|credential|authorization|cookie|bearer|key|auth)"
    r"(?:[_-][a-z0-9]+)*(?![a-z0-9])"
)
_SECRET_VALUE_RE = re.compile(
    rf"(?i)({_SECRET_NAME_PATTERN})\s*[:=]\s*['\"]?([^\s'\";,}}\]]+)"
)
_SECRET_JSON_RE = re.compile(
    rf"(?is)(?P<prefix>['\"]?{_SECRET_NAME_PATTERN}['\"]?\s*:\s*)"
    rf"(?P<quote>['\"])(?P<value>.*?)(?P=quote)"
)
_SECRET_BEARER_RE = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_SECRET_PRIVATE_KEY_RE = re.compile(
    r"(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----"
)
_SECRET_TOKEN_RE = re.compile(r"\b(?:sk|xai)-[A-Za-z0-9_-]{16,}\b")
_SECRET_KEY_NAMES = {
    "apikey",
    "accesstoken",
    "refreshtoken",
    "sessiontoken",
    "clientsecret",
    "privatekey",
    "recoverycode",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "credentials",
    "authorization",
    "cookie",
    "bearer",
}
_SECRET_KEY_WORDS = {
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "credentials",
    "authorization",
    "cookie",
    "bearer",
    "private",
    "recovery",
    "auth",
}
_SECRET_KEY_PREFIXES = {"api", "access", "client", "private", "secret", "signing"}


def redact_secrets(text: str) -> str:
    """Redact likely secret values from human-readable text."""
    if not text:
        return text
    redacted = _SECRET_PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", text)
    redacted = _SECRET_BEARER_RE.sub("Bearer ***redacted***", redacted)
    redacted = _SECRET_VALUE_RE.sub(
        lambda match: f"{match.group(1)}=***redacted***",
        redacted,
    )
    redacted = _SECRET_JSON_RE.sub(
        lambda match: (
            f"{match.group('prefix')}{match.group('quote')}"
            f"***redacted***{match.group('quote')}"
        ),
        redacted,
    )
    return _SECRET_TOKEN_RE.sub("***redacted***", redacted)


def _is_secret_key(key: Any) -> bool:
    text = str(key)
    normalized = re.sub(r"[^a-z0-9]", "", text.lower())
    if normalized in _SECRET_KEY_NAMES:
        return True
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    words = set(re.findall(r"[a-z0-9]+", expanded.lower()))
    if words & _SECRET_KEY_WORDS:
        return True
    return "key" in words and bool(words & _SECRET_KEY_PREFIXES)


def redact_value(value: Any) -> Any:
    """Recursively redact secret-bearing strings and structured values."""
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            redacted[key] = (
                "[REDACTED]"
                if _is_secret_key(key)
                else redact_value(item)
            )
        return redacted
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        return redact_secrets(value)
    return value


def env_secrets() -> set[str]:
    """Names of environment variables that look like secrets (never values)."""
    names: set[str] = set()
    for name in os.environ:
        upper = name.upper()
        if any(hint in upper for hint in SECRET_NAME_HINTS):
            names.add(name)
    return names


def scrub_env(env: dict[str, str]) -> dict[str, str]:
    """Return a copy of env with secret-looking values redacted (for logs)."""
    secret_names = {n.upper() for n in env_secrets()}
    scrubbed: dict[str, str] = {}
    for key, value in env.items():
        if key.upper() in secret_names or any(
            hint in key.upper() for hint in SECRET_NAME_HINTS
        ):
            scrubbed[key] = "***redacted***"
        else:
            scrubbed[key] = value
    return scrubbed


class ApprovalClass(StrEnum):
    """Canonical approval classes, least to most consequential."""

    READ_ONLY = "READ_ONLY"
    LOCAL_REVERSIBLE = "LOCAL_REVERSIBLE"
    EXTERNAL_REVERSIBLE = "EXTERNAL_REVERSIBLE"
    EXTERNAL_CONSEQUENTIAL = "EXTERNAL_CONSEQUENTIAL"
    FINANCIAL = "FINANCIAL"
    LEGAL = "LEGAL"
    SECURITY = "SECURITY"
    IRREVERSIBLE = "IRREVERSIBLE"


APPROVAL_CLASS_RANK = {name: rank for rank, name in enumerate(ApprovalClass)}


class TrustLevel(StrEnum):
    """Executor trust levels. Public discovery always lands on UNTRUSTED."""

    LOCAL_TRUSTED = "LOCAL_TRUSTED"
    INTERNAL_TRUSTED = "INTERNAL_TRUSTED"
    APPROVED_EXTERNAL = "APPROVED_EXTERNAL"
    UNTRUSTED = "UNTRUSTED"


def approval_class_for(
    operation: str = "",
    data_class: str = "INTERNAL",
    is_external: bool = False,
) -> ApprovalClass:
    """Assign the approval class for a task (task/policy decides, §18).

    An executor can never reduce this class — it is carried on the job,
    not negotiated by the cell.
    """
    text = f"{operation} {data_class}".lower()
    if any(k in text for k in ("financial", "money", "payment", "purchase")):
        return ApprovalClass.FINANCIAL
    if any(k in text for k in ("legal", "filing", "contract", "signature")):
        return ApprovalClass.LEGAL
    if any(k in text for k in ("security", "credential", "auth", "secret")):
        return ApprovalClass.SECURITY
    if "highly_sensitive" in text:
        return ApprovalClass.EXTERNAL_CONSEQUENTIAL if is_external else ApprovalClass.SECURITY
    if is_external:
        return ApprovalClass.EXTERNAL_REVERSIBLE
    if any(
        k in text
        for k in ("write", "delete", "execute", "run", "patch", "modify")
    ):
        return ApprovalClass.LOCAL_REVERSIBLE
    return ApprovalClass.READ_ONLY


def trust_allows(trust: str, data_class: str, is_external: bool) -> tuple[bool, str]:
    """Enforce trust boundaries (§19).

    Highly-sensitive work never routes to public/untrusted external
    executors. Returns ``(allowed, reason)``.
    """
    level = str(trust or "UNTRUSTED").upper()
    data = str(data_class or "INTERNAL").upper()
    if data == "HIGHLY_SENSITIVE" and (
        is_external or level == TrustLevel.UNTRUSTED.value
    ):
        return False, "HIGHLY_SENSITIVE work stays within trusted local cells"
    if data in ("CONFIDENTIAL", "HIGHLY_SENSITIVE") and level == "UNTRUSTED" and is_external:
        return False, "untrusted external executors are capped at INTERNAL"
    return True, f"trust={level} satisfies data class {data}"
