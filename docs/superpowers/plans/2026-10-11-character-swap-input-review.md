# Character-swap input review implementation plan

> Execute in the current clean `feat/video-character-swap` topic branch. The user approved implementation; do not commit, publish or make paid requests.

**Goal:** Show sampled clip frames and the exact prompt, then require durable input-bound acceptance before either paid character-swap stage.

**Architecture:** Extend the existing character-swap document, contracts, routes and Vue panel. Reuse owned FFmpeg preparation, atomic metadata publication and the current quote/receipt safeguards.

**Tech stack:** FastAPI/Pydantic, FFmpeg/Pillow, generated TypeScript contracts, Vue, unittest and Vitest.

## Backend and contracts

- [x] Add failing tests demonstrating submission without preparation review and missing review endpoint/strip.
- [x] Add `filmstrip_url`, `prompt`, `review_fingerprint`, `preparation_reviewed` to the job response; add `ReviewCharacterSwapRequest` with revision and exact fingerprint.
- [x] Prepare and retain `filmstrip.png` in existing unpublished staging; verify its dimensions and record its hash.
- [x] Derive response prompts/fingerprints from authoritative stage and retained inputs. Persist only accepted fingerprint; old records default to unreviewed.
- [x] Add `review` using admission/document locking, input checks, revision/fingerprint checks and atomic save. Gate `submit` before preparing any receipt or starting cloud work.
- [x] Add the protected review route and owned strip serving; regenerate `frontend/src/api/generated.ts`.
- [x] Adapt existing approved-flow test fixtures and add regression coverage for changed files/prompts/stages, reload, concurrent revision changes, interrupted writes and old records.

## Frontend

- [x] Add failing behavioral tests for filmstrip/prompt display, local review and inability to submit before saved acceptance.
- [x] Add the typed review API caller. Display five sampled frames, honest sampling text and exact backend prompt.
- [x] Add a local review button with current revision/fingerprint; integrate it with existing stale-response tokens, errors and busy state. Gate paid submission on returned saved acceptance.
- [x] Preserve existing saved-job loading, two cost approvals, accessibility and translated labels. Cover saved review reload and late review responses after selection/teardown.

## Integration

- [x] Update `docs/video-studio.md` with the review behavior and limits.
- [x] Run focused backend/frontend tests, then frontend full tests/build, backend full isolated discovery, generated-contract check and CI mypy platform loop.
- [x] Review spec compliance, then code quality, address findings and inspect final diff.
- [x] Report actual verification and unverified model quality; leave all changes uncommitted.

## Verification recorded

- Frontend: `npm test` passed 1,231 tests; `npm run build` passed strict TypeScript and production bundling (chunk-size warning remains).
- Backend: final isolated discovery passed 1,526 tests, with 10 platform/optional skips. Final focused swap/management run passed 78 tests. New concurrency test uses an isolated admission lock to avoid binding the application's shared lock to a closed unittest loop.
- Contracts: `python backend/scripts/generate_contracts.py --check` passed. Generated client types/parsers were produced from Pydantic schemas.
- Python: both strict CI mypy commands passed for Linux, macOS and Windows paths. The changed ten-file scope passed again after the diagnostic recovery fix.
- Browser: isolated temporary library, synthetic MP4/image, real strip (800×90) and clip decoding, local acceptance with OpenRouter disabled, acceptance retained after browser reload and actual backend restart. Test server and tab closed cleanly.
- Review: spec review passed; quality review caught diagnostic strips blocking paid-result recovery. Missing/changed-strip regression went red, then green after preserving GET-only collection. Final review reported no remaining important issues; `git diff --check` passed.
- No new dependency, model download, paid generation, commit or publishing action. Desktop packaging was unaffected and desktop tests were not run. Depth/SAM, mesh and HD generation remain deferred; visual generation quality was not evaluated.
