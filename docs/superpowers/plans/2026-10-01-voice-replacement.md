# Reference Track Voice Replacement Implementation Plan

> For agentic workers: use focused parallel ownership for backend upload/conversion and frontend API/history. Preserve existing work; no commits or model downloads are requested.

**Goal:** Add Voice Replacement beside reference/remix scenarios: an uploaded song uses a ready cloned voice while retaining its accompaniment and original library source.

**Architecture:** Add a typed multipart `/api/voices/replace` boundary. Validate bounded upload bytes and decoded audio duration, persist independent original/result tracks, then reuse backend-owned voice conversion and status. Never submit an ACE generation request for this scenario. Keep its results in ACE's existing feed/history without mislabelling upload origin as generated music.

**Tech Stack:** Existing FastAPI/Pydantic, SQLite track catalog, FFmpeg/Demucs/Seed-VC; Vue/Pinia/strict TypeScript/Vitest.

## Ownership

- Backend worker: new `backend/app/voice_replacement.py`, its regression tests, `api/routes_voices.py`, minimal apply cancellation helper in `voice_build.py`, `client_contracts.py` response registration. No generated file edits.
- Frontend worker: `api/voices.ts`, `stores/aceStep.ts`, focused API/store tests. Root integrates generated contracts.
- Root: `GenerateForm.vue`, `JobCard.vue`, `VoiceSelect.vue` integration if needed, locales, UI tests, docs, CI strict scope and final verification.

## Tasks

- [x] Write and reproduce backend failures for missing/unready voice, invalid/oversized/too-long audio, safe upload names, rollback on storage/start failure, original/result file independence, cancellation and deleting a running result.
- [x] Implement upload-to-library conversion with stable errors and generated response contract `{track, source_track, application}`. Limit files to 256 MiB and two hours; use supported audio extensions. Preserve original bytes in a separate source catalog row and conversion in a result row, with explicit source/voice provenance.
- [x] Add drained apply cancellation endpoint; make replacement deletion drain its worker before deleting result files. Keep original available.
- [x] Write failing API/store tests for replacement bypassing ACE release, immediate status, reload restoration, filtering unrelated uploads, cancellation and stale polling.
- [x] Add typed replacement/cancel APIs and store actions; merge only tagged replacement uploads into existing ACE history, respecting concurrent submission/poll ownership.
- [x] Write mounted form tests for selectable Voice Replacement despite ACE task restrictions, requiring upload and ready selected voice, accepting empty style, correct action label, generation controls hidden, and regular cover behavior unchanged.
- [x] Implement option and help-table row, named upload/voice controls, action/error states and both locales. Send only file and captured voice ID to replacement. Guard repeated submits and teardown responses.
- [x] Integrate source download/result controls and cancel/retry behavior in existing result cards. Never label an in-progress replacement's original audio as converted output.
- [x] Regenerate/check contracts; run frontend suite/build, backend suite, expanded strict mypy and diff check. Test browser upload flow against temporary CPU fixtures with model boundaries mocked; no GPU quality claims.
- [x] Document limits and final verification; review the complete scoped diff without committing.

## Final evidence

- Native inventory was inspected in the live ACE server and local source. The parser now accepts its LM objects and nullable default; all three installed DiT models were selectable in the running browser. Loading/empty/error/retry and request ownership have regressions.
- Reviewed and fixed initial job persistence failure, cancelled upload-reader ownership, retry provenance, crash supervision, failed termination ownership, unusable cached voices, unknown initial engine status, and stale history after deletion. Decoder input protocols/formats and transport/file limits are bounded.
- Full isolated backend suite: **379 tests passed**. Generated contracts check passed; strict CI mypy scope passed. Full frontend suite/build passed before the subsequent generator-panel request; that request has a separate plan and receives final frontend verification.
- Private CPU browser fixture verified upload, cancel, retry, navigation/reload recovery, converted playback, byte-identical original download, independent result bytes and a 390px layout without horizontal overflow. Model inference was mocked in browser verification.
- An early regression reached real Demucs; model-cache/device effects were not recorded. The checkpoint-link effect was disclosed, its active configured target was verified, and final test engine paths were isolated. No GPU singing-quality claim is made.
