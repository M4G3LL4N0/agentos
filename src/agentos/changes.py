"""Filesystem + git change detection (git strictly READ-ONLY).

Gives every execution an honest change footprint: what the execution
produced vs what was already there. Git is never mutated here — only
``git status --short`` and ``git ls-files`` are invoked. No add, commit,
push, reset, clean, or checkout, ever.
"""

from __future__ import annotations

import hashlib
import os
import subprocess

# Directories never walked/hashed (bounded walk: never hash the world).
SKIP_DIRS = frozenset({".git", ".venv", "__pycache__", ".hg", ".svn", "node_modules"})

# Git subcommands this module may invoke. Anything else is forbidden.
GIT_READ_ONLY = frozenset({"status", "ls-files", "rev-parse", "diff"})

FsState = dict[str, str]


def _sha1_file(path: str) -> str | None:
    try:
        digest = hashlib.sha1()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except (OSError, PermissionError):
        return None


def capture_fs_state(root: str) -> FsState:
    """Map relpath -> sha1 for every readable file under root.

    Directories are excluded. ``SKIP_DIRS`` are pruned. Unreadable files
    are skipped gracefully (never crash on permission errors).
    """
    state: FsState = {}
    try:
        walker = os.walk(root, followlinks=False)
    except (OSError, PermissionError):
        return state
    for dirpath, dirnames, filenames in walker:
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                if not os.path.isfile(full):
                    continue
                rel = os.path.relpath(full, root)
            except (OSError, ValueError):
                continue
            digest = _sha1_file(full)
            if digest is not None:
                state[rel] = digest
    return state


def diff_fs(before: FsState, after: FsState) -> dict[str, list[str]]:
    """Diff two filesystem states.

    Returns ``added``/``modified``/``deleted`` lists (plus ``removed`` as
    an alias of ``deleted`` for caller convenience).
    """
    before_keys = set(before)
    after_keys = set(after)
    added = sorted(after_keys - before_keys)
    deleted = sorted(before_keys - after_keys)
    modified = sorted(k for k in before_keys & after_keys if before[k] != after[k])
    return {"added": added, "modified": modified, "deleted": deleted,
            "removed": list(deleted)}


def _git(args: list[str], root: str, timeout: float = 15.0) -> str | None:
    if not args or args[0] not in GIT_READ_ONLY:
        raise ValueError(f"git subcommand not read-only-allowed: {args}")
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def git_tracked(root: str) -> set[str]:
    """Relpaths tracked by git under root; empty set on any failure."""
    out = _git(["ls-files"], root)
    if not out:
        return set()
    return {line for line in out.splitlines() if line}


def git_status_short(root: str) -> str:
    """``git status --short`` output (read-only); ``""`` on any failure."""
    out = _git(["status", "--short"], root)
    return out if out is not None else ""


def attribute_changes(
    before: FsState, after: FsState, baseline: FsState | None = None
) -> dict[str, object]:
    """Distinguish produced changes from pre-existing ones.

    ``before``/``after`` bracket one execution; ``baseline`` (default
    ``before``) is the reference for what already existed. Files new in
    ``after`` that were not in ``before``/``baseline`` are *produced*;
    files modified by the execution that already existed in the baseline
    are *pre-existing-modified*; files modified that never existed in the
    baseline are *produced-modified*. Files already differing between
    ``baseline`` and ``before`` were dirty before this execution started.
    """
    base = baseline if baseline is not None else before
    d = diff_fs(before, after)
    added: list[str] = list(d["added"])
    modified: list[str] = list(d["modified"])
    deleted: list[str] = list(d["deleted"])

    produced_new = sorted(f for f in added if f not in base)
    restored = sorted(f for f in added if f in base)
    pre_existing_modified = sorted(f for f in modified if f in base)
    produced_modified = sorted(f for f in modified if f not in base)
    already_dirty = sorted(
        f for f in set(base) & set(before) if base.get(f) != before.get(f)
    )
    produced = sorted(set(produced_new) | set(produced_modified))
    pre_existing = sorted(set(pre_existing_modified))

    detail: dict[str, str] = {}
    for f in produced_new:
        detail[f] = "produced-new"
    for f in produced_modified:
        detail[f] = "produced-modified"
    for f in pre_existing_modified:
        if f in already_dirty:
            detail[f] = "pre-existing-modified (already dirty before execution)"
        else:
            detail[f] = "pre-existing-modified"
    for f in restored:
        detail[f] = "restored (in baseline, re-created during execution)"
    for f in deleted:
        detail[f] = "deleted during execution"

    return {
        "produced_new": produced_new,
        "produced_modified": produced_modified,
        "produced": produced,
        "pre_existing_modified": pre_existing_modified,
        "pre_existing": pre_existing,
        "already_dirty": already_dirty,
        "restored": restored,
        "deleted": deleted,
        "detail": detail,
    }


def change_summary(
    before: FsState,
    after: FsState,
    baseline: FsState | None = None,
    git_status: str | None = None,
) -> str:
    """One human-readable line (plus file lists) for a change footprint."""
    d = diff_fs(before, after)
    attr = attribute_changes(before, after, baseline=baseline)
    produced = attr["produced"]
    pre_existing = attr["pre_existing"]
    assert isinstance(produced, list) and isinstance(pre_existing, list)
    lines = [
        f"{len(d['added']) + len(d['modified']) + len(d['deleted'])} files changed: "
        f"+{len(d['added'])} new, ~{len(d['modified'])} modified, "
        f"-{len(d['deleted'])} deleted "
        f"({len(produced)} produced, {len(pre_existing)} pre-existing)"
    ]
    if produced:
        lines.append("produced: " + ", ".join(sorted(produced)))
    if pre_existing:
        lines.append("pre-existing: " + ", ".join(sorted(pre_existing)))
    if d["deleted"]:
        lines.append("deleted: " + ", ".join(d["deleted"]))
    if git_status:
        excerpt = git_status.strip().splitlines()
        lines.append("git status:")
        lines.extend(f"  {line}" for line in excerpt[:20])
        if len(excerpt) > 20:
            lines.append(f"  ... ({len(excerpt) - 20} more)")
    return "\n".join(lines)
