# OpenFabric Studio maintenance

This repository is an **independent** AGPL-3.0-only project under `OpenFabric-Studio`, derived from Remiqora (inikolax → mchosc). Inherited MIT material retains its original terms. It is not maintained as a GitHub fork of remiqora.

## Policy

- Keep the AGPL `LICENSE`, inherited MIT terms in `LICENSE-MIT`, `NOTICE`, and UI notices linking inikolax/remiqora and mchosc/remiqora. Include these notices in installers and provide Corresponding Source for the exact binary release. Modified network deployments must offer their exact Corresponding Source to remote users under AGPL section 13.
- Prefer lean GitHub Actions (PR + `main` CI; desktop packaging on tags / `workflow_dispatch` only).
- When adopting code from other projects, record provenance in commits/`NOTICE` and respect their licenses.
- Review third-party PRs for safety and fit before merge.

## Release checks (desktop)

Unsigned experimental installers. Verify clean install, GPU path, and first-run download before publishing any draft release.
