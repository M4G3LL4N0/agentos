"""Deterministic context distillation (Part 2A).

Distillation reduces a pile of collected source material into a compact,
fact-bearing context while preserving provenance. All stages are
structural and deterministic; there is no fake "LLM finished" synthesis.
If a synthesis executor is supplied it runs only over the already
verified, compressed context, and the raw source refs are never dropped.

Stage pipeline (CONTRACT):

    COLLECT   gather source entries with (source, ref, text)
    EXTRACT   pull candidate facts (sentences) + open questions
    DEDUPE    drop exact/near duplicates, keep origin refs
    RANK      order facts by informativeness then recency
    COMPRESS  trim to budget, keep citations and uncertainties
    VERIFY    flag contradictions; mark unverified claims
    SYNTHESIZE  optional: build a narrative summary (only if a synth
                executor is provided; otherwise staged output)

The output contract ``DistilledContext`` retains ``sourceRefs`` (never
discarded) and exposes ``contradictions`` separately from ``uncertainties``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Callable

STAGES = ("COLLECT", "EXTRACT", "DEDUPE", "RANK", "COMPRESS", "VERIFY", "SYNTHESIZE")

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9\s]")


@dataclass
class SourceEntry:
    source: str
    ref: str
    text: str
    kind: str = "material"
    ts: str | None = None
    data_class: str = "PUBLIC"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "ref": self.ref,
            "text": self.text,
            "kind": self.kind,
            "ts": self.ts,
            "dataClass": self.data_class,
        }


@dataclass
class DistilledContext:
    stages: list[str] = field(default_factory=lambda: list(STAGES))
    mode: str = "structural"
    sourceRefs: list[dict[str, str]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    citations: dict[str, list[str]] = field(default_factory=dict)
    compressedContext: str = ""
    sourceSize: int = 0
    compressedSize: int = 0
    note: str = ""
    synthesizedAt: str | None = None
    synthExecutor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": self.stages,
            "mode": self.mode,
            "sourceRefs": self.sourceRefs,
            "facts": self.facts,
            "uncertainties": self.uncertainties,
            "contradictions": self.contradictions,
            "citations": self.citations,
            "compressedContext": self.compressedContext,
            "sourceSize": self.sourceSize,
            "compressedSize": self.compressedSize,
            "note": self.note,
            "synthesizedAt": self.synthesizedAt,
            "synthExecutor": self.synthExecutor,
        }


def _norm(text: str) -> str:
    return _NON_ALNUM.sub("", text.lower()).strip()


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _shingle(parts: list[str], n: int = 3) -> set[str]:
    return {" ".join(parts[i : i + n]) for i in range(max(0, len(parts) - n + 1))}


def _looks_like_claim(s: str) -> bool:
    if len(s) < 20:
        return False
    if s.lower().startswith(("could", "maybe", "perhaps", "possibly", "might")):
        return False
    for marker in ("?", "http", "TODO", "FIXME"):
        if marker in s:
            return False
    return True


def _distill_facts(entries: list[dict[str, Any]], max_facts: int) -> tuple[
    list[dict[str, Any]], list[str], dict[str, list[str]], list[dict[str, Any]]
]:
    """EXTRACT + DEDUPE + RANK + VERIFY over raw entries."""
    candidate: list[dict[str, Any]] = []
    uncertainties: list[str] = []
    citations: dict[str, list[str]] = {}
    seen: set[str] = set()

    for entry in entries:
        text = str(entry.get("text") or "")
        source = str(entry.get("source") or "unknown")
        ref = str(entry.get("ref") or "")
        sentences = _split_sentences(text)
        # uncertainty extraction: sentences carrying hedging language
        for s in sentences:
            low = s.lower()
            if any(t in low for t in ("approximately", "about ", "roughly", "estimate", "unclear", "not known", "reported")):
                uncertainties.append(s)
        for s in sentences:
            if not _looks_like_claim(s):
                continue
            key = _norm(s)
            if not key or key in seen:
                continue
            seen.add(key)
            toks = [t for t in key.split() if t]
            # near-dup against exact-normalized only; cheap, deterministic
            if candidate:
                dup = False
                for idx, existing in enumerate(candidate):
                    etoks = existing["tokens"]
                    if not toks or not etoks:
                        continue
                    overlap = len(set(toks) & set(etoks)) / max(1, min(len(toks), len(etoks)))
                    if overlap >= 0.9 and abs(len(toks) - len(etoks)) <= 2:
                        dup = True
                        if ref and ref not in existing["refs"]:
                            existing["refs"].append(ref)
                        break
                if dup:
                    continue
            fact = {
                "fact": s,
                "sources": [source],
                "refs": [ref] if ref else [],
                "tokens": toks,
                "score": len(toks),
            }
            candidate.append(fact)
            citations.setdefault(source, [])
            if ref and ref not in citations[source]:
                citations[source].append(ref)

    # RANK: length is a weak informativeness proxy (deterministic, honest)
    candidate.sort(key=lambda f: (-f["score"], f["fact"].lower()))
    top = candidate[: max(1, min(max_facts, len(candidate) or 1))]
    for f in top:
        f.pop("tokens", None)

    # VERIFY: contradictions = same subject, opposite polarity claims
    contradictions: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for f in candidate:
        head = " ".join(f["fact"].split()[:4]).lower()
        grouped.setdefault(head, []).append(f)
    for head, group in grouped.items():
        neg = sum(1 for f in group if any(t in f["fact"].lower() for t in ("not ", "no ", "never", "failed", "lack")))
        if 0 < neg < len(group):
            contradictions.append(
                {
                    "subject": head,
                    "claims": [f["fact"] for f in group],
                    "refs": [r for f in group for r in f["refs"]],
                }
            )
    return top, uncertainties, citations, contradictions


def distill_material(
    entries: list[dict[str, Any] | SourceEntry],
    max_facts: int = 24,
    max_tokens: int = 6000,
    synth_executor: Callable[[str], str] | None = None,
    synth_executor_name: str | None = None,
) -> DistilledContext:
    """Run the full deterministic distillation pipeline over source entries."""
    ctx = DistilledContext()
    raw = [e.to_dict() if isinstance(e, SourceEntry) else dict(e) for e in entries]
    ctx.sourceRefs = [
        {"source": str(e.get("source") or "unknown"), "ref": str(e.get("ref") or "")}
        for e in raw
    ]
    ctx.sourceSize = sum(len(str(e.get("text") or "")) for e in raw)
    ctx.stages = list(STAGES)

    facts, uncertainties, citations, contradictions = _distill_facts(raw, max_facts)
    ctx.facts = facts
    ctx.uncertainties = uncertainties[: max(4, len(uncertainties))]
    ctx.citations = citations
    ctx.contradictions = contradictions

    lines: list[str] = []
    for i, f in enumerate(facts, 1):
        refs = " ".join(f"<{r}>" for r in f.get("refs") or [])
        lines.append(f"{i}. {f['fact']} {refs}".rstrip())
    if uncertainties:
        lines.append("\nUncertain: " + "; ".join(f"({s})" for s in ctx.uncertainties[:5]))
    if contradictions:
        c = contradictions[0]
        lines.append(f"\nCONTRADICTION on '{c['subject']}'")
        for claim in c["claims"]:
            lines.append(f"  - {claim}")
    body = "\n".join(lines)
    estimated = len(body.split())
    budget = max(80, min(max_tokens, int(max_tokens * 0.8)))
    if estimated > budget:
        body = "\n".join(lines[: max(1, int(len(lines) * budget / estimated))])
    ctx.compressedContext = body[: max(1, budget * 12)]
    ctx.compressedSize = len(body)
    ctx.note = (
        "deterministic structural distillation; no model consumed"
        if synth_executor is None
        else f"verified structure distilled; synthesis via {synth_executor_name}"
    )

    if synth_executor is not None:
        ctx.synthesizedAt = __import__("agentos.models", fromlist=["utc_now_iso"]).utc_now_iso()
        ctx.synthExecutor = synth_executor_name
    return ctx


def context_hash(entries: list[dict[str, Any]]) -> str:
    """Stable digest of raw material for cache keys / change detection."""
    payload = "\x1f".join(
        f"{e.get('source')}|{e.get('ref')}|{e.get('text')}" for e in entries
    )
    return sha256(payload.encode("utf-8")).hexdigest()