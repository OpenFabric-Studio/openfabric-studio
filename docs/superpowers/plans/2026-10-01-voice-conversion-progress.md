# Voice Conversion Progress Implementation Plan

> **For agentic workers:** Use focused backend and frontend agents with exclusive file ownership, followed by independent integrated review. Follow the approved design in `docs/superpowers/specs/2026-10-01-voice-conversion-progress-design.md`.

**Goal:** Make waiting and active voice conversion understandable without inventing progress or completion estimates.

**Architecture:** Extend the existing persisted progress tracker and generated API contracts. Track ownership of the existing accelerator lock, emit structured conversion events through vendor compatibility patches, and display one reusable typed progress component across track workflows.

**Tech stack:** Python/FastAPI/Pydantic, asyncio, SQLite audio-version catalog, Vue/TypeScript, Vitest and unittest.

## Backend

- [x] Add red regression tests for application progress persistence, terminal/interrupted/retry clocks and propagation into audio-version responses (`voice_build_test.py`, `audio_versions_test.py`).
- [x] Extend `VoiceJobProgress` with application kind, preparation/loading/analysis/conversion/mixing stages, chunk units and bounded queue metadata. Add optional `job_progress` to `ApplyStatusResponse` and `AudioVersion`.
- [x] Add tested ownership metadata to the existing accelerator lock. Report the current owner only while held, clear on failure/cancellation, preserve waiting order, and retain generic ownership fallback for callers without labels.
- [x] Integrate application timing and queue metadata into `voice_build.py`; persist terminal timing and reconcile interrupted work without inventing historical timestamps.
- [x] Add independent source-fixture regression tests for structured Seed-VC phase/chunk events. Patch exact inspected signatures idempotently, fail closed on unknown signatures, and avoid modifying the installed engine during tests.
- [x] Consume bounded incremental progress events, exclude startup from measured rate, reject malformed/regressive events and stop/await the watcher during cancellation or completion.
- [x] Generate frontend contracts with `backend/scripts/generate_contracts.py`; include new core modules in the existing strict mypy platform loop.

## Frontend

- [x] Add red behavioral tests for queue reason/label, phase/counts, unavailable/stale ETA, elapsed time freezing, retry/reset and optional legacy progress.
- [x] Display the shared progress component for every active voice version regardless of playback selection. Preserve cancel/retry controls and original playback behavior.
- [x] Carry authoritative progress through initial generation and uploaded-track replacement stores; remove synthetic percentages for instrumented jobs.
- [x] Add English/Russian strings, an accessible measured/indeterminate progress bar, polite stage announcements and timer/poll cleanup tests.

## Integration and publication

- [x] Review final diff and obtain focused independent code review. Fix material findings with regression coverage.
- [x] Run `npm test` and `npm run build` in `frontend/`.
- [x] Run backend unittest discovery using temporary `REMIQORA_CONFIG`, `REMIQORA_DATA_DIR` and `SEED_VC_DIR` paths, then generated-contract drift check and strict mypy on linux/darwin/win32 using the workflow's module list.
- [x] Verify compatibility patches against a temporary copy of installed vendor source without loading models; do not claim real GPU inference coverage from this check.
- [x] Review changed paths for real data, model cache, generated artifact drift and accidental changes.
- [ ] Commit and push the verified topic branch to the fork, check hosted CI, and report the branch, actual validation and runtime limitations.

Local verification: 398 frontend tests and strict production build passed; 474 backend tests passed with 4 installed-engine tests skipped; 45 strict Python modules passed linux/darwin/win32 typing; generated contract drift, workflow lint and final diff checks passed. Vendor compatibility applied twice to a temporary copy of installed source without changing the installed files. Real GPU inference is not claimed.
