# Working on OpenFabric Studio

Act as a senior technical and business partner. Be direct, skeptical, and evidence-driven. Distinguish verified facts, assumptions, inferences, and unknowns. Inspect the repository and authoritative source code before guessing APIs or changing architecture.

## Scope and collaboration

- Preserve existing uncommitted work. Never reset, overwrite, or discard another contributor's changes.
- Complete the authorized task, including affected integrations and verification. Ask only when a missing decision materially affects security, data integrity, compatibility, or external behavior.
- Prefer the smallest maintainable root-cause fix. Add infrastructure or dependencies only when a concrete requirement justifies them.
- Use focused parallel agents for independent problem domains when useful; assign exclusive file ownership and review the integrated result.
- Do not commit, publish, deploy, rewrite Git history, or run production migrations unless explicitly requested.

## Maintained fork workflow

- This is `OpenFabric-Studio/openfabric-studio`, a maintained fork of `inikolax/remiqora`. Preserve upstream history, MIT copyright and attribution; clearly label fork changes and experimental releases.
- `origin` is the fork and `upstream` is the original repository. Check remotes before pushing; use the fork as the push default. Do not push to upstream as part of ordinary fork work.
- Use focused topic branches from fork `main`. Integrate upstream on reviewed `sync/upstream-…` branches with merge commits; never force-push or rebase shared `main`.
- Follow [ROADMAP.md](ROADMAP.md) and [fork maintenance](docs/fork-maintenance.md). Prioritize recoverable data, installation and measured quality before additional engines or infrastructure.
- Before broad migrations or a baseline snapshot, preserve source/history and private configuration, take a consistent database backup, and copy user media/checkpoints/datasets. Keep backups and model caches outside Git. Do not move a library during active work.
- Run the required checks before integrating into `main`. Release tags must match desktop version metadata; packaging creates a draft prerelease. Publishing requires an explicit maintainer request covering the reviewed artifacts and platform evidence. Never overwrite a published release's assets.

## Strict type safety

- TypeScript must remain in strict mode. Do not introduce `any`, double assertions, unchecked assertions at untrusted boundaries, `@ts-ignore`, `@ts-expect-error`, or disabled checks to make a build pass.
- Treat HTTP responses, JSON, local storage, uploaded files, and external model output as untrusted. Parse into `unknown`, then validate and narrow before use.
- Backend request/response schemas are the source of truth for app-owned APIs. Generate client contract types from those schemas and check generated artifacts in CI. Do not duplicate or guess models.
- Use explicit interfaces, discriminated unions, typed errors, and bounded numeric/string values. Use shared JSON value types only for deliberately opaque JSON; do not replace domain models with broad dictionaries.
- Python code must annotate public boundaries and new helpers. Use Pydantic models, dataclasses, enums, TypedDicts, and explicit callback signatures where appropriate. Do not add `Any`, blanket type-check suppression, or fake stubs to silence errors.
- JavaScript boundaries must validate input and document supported values. Third-party typing defects require an accurate, narrowly scoped declaration based on inspected package source, never an unsafe cast.

## Security and storage

- Keep business logic, validation, filesystem access, and job persistence on the backend.
- Resolve and constrain all user-controlled filesystem paths. Reject absolute paths, traversal, and symlink escapes from serving roots.
- Preserve original data until replacements are complete. File/catalog migration must remain recoverable after errors or interrupted processes.
- Use atomic replacement for metadata and serialize updates to the same object. A lock around one write does not make a read-modify-write transaction atomic.
- Use SQLite migrations/constraints for schema changes. Consider existing rows, indexes, rollback, and backward compatibility; never silently lose data.
- Persist generated outputs independently of the browser. Reloading, navigation, or closing a tab must not abandon completed work.
- Never expose secrets or raw internal exceptions to users. Log technical details on the backend and return structured, stable error codes.
- The application has no built-in authentication. Keep loopback binding by default; do not broaden network exposure as an incidental change.

## Processes, async work, and audio

- Own every background task and subprocess. Cancellation must terminate descendants, await exit, clean partial outputs, and release locks. Shutdown must drain all job registries.
- Use cancellation/generation tokens for polling and awaited UI actions. A response after teardown must not restart work or update a different session.
- Keep preview and export behavior consistent. Dispose complete Web Audio graphs, including oscillators, feedback loops, analysers, and destination connections.
- Voice quality labels must describe measured screening criteria. Preserve original source/time provenance and dry samples; require reviewed selection and singer confirmation. Keep held-out evaluation audio separate from training. Fresh builds must not discover prior checkpoints; resume must validate the same data/settings and restore complete training state.
- Reuse existing components and styling. Accessibility, keyboard controls, translated text, and honest progress/error states are part of correctness.

## Tests and verification

- Reproduce bugs with meaningful regression tests before fixing them when practical. Cover failure, cancellation, concurrency, reload, and interrupted-write paths, not just happy paths.
- Tests must use temporary libraries/configuration and mocked model boundaries; never touch the user's library or download GPU models as part of unit tests. Set `SEED_VC_DIR` to a temporary engine path as well: model placement can rewrite the configured engine's checkpoint symlink even when the library path is temporary.
- Run frontend type-check/build and tests, backend regression tests, and desktop tests when affected. CI must run behavioral checks, not syntax checks alone.
- Inspect the final diff for scope, type/schema ripple effects, security, and accidental changes.
- Never claim a command passed unless it actually ran successfully. Report what changed, why, actual verification, and remaining uncertainties. GPU and platform-specific flows require explicit evidence before claiming end-to-end support.

## Required checks

- Frontend: `npm test` and `npm run build` from `frontend/`. The build runs strict TypeScript and rejects explicit `any`, chained assertions, and TypeScript suppressions.
- Backend: install `backend/requirements-test.txt`, then run `python -m unittest discover -s app -p '*_test.py' -v` from `backend/`. Always point `OPENFABRIC_CONFIG` and `OPENFABRIC_DATA_DIR` at temporary paths first.
- Contracts: `python backend/scripts/generate_contracts.py --check` from the repository root. Regenerate after changing a shared Pydantic model; never hand-edit `frontend/src/api/generated.ts`.
- Python core: run the strict mypy platform loop in `.github/workflows/ci.yml` for Linux, macOS and Windows typing paths. Its checked scope includes contracts, storage/job ownership, voice preparation/comparison/DSP helpers, vendor compatibility, and contract generation. Accurate soundfile declarations live under `backend/typings`; expand checked legacy coverage without pretending this is a whole-backend check.
- Desktop: `npm test` from `desktop/` when shared backend startup, packaging, or file layout changes.
