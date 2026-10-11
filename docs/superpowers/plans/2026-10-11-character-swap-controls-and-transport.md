# Character replacement repairs and saved-job controls

Scope authorized by the user: fix video generation, allow saved replacements to be removed, and assess local Mac models. No paid generation, model installation or deletion of existing user jobs during verification.

## Evidence and design

- Official OpenRouter responses permit external `unsigned_urls`. Our parser rejects them despite downloads already using the fixed authenticated OpenRouter content endpoint. Ignore unused response URL metadata; continue enforcing fixed request endpoints.
- Explicit HTTP validation rejections currently become `submission_unknown`. Persist a bounded HTTP status and classify definitive rejections as failed. Timeouts, 5xx and invalid success responses remain unknown. Preserve historical receipts: their original HTTP responses were not retained.
- Add revision-checked PATCH rename and DELETE endpoints for inactive saved replacements. Reject local owners, active states and unresolved workers. Rename clears a stale quote. Deletion atomically hides the job in a marked local tombstone before removing media; interrupted cleanup is retried on startup. Paid receipts are stored separately and survive deletion.
- Add named confirmation, rename controls, disabled active controls, stopped preview playback, response ownership checks, neighboring selection and keyboard focus in the existing UI. Unknown billing remains explicit. Generated contracts remain the client source of truth.
- Research only: this Mac has M4 Max/128 GiB. Official Wan Animate replacement requires CUDA; community MLX replacement is experimental. A small isolated trial is a follow-up, not part of this change.

## Execution and verification

1. Generate management contracts; reproduce missing routes with temporary-library regression tests.
2. Implement safe management and interrupted-delete recovery; cover stale revisions, active/unresolved work, legacy local jobs, origin protection and symlink escapes.
3. Implement transport regression coverage for valid external URL metadata and rejected/uncertain POST outcomes. Keep a single submission and fixed-origin polling/downloads.
4. Verify terminal rejection cannot offer GET-only recovery without a remote ID. Integrate UI tests and public error translations.
5. Run frontend tests/build, backend temporary-library regressions, generated-contract checks and the CI strict Python platform checks. Review final diff and exercise controls against synthetic data; rebuild/restart the loopback app without paid requests or user-job mutations.

Limit: mocked provider verification cannot establish successful paid generation on the user's footage. Historical unknown outcomes still require account activity review.

## Completed verification

- Frontend: 1,158 tests in 138 files; strict TypeScript/policy production build passed.
- Backend: 1,449 tests run with 10 expected skips; isolated config, library, Seed-VC and module roots. The management routes first failed with HTTP 405, then passed all 14 management regressions. A rejected video initially incorrectly offered resume; the added regression now passes.
- Desktop: 85 tests passed, one skipped. Generated-contract drift check and both exact CI strict mypy scopes passed for Linux, macOS and Windows typing paths (6 feature / 163 core files per platform). Final diff whitespace check passed.
- Isolated browser/API check: prepared a synthetic two-second clip with cloud disabled, saved a renamed title, inspected and cancelled the named confirmation, deleted only the disposable fixture via its local endpoint, then verified removal from the UI, missing media directory and HTTP 404 for the removed job.
- Rebuilt/restarted the actual app on loopback port 9000 and opened Replace a character with Rename/Delete visible. SHA-256 comparisons confirmed all five existing job documents and all six billing receipts stayed unchanged. Temporary test server was shut down cleanly.

Evidence logs: `/tmp/openfabric-swap-{frontend-full,build,backend-full,desktop,types}.log`; screenshot `/tmp/openfabric-character-swap-controls.png`. No live generation, retry of unknown receipts, user-job deletion, model download or local model inference was performed. New work is uncommitted.
