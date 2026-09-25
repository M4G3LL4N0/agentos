## Summary

## Safety and evidence

- [ ] No secrets, cookies, sessions, account data, private paths, or fabricated live-worker claims
- [ ] Provider-specific behavior stays behind an adapter
- [ ] Success and failure paths have regression coverage
- [ ] Status labels distinguish verified, optional, unconfigured, experimental, and planned behavior

## Verification

- [ ] `python -m compileall -q src`
- [ ] `python -m unittest discover -s tests`
- [ ] `git diff --check`

## Review notes
