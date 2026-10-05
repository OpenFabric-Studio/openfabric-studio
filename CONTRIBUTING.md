# Contributing to OpenFabric

This repository, [OpenFabric-Studio/openfabric-studio](https://github.com/OpenFabric-Studio/openfabric-studio), is a maintained
fork of [inikolax/remiqora](https://github.com/inikolax/remiqora), originally created
by Nikolay Cherkashin. Bug reports, documentation fixes and focused pull requests
are welcome in **English or Russian**. See the [roadmap](ROADMAP.md) for priorities.

## Questions and ideas

Use this fork's [issues](https://github.com/OpenFabric-Studio/openfabric-studio/issues) for questions,
reproducible bugs and concrete feature proposals. Include the commit/version,
OS/device, installed engine revisions, reproduction steps and sanitized logs.
Do not upload secrets or private recordings. For security reports, follow
[SECURITY.md](SECURITY.md).

## Getting set up

Follow the [README](README.md) for your platform. Read [AGENTS.md](AGENTS.md) before
changing code, and use a copied or temporary library for development checks.
Voice/video engine installation is separate from the backend environment; see
[voice preparation](docs/voice-quality.md) and [video studio](docs/video-studio.md).
Use the documented setup for engines/models; unit tests must not download weights.

## Before you open a pull request

- Branch from the fork's `main` using a small `fix/…`, `feat/…` or `docs/…` topic.
  Preserve unrelated uncommitted work. Open the PR against this fork's `main`.
- Keep one problem per PR. Explain its concrete trigger, resulting behavior,
  compatibility/data effects and remaining uncertainty.
- Add meaningful regression tests for behavioral changes, including relevant
  failure, cancellation, concurrency and recovery paths. Keep TypeScript strict;
  validate untrusted input and derive app-owned client contracts from the backend.
- Run frontend tests/build, backend regressions, contract drift and the exact
  scoped strict-mypy check in CI. Run desktop tests when startup, packaging or
  file layout changes. [Fork maintenance](docs/fork-maintenance.md#required-checks)
  gives isolated test commands; [CI](.github/workflows/ci.yml) is the checked scope.
- Add user-facing strings to the current English locale module, using the existing
  design components. Include relevant UI screenshots and accessibility
  verification for UI changes.
- Use descriptive commits with `feat:`, `fix:`, `docs:` or `chore:` prefixes. Keep
  model weights, recordings, `.env`, logs and machine-specific artifacts out of Git.
- Report commands actually run and distinguish CPU/mocked coverage from real
  engine/platform checks. Passing tests do not establish perceptual model quality.

Maintainers integrate upstream through `sync/upstream-…` branches. Do not rebase or
force-push shared `main`. Submit selected, self-contained fixes upstream; the
fork's accumulated baseline is not one giant upstream PR. See the
[maintenance workflow](docs/fork-maintenance.md#branches-and-upstream-integration).

## Third-party models

Engine code, model weights, datasets and generated media can have separate terms.
Keep provenance and license notices for the exact artifacts used; check their
authoritative license files before distributing them. This repository's MIT
license does not license someone else's weights, recordings or output rights.

## License

Contributions to this repository are licensed under [MIT](LICENSE). Preserve the
original copyright notice and credit to Nikolay Cherkashin and the upstream
project; make the maintained-fork identity clear in distributed builds and docs.
