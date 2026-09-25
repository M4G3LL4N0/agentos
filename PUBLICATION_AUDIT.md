# Publication Audit

Status: **pre-publication verification**

Project: AgentOS 0.5.0
Target: `M4G3LL4N0/agentos`

## Public contents

The public repository contains Python source, tests, package metadata, architecture and release documentation, synthetic examples, and CI configuration. It does not contain credentials, account state, browser sessions, cookies, private keys, harvested material, autonomy reports, billing data, machine state, local archives, or developer-specific paths.

## Excluded material

The release boundary excludes virtual environments, caches, SQLite files, local state, scratch review material, generated archives, private operational records, and all environment or credential files. Synthetic secret markers in tests and security documentation are not credentials.

## Truthful release status

- Version: `0.5.0`.
- Local release gate: 710 tests passed, 0 failures, 0 errors, 0 skips.
- Runtime verification: local CLI, deterministic benchmark, backup dry-run, doctor, and source/bytecode audits.
- Provider calls: none required for the release evidence.
- Deployment: none.
- Public remote: the dedicated target above is reserved for this release and is not the inherited parent remote.

## Review checklist

- [x] Explicit MIT license and package metadata.
- [x] Public README, security boundary, contributing guide, roadmap, changelog, and release notes.
- [x] Ignore rules cover local state, credentials, caches, and generated material.
- [x] Supported CI matrix and lightweight secret scan.
- [x] No private path or live-worker claim in the public surface.
- [ ] Dedicated public remote pushed and verified.
- [ ] GitHub Actions run verified on the pushed commit.
- [ ] v0.5.0 release tag and notes created from the verified commit.

No local validation result is evidence of a live external worker or paid provider call.
