# Security

AgentOS executes commands and can bridge to external tools. Treat every capability, adapter, prompt, artifact, and remote response as untrusted until reviewed.

## Public repository boundary

Never commit or include:

- API keys, access tokens, passwords, cookies, session data, private keys, or credentials;
- browser profiles, account exports, harvested material, autonomy reports, billing data, or machine-local state;
- absolute developer paths, private inventories, or private operational logs;
- fabricated live-worker, cost, usage, or verification claims.

The repository excludes virtual environments, caches, SQLite state, archives, and local scratch material through `.gitignore`. Public examples and security tests use synthetic markers only.

## Runtime rules

- Keep secrets in environment variables or approved secret stores.
- Use simulated or inspect mode until authorization and policy gates are explicit.
- Treat a capability label as metadata, not proof of execution.
- Require real evidence before recording a successful outcome.
- Stop and escalate when a boundary, approval, or credential assumption is unclear.

## Reporting a vulnerability

Do not open a public issue containing exploit details or private evidence. Use GitHub's private vulnerability reporting for this repository when available. Include a sanitized reproduction, affected version, impact, and suggested mitigation without account data or secrets.

## Related boundaries

See [`GROKBOT_PROVIDER.md`](GROKBOT_PROVIDER.md) for the read-only external bridge and [`PUBLICATION_AUDIT.md`](PUBLICATION_AUDIT.md) for the release contents.
