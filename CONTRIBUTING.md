# Contributing

AgentOS is provider-neutral and evidence-driven. Keep changes focused on the execution, verification, or public-release boundary they actually serve.

## Local checks

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m compileall -q src
.venv/bin/python -m unittest discover -s tests
```

Use an isolated `AGENTOS_HOME` for CLI experiments. Do not run against a real state directory by accident.

## Change requirements

- Put provider-specific behavior behind an adapter; keep the core provider-neutral.
- Add regression tests for every new execution or persistence path.
- Verify success and failure cases with evidence, not only a return value.
- Keep retries, timeouts, worker counts, and escalation bounded.
- Redact secrets before persistence and keep private state out of source and docs.
- Update `CURRENT_STATE.md`, `BUILD_MAP.md`, and release evidence when behavior changes.

## Pull requests

Describe the user-visible outcome, safety boundary, tests run, and any unconfigured or experimental status. Do not include credentials, private paths, account data, or claims of live external execution.
