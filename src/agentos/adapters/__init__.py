"""Adapter package exports."""

from agentos.adapters.base import Adapter, AdapterResult, ExecutionRequest, text_of
from agentos.adapters.echo import ECHO_OPERATION, EchoAdapter
from agentos.adapters.filesystem import (
    EXISTS_OPERATION,
    LIST_OPERATION,
    READ_OPERATION,
    SUPPORTED as FILESYSTEM_OPERATIONS,
    WRITE_OPERATION,
    FilesystemAdapter,
)
from agentos.adapters.github import (
    BRANCH_OPERATION as GITHUB_BRANCH_OPERATION,
    COMMIT_OPERATION as GITHUB_COMMIT_OPERATION,
    FILE_OPERATION as GITHUB_FILE_OPERATION,
    ISSUE_OPERATION as GITHUB_ISSUE_OPERATION,
    PR_OPERATION as GITHUB_PR_OPERATION,
    REPO_OPERATION as GITHUB_REPO_OPERATION,
    SUPPORTED as GITHUB_OPERATIONS,
    GitHubAdapter,
)
from agentos.adapters.grok import GROK_OPERATION, GrokAdapter
from agentos.adapters.grokbot_office import (
    FORBIDDEN_GROKBOT_COMMANDS,
    GROKBOT_OFFICE_OPERATION,
    READ_ONLY_COMMANDS,
    GrokBotOfficeAdapter,
)
from agentos.adapters.langgraph import GRAPH_OPERATION, LangGraphAdapter
from agentos.adapters.opencode import OPENCODE_OPERATION, OpenCodeAdapter
from agentos.adapters.openclaw import OPENCLAW_OPERATION, OpenClawAdapter
from agentos.adapters.openhands import OPENHANDS_OPERATION, OpenHandsAdapter
from agentos.adapters.hermes import HERMES_OPERATION, HermesAdapter
from agentos.adapters.browser_harness import (
    BROWSER_HARNESS_OPERATION,
    BrowserHarnessAdapter,
)
from agentos.adapters.proc import resolve_binary, run_command, version_output
from agentos.adapters.shell import SHELL_OPERATION, ShellAdapter
from agentos.adapters.xai import CHAT_OPERATION as XAI_CHAT_OPERATION, XAIAdapter

__all__ = [
    "Adapter",
    "AdapterResult",
    "ExecutionRequest",
    "text_of",
    "EchoAdapter",
    "ECHO_OPERATION",
    "FilesystemAdapter",
    "READ_OPERATION",
    "WRITE_OPERATION",
    "EXISTS_OPERATION",
    "LIST_OPERATION",
    "FILESYSTEM_OPERATIONS",
    "ShellAdapter",
    "SHELL_OPERATION",
    "OpenCodeAdapter",
    "OPENCODE_OPERATION",
    "GitHubAdapter",
    "GITHUB_OPERATIONS",
    "GITHUB_REPO_OPERATION",
    "GITHUB_BRANCH_OPERATION",
    "GITHUB_FILE_OPERATION",
    "GITHUB_COMMIT_OPERATION",
    "GITHUB_ISSUE_OPERATION",
    "GITHUB_PR_OPERATION",
    "XAIAdapter",
    "XAI_CHAT_OPERATION",
    "GrokAdapter",
    "GROK_OPERATION",
    "GrokBotOfficeAdapter",
    "GROKBOT_OFFICE_OPERATION",
    "READ_ONLY_COMMANDS",
    "FORBIDDEN_GROKBOT_COMMANDS",
    "OpenClawAdapter",
    "OPENCLAW_OPERATION",
    "LangGraphAdapter",
    "GRAPH_OPERATION",
    "resolve_binary",
    "run_command",
    "version_output",
]