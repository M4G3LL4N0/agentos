# Publication Audit

Status: **public repository; verified code release**
Project: AgentOS 0.5.0
Repository: https://github.com/M4G3LL4N0/agentos

## Public contents

The public repository contains Python source, tests, package metadata, architecture and release documentation, synthetic examples, and CI configuration. It does not contain credentials, account state, browser sessions, cookies, private keys, harvested material, autonomy reports, billing data, machine state, local archives, or developer-specific paths.

## Excluded material

The release boundary excludes virtual environments, caches, SQLite files, local state, scratch review material, generated archives, private operational records, and all environment or credential files. Synthetic secret markers in tests and security documentation are not credentials.

## Verified release evidence

- Version: `0.5.0`.
- Verified code commit: `4583f079a36d0daf15f08b09c0584a2ed76bd595`.
- Local release gate: 710 tests passed, 0 failures, 0 errors, 0 skips.
- GitHub Actions `validate`: passed on the verified code commit; run `https://github.com/M4G3LL4N0/agentos/actions/runs/36160750070`.
- Runtime verification: local CLI, deterministic benchmark, backup dry-run, doctor, and source/bytecode audits.
- Provider calls: none required for the release evidence.
- Deployment: none.

## Review checklist

- [x] Explicit MIT license and package metadata.
- [x] Public README, security boundary, contributing guide, roadmap, changelog, and release notes.
- [x] Ignore rules cover local state, credentials, caches, and generated material.
- [x] Supported CI matrix and lightweight secret scan.
- [x] No private path or live-worker claim in the public surface.
- [x] Dedicated public remote created, pushed, and verified.
- [x] GitHub Actions validation passed on the verified code commit.
- [ ] v0.5.0 release tag and GitHub release created from the final verified commit.

No local validation result is evidence of a live external worker or paid provider call.
