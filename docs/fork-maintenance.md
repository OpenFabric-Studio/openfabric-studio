# OpenFabric Studio maintenance

This repository is an **independent** MIT project under `OpenFabric-Studio`, derived from Remiqora (inikolax → mchosc). It is not maintained as a GitHub fork of remiqora.

## Policy

- Keep MIT attribution: `LICENSE`, `NOTICE`, UI notices linking inikolax/remiqora and mchosc/remiqora.
- Prefer lean GitHub Actions (PR + `main` CI; desktop packaging on tags / `workflow_dispatch` only).
- When adopting code from other projects, record provenance in commits/`NOTICE` and respect their licenses.
- Review third-party PRs for safety and fit before merge.

## Release checks (desktop)

Unsigned experimental installers. Verify clean install, GPU path, and first-run download before publishing any draft release.
