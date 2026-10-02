# Review fixes implementation plan

The user approved the review's eleven fixes and requested strict engineering guidance. Work in the existing checkout to preserve its uncommitted voice/video feature work. Do not commit or publish.

**Goal:** Correct the eleven reproduced faults, enforce safe app-owned contracts, and run the regressions in CI.

**Architecture:** Keep Vue/Pinia, FastAPI, SQLite, and the existing engine processes. Move ACE candidate persistence into a tracked backend job so it survives browser teardown. Use atomic metadata replacement, recoverable library migration, shared polling/channel disposal, and validated app-owned API schemas.

## Storage (exclusive owner: storage agent)

- [x] Add failing regressions for default → A → B relocation and failure after catalog path rewrite.
- [x] Preserve the active migration source in configuration, rewrite structured paths safely, and recover the catalog/metadata with the files on rollback. Account for existing configurations and interrupted moves.
- [x] Run `python -m unittest app.data_root_test -v` using isolated configuration.

## Audio (exclusive owner: audio agent)

- [x] Add a frontend behavioral runner and failing regressions for MIDI instrument parity and complete graph teardown.
- [x] Export/reuse complete channel disposal and use the selected MIDI instrument during offline rendering.
- [x] Remove unsafe audio assertions using actual Web Audio and inspected SoundTouch types.
- [x] Run focused frontend tests and the production type-check/build.

## Frontend jobs (exclusive owner: jobs agent)

- [x] Add failing regressions for queued YuE2 cancellation and polling teardown/restart races.
- [x] Implement shared polling cancellation semantics for ACE, YuE2, orchestrator, and affected controls.
- [x] Replace frontend ACE fetch/upload persistence with app-owned backend jobs; preserve pending candidates until all are durable and reconcile reloads without duplicated tracks.
- [x] Remove explicit `any` from stores/forms/i18n/API stats and validate persisted JSON at boundaries.
- [x] Lazy-load routed views and verify focused tests/build.

## Backend and integration (owner: root)

- [x] Add failing tests for encoded/absolute/symlink SPA path escape, fine-tuned checkpoint selection, atomic metadata reads, descendant cancellation, and browser-independent ACE persistence.
- [x] Constrain SPA serving, pass voice checkpoint/config to inference, serialize metadata updates with atomic replacement, and kill/await Demucs process groups.
- [x] Own and drain subprocess/background jobs during API shutdown.
- [x] Add persisted ACE generation jobs, idempotent candidate writes, validated request/response contracts, and a generated frontend contract artifact.
- [x] Wire behavioral frontend/backend checks, dependency setup, and generated-contract verification into CI.
- [x] Run integrated suites, review every final change against the eleven findings, and independently review spec compliance followed by code quality.

## Verification commands

- Backend: `python -m unittest discover -s app -p '*_test.py' -v` from `backend`, with the declared test requirements installed and temporary `REMIQORA_CONFIG`/`REMIQORA_DATA_DIR`.
- Frontend: `npm test` and `npm run build` from `frontend`.
- Desktop: `npm test` from `desktop`.
- Contracts: run the generation script in check mode once introduced.
- Final diff: `git diff --check` and inspect tracked/untracked implementation files against the initial working tree.

Implemented and integrated. Independent review added regressions for SQLite transaction interference, numeric overflow, completed-file recovery before catalog insertion, new-project graph rebuilding, and editor/history navigation races. Final command results are reported in the task handoff. No commit or deployment was made.
