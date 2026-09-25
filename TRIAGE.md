# Triage (rules-first, cheap before premium)

`triage()` in `src/agentos/efficiency.py`. Deterministic resolution order,
cheapest to most expensive:

CACHE_RETURN → ARTIFACT_RETURN → KNOWN_WORKFLOW → DETERMINISTIC → DATABASE →
API → MCP → SCRIPT → CHEAP_MODEL → SPECIALIST → PREMIUM → GROKBOT

## Rules

1. **Exact cache hit short-circuits everything** — resolution `CACHE_RETURN`;
   zero model calls.
2. **Known workflow beats premium rerouting** — `KNOWN_WORKFLOW` only when the
   reusable catalog (usage-led) already has an applicable workflow.
3. **Deterministic math/sum/count → DETERMINISTIC**: no model.
4. **Cheap model** for classify/categorize/label.
5. **GrokBot is a late escalation**, never first — only when the objective
   uniquely needs persistent cloud computer/authenticated browser/durable
   supervisor identity (`grokbots_favored`), and it is disfavored for coding
   (`CODING`, `refactor`, `repo`, `test` hints set `grok_bias=DISFAVORED`).
6. Single-token hints use **word boundaries** so `compute` never matches
   `computer` (multi-word hints stay substring).

## Output

`TriageDecision` → `to_dict()`: `resolution`, `objective`, `reason`,
`route_matched`, `grok_bias`, `match`. Exposed via
`services.efficiency_triage()` and
`agentos efficiency triage "<objective>"`.

## Facts

`tests/test_efficiency_triage.py` (9) + kernel triage tests pin the ordering:
cache > workflow > deterministic > cheap > premium, and GrokBot only on unique
value.