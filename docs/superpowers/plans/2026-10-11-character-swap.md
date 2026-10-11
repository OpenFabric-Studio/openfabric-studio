> Superseded by the approved [OpenRouter-only implementation plan](2026-10-11-openrouter-character-swap.md).

# Character Swap Implementation Plan

> **For agentic workers:** Use focused parallel ownership for transport, frontend and job/media integration. Follow test-first implementation and review the integrated diff. No commits or paid requests are authorized.

**Goal:** Add the approved optional cloud character-replacement workflow for supplied short video sections.

**Architecture:** Fixed fal model adapter, durable local jobs under videos/character-swaps, and an independent panel in the existing Video view. Reuse current atomic storage, supervised processes, media validation, HTTP and generated contracts.

**Tech Stack:** FastAPI, Pydantic, httpx, FFmpeg/Pillow, Vue, Vitest and unittest.

## Transport and contracts

- [x] Add failing HTTP regressions in `backend/app/fal_video_test.py` for single submit, unknown outcomes, validated IDs, bounded payloads and credential-free constrained downloads.
- [x] Add `backend/app/character_swap_contracts.py` with prepared/submitting/unknown/queued/running/ready/failed/stopped states, public job data and confirmation requests.
- [x] Add `backend/app/fal_video.py`: full model path for POST, root app path for request GETs, fixed safety/480p controls, FAL_KEY environment credential, no paid retries, streamed size limits and data URI inputs.
- [x] Run isolated unittest regressions and strict typing for these files.

## Owned jobs and media

- [x] Reproduce the missing readiness endpoint with a failing API regression in `backend/app/character_swaps_test.py`.
- [x] Add `backend/app/character_swaps.py` and `backend/app/api/routes_character_swaps.py`. Persist immutable source hashes, bounded selected inputs, quote expiration, intent and remote identity. Serialize every metadata transition with `document_lock`.
- [x] Supervise preparation/encoding workers, own preparation and polling tasks, and drain them on stop/shutdown. Recover known provider jobs with GET only; retain unknown submissions.
- [x] Verify actual CPU media preparation/export, original retention, source audio, cancellation, duplicate submission, interrupted publication and symlink rejection.
- [x] Integrate startup/recovery/shutdown in `backend/app/main.py` and work admission in `backend/app/work_busy.py`; register client models in `backend/scripts/generate_contracts.py` and strict CI scope.

## Frontend and documentation

- [x] Add failing entry/control regressions in `frontend/src/views/video/VideoPage.test.ts` and `VideoCharacterSwap.test.ts`.
- [x] Add `frontend/src/api/characterSwaps.ts`, generated-contract parsers, `VideoCharacterSwap.vue` and English translations following current locale registration. Poll with abort/generation protection and render retained before/after media.
- [x] Integrate an accessible Character swap option without discarding the current video workspace. Explain single-person/short-clip scope, price/transfer review, environment setup and uncertain submission/stop behavior.
- [x] Update `docs/video-studio.md` and run focused tests, full frontend/backend/desktop suites, contract drift and the CI mypy loop.
- [x] Review the final diff and report actual checks plus the unverified live-generation/quality boundary.


## Completed verification — 2026-10-11

- Frontend: `npm test` passed all 1,096 tests; `npm run build` passed strict TypeScript, policy checks and production bundling.
- Backend: final isolated discovery passed 1,321 tests with 10 skips. Preparation/export tests use real CPU FFmpeg; provider boundaries are mocked.
- Contracts: regeneration and `generate_contracts.py --check` passed. Fixed local media patterns reach the generated client parser; cross-field job identity validation remains on the backend.
- Python: both CI strict mypy commands passed for Linux, macOS and Windows typing paths (4 new production files plus the existing 162-file scope). The new service tests also passed strict checks on all three paths.
- Desktop: 78 tests passed with one platform skip.
- Independent review covered paid-request ambiguity, recovery, worker ownership, copied-library receipts, metadata stripping and media-link validation. No outstanding concrete findings.
- An existing audiobook test raced asynchronous cleanup. A gated reproduction failed before and passed after the three-line test helper change to await its owned task; every existing assertion and timeout is preserved.
- An isolated browser preview verified retained test media and cost/transfer review after reload. Browser file access prevented chooser upload; local API preparation and media downloads were verified instead. The isolated server was shut down.
- Final diff and whitespace checks passed. No dependencies, commits, deployments, paid inference or user-library mutations were part of this work.

Not established: live fal availability, perceptual character/scene/motion preservation, or native Windows recovery. These require provider and platform evidence before claiming end-to-end quality.
