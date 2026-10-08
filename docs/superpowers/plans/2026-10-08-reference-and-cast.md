# Speech References and Cast Inspection Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans task by task. Do not commit or push without a new user request.

**Goal:** Reject unsafe speech-reference inputs and expose actual chapter voice routing before synthesis.

**Architecture:** Reuse trusted-origin validation and installed audio decoders at the profile boundary. Build a read-only chapter report from the existing cast parser/resolver and generate its frontend contract. Keep stored media and narration routing unchanged.

**Tech Stack:** FastAPI, Pydantic, Python unittest, Vue, generated TypeScript parsers, Vitest.

## Task 1: Reference upload boundary

- [x] Add regression cases in `backend/app/voice_profile_upload_test.py` for malformed/truncated WAV/FLAC, oversized streamed multipart requests, valid preserved bytes and untrusted create/import/patch/delete requests. Use real small PCM fixtures rather than fake RIFF headers.
- [x] Run those cases in temporary configuration/library/engine paths and confirm failures before implementation.
- [x] Update `backend/app/api/routes_voice_profiles.py` to bound the request before parsing, bound file reads and reuse `require_local_origin` on all mutations. Validate audio in `backend/app/voice_profiles.py` before storage; preserve consent and transactional cleanup.
- [x] Update existing backend profile fixtures that used invalid placeholder audio, then verify affected tests. Integrate stable upload errors into existing profile UI messages.

## Task 2: Cast inspection

- [x] Add backend and HTTP regressions for routing parity, unknown labels/headings, narrator-shared assignments, unused/missing/revoked profiles, no synthesis/writes and stale saved chapter revisions.
- [x] Extract only the existing label parsing into a reusable helper in `backend/app/audiobook_cast.py`; inspection must use `split_turns` for actual routing. Add bounded request/response models in `backend/app/audiobook_contracts.py`, focused inspection functions beside the existing resolver, and draft/saved endpoints in audiobook routes.
- [x] Register the models with the existing client-contract list and run `backend/scripts/generate_contracts.py` using the backend environment.
- [x] Add validated API functions in `frontend/src/api/audiobookWorkflow.ts` and Check cast controls/results beside auditions. Add frontend regressions for input changes, hidden/unmounted panels, stale responses and accessibility.

## Task 3: Integration and verification

- [x] Update relevant user docs with input limits and advisory cast-warning behavior.
- [x] Run frontend tests and strict build, backend regressions in temporary roots, contract drift checks and the CI strict-mypy platform loop for linux/darwin/win32.
- [x] Review the final diff for schema ripple effects, original-data preservation, process/error ownership and accidental scope. Keep changes reviewable on the feature worktree until integration is requested.

## Verification recorded

- Full backend suite, including the final unknown-length FLAC rejection guard: 1,287 tests, 10 skipped.
- Frontend: 1,079 tests passed; strict type policy, vue-tsc and production build passed.
- Desktop: 78 tests passed, one Windows-only test skipped.
- CI strict-mypy command: 162 source files passed for Linux, macOS and Windows targets.
- Generated contract drift and diff whitespace checks passed. A fresh hash-locked baseline install validated WAV/FLAC and rejected truncation and understated sample counts.
- Spec and quality reviews passed after fixes for stale reports/save refresh, pronunciation errors, chapter navigation, false WAV alignment and untrusted FLAC sample counts.

No model downloads, GPU synthesis or native Windows/Linux installer runs were performed for this feature. The maintainer authorized committing, merging into `main` and pushing on 2026-10-08.
