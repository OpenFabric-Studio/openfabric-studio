# Voice Step and Status Implementation Plan

> For agentic workers: use the subagent-driven-development workflow for this independent voice domain. User approved implementation; preserve existing work and do not commit.

**Goal:** Make Files → Samples → Coverage → Build → Compare a understandable progression with continuously visible preparation/training status, elapsed time, and evidence-based remaining estimates.

**Architecture:** Keep the existing always-mounted preparation observer and backend-owned jobs. Persist start/finish/phase timing and progress on backend responses so reloads retain elapsed time. Derive prerequisites and action labels from preparation revision/selection/usable model state; keep earlier steps accessible for revision.

**Tech Stack:** Python/Pydantic/FastAPI and Vue/TypeScript/Vitest.

**Files:** `backend/app/voice_contracts.py`, `backend/app/voice_preparation.py`, `backend/app/voice_build.py`, voice tests; `frontend/src/views/voice/VoiceClonePage.vue`, `VoicePreparationReview.vue`, `voiceWorkspace.ts`, `useVoicePreparation.ts`, new status/timing helpers and mounted tests; voice sections of `frontend/src/locales/en.ts` and `ru.ts`. Root owns shared client-contract/generated integration.

- [x] Add failing backend tests for persisted preparation/build start/finish timestamps and phase/progress changes, cancellation/recovery timing, and compatibility with legacy metadata.
- [x] Add bounded typed fields and persist progress/timing without changing source data or claiming a fabricated ETA. Use backend wall time for durable elapsed time; use measured comparable progress deltas for estimates.
- [x] Add failing frontend tests for step numbering, descriptive objectives, prerequisite explanations, next/back progression, a status area visible below navigation in every step, clock updates and teardown, estimates withheld without sufficient observations and reset on phase/job changes.
- [x] Implement numbered progression, clear complete/current/available/blocked indicators, contextual next action and explanatory text. Keep valid edits accessible and invalidate downstream completion honestly when saved preparation changes.
- [x] Show selected voice, current preparation/build/coverage operation, stage, file/step counts, progress, elapsed, estimated remaining or 'estimating', terminal result and cancel/recovery action directly below steps. Avoid noisy screen-reader announcements for every clock tick.
- [x] Verify build/preparation polling stays owned across step changes and late responses cannot alter another voice. Avoid duplicate competing progress summaries.
- [x] Verify source-path explanation for applying voice: generation → separation → conversion → original accompaniment mix, with zero-shot versus trained reference distinction.
- [x] Run focused voice backend/frontend tests and report contract fields for root regeneration; run complete frontend tests/build after integration.
