# Music Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Replace `/music`'s automatic engine redirect with an approved brief-and-queue interface backed by durable, sequential local generation.

**Architecture:** One coordinator stores brief snapshots and ordered child work in SQLite, then delegates to existing generation services. An internal owner-aware admission reservation covers engine transitions without blocking the coordinator's own children. The browser previews and controls the queue through generated contracts; it never owns execution.

**Tech Stack:** Existing Python 3.12/FastAPI/Pydantic/SQLite backend and strict TypeScript/Vue/Pinia frontend. No new dependencies.

**Workspace:** Preserve the current uncommitted feature implementation in this workspace. Do not commit, publish, deploy, reset, or create a checkout that loses those dependencies. Assign exclusive files to independent implementers; review shared integrations centrally.

## 1. Contracts, normalization and readiness

Files: create `backend/app/music_queue_contracts.py`,
`backend/app/music_queue_plan.py`, and `backend/app/music_queue_plan_test.py`.
Contract generation registration in `backend/scripts/generate_contracts.py` is
integrated centrally after contracts freeze.

- [x] Write failing normalization regressions before production code. Cover:
  two selected engines × three variations produces six typed items; YuE requires
  lyrics and reports no controlled duration; SA3 rejects vocals; Heart duration
  is a maximum; native/local duration bounds are applied visibly; invalid tempo,
  duplicate engine selection, prompt overflow, variant/adapter mismatch and
  missing setup fail before enqueue.
- [x] Run the focused unittest module with temporary config/library/module and
  Seed-VC roots; observe missing-feature or assertion failures.
- [x] Define strict contracts for brief, ordered engine selections, normalized
  discriminated requests, per-engine adjustments/readiness, preview, submission,
  queue state, brief/item/attempt state and revisioned controls. Keep original
  text separate from normalized input. Use existing request and SavedTrack types.
- [x] Implement deterministic mapping and preview fingerprints. Canonical bounds
  are in the approved design. Resolve readiness from existing installation and
  model evidence without loading engines or downloading files. Validate matching
  adapters and preserve their provenance.
- [x] Verify the focused tests and strict mypy on Linux/darwin/win32. Freeze
  public field names before frontend/backend coordinator integration.

Normalization test invariant:

```python
self.assertEqual([item.engine for item in preview.items],
                 ['ace_step'] * 3 + ['yue2'] * 3)
self.assertIsNone(preview.models[1].effective_duration_seconds)
```

## 2. Internal ownership and legacy admission

Files: create `backend/app/music_queue_ownership.py` and regression tests; modify
only required admission helpers/callers in `resource_admission.py`,
`gpu_lease.py`, `work_busy.py`, `support_controls.py`, engine mutation boundaries
and their focused tests. Coordinate exclusive file ownership before editing.

- [x] Reproduce an external model switch/generation competing with an active
  queue owner; assert a stable conflict while the owner's own child is admitted.
- [x] Implement a private capability/context owner, not a user-supplied bypass
  flag. Reserve once existing incompatible work is idle; retain ownership across
  model transitions and release only after verified cleanup.
- [x] Protect external native initialization, generation, model/LoRA changes,
  setup, training and GPU work through existing shared boundaries. Pending or
  paused queue records alone must not reserve resources. Child tasks inherit
  only their owner's context, and stale owner contexts cannot gain new access.
- [x] Coordinate manual Stop with queue cancellation and exact owned-instance
  checks. Avoid self-deadlocks in `require_setup_idle`, `reserve_native`, local
  submissions or stop controls. Preserve current behavior without a queue owner.
- [x] Verify conflict, self-admission, stale-owner, release-failure and concurrent
  control tests; run existing admission/native lifecycle suites and typing.

Ownership test invariant:

```python
with self.assertRaises(ResourceBusyError):
    await reserve_native(lambda: False)
# The same call in the coordinator's private owner context remains admissible.
```

## 3. Durable queue and engine adapter

Files: create `backend/app/music_queue.py`, `music_queue_engines.py`,
`music_queue_test.py`, `music_queue_engines_test.py`,
`api/routes_music_queue.py` and `api/routes_music_queue_test.py`.
Modify `main.py`, `app_lifespan_test.py`, contract generation and CI typing scope.

- [x] Write red tests using fake engine services and temporary SQLite storage.
  Assert persisted ordering, distinct stored seeds, immutable briefs, submission
  key/hash deduplication, revision conflicts, outstanding-work bounds and atomic
  enqueue/reorder. A database failure must not leave partially queued briefs.
- [x] Add SQLite migrations for queue control, brief snapshots and ordered items
  with foreign keys, indexes and uniqueness constraints. Use independent
  transactions for queue read-modify-write operations.
- [x] Implement one owned scheduler. Persist submission intent before native
  calls and child identities immediately after return. Do not pre-submit every
  model's work. Reuse existing track publication and completed job recovery.
- [x] Add engine delegation: native load/start and exact-instance stop for ACE
  and YuE; existing supervised one-shot jobs for HeartMuLa and SA3. Explicitly
  clear/apply ACE's session adapter. Await child terminal state and complete
  registry/lease drain before switching. No automatic voice conversion.
- [x] Test pause after saving, cancellation during load/inference/publication,
  blocked startup, partial success, cleanup failure, manual Stop, child lookup
  failure, ambiguous native submission and retained-output recovery. Failed
  ownership proof must block advancement rather than free the reservation.
- [x] Integrate recovery after engine-job recovery and shutdown before engine-job
  teardown. On restart reconcile saved children and leave pending work paused;
  never blindly resubmit an ambiguous accepted native task.
- [x] Implement bounded same-origin APIs for preview, snapshot, enqueue, start,
  pause, cancel, reorder and explicit retry. Return stable typed errors; keep raw
  exceptions in backend logs. Generate the frontend contract artifact.
- [x] Run backend focused tests, contract drift check and strict typing.

Scheduler test invariant:

```python
self.assertEqual(events, [
    'load:ace_step', 'save:ace_step:1', 'save:ace_step:2', 'unload:ace_step',
    'load:yue2', 'save:yue2:1', 'save:yue2:2', 'unload:yue2',
])
```

## 4. Approved Vue workspace

Files: create typed `frontend/src/api/musicQueue.ts` and tests; create
`frontend/src/views/music/MusicQueuePage.vue` and focused shared/presentational
components if needed, plus tests and `frontend/src/locales/musicQueue.ts`.
Modify MusicPage/router/navigation tests and the locale registration.

- [x] Write failing tests for `/music` opening the queue without switching an
  engine, live totals and ordered model choices, lyrics/instrumental validation,
  preview adjustments, queue controls and results tied to original briefs.
- [x] Implement the approved split composer/plan with existing theme/components.
  Show real readiness and compatible engine-specific adapter controls. Preserve
  existing direct engine and training routes under Advanced generators.
- [x] Bind to generated backend types. Validate unknown HTTP results at the API
  boundary with existing schema/helper conventions. No duplicated API models,
  unsafe assertions, `any`, or TypeScript suppressions.
- [x] Poll only backend state with abort/generation guards. Draft changes or
  teardown invalidate old preview responses. Avoid concurrent mutations from
  stale tabs through revision handling, and prevent duplicate button submits.
- [x] Add keyboard labels/focus/error behavior, translated text and responsive
  layout. Reuse actual library audio actions; show no invented progress/ETA.
- [x] Run focused UI/API tests, then frontend `npm test` and `npm run build`.

## 5. Integration review and completion

- [x] Independently review spec coverage, then code quality, including ownership,
  all legacy entry paths, schema/type propagation and saved-output recovery.
  Fix concrete findings and rerun affected regressions.
- [x] Run backend full unittest discovery with all four temporary environment
  roots, generated contracts `--check`, all six CI mypy batches for all three
  platforms, frontend tests/build and affected desktop tests. Use mocked model
  boundaries; download no weights and touch no user library during tests.
- [x] Browser-test the actual queue against an isolated test backend: preview,
  add multiple briefs, pause/resume/cancel, progress/results, reload and competing
  tabs. Exercise keyboard and narrow layouts. CPU fixture execution is evidence
  for orchestration only, not model quality/residency.
- [x] Update local-music documentation and the audit with actual verification
  and remaining GPU/platform limits. Inspect the complete feature diff.
- [x] Restart the user's app only after confirming it is idle and has no pending
  library move. Restore the previously selected engine without launching queued
  inference. Leave changes uncommitted and report actual checks and limits.


Verification record: full backend discovery 1,920 tests / 10 skips; final focused
queue/engine/YuE/API/lifecycle regressions 127 passed; frontend 158 files / 1,426
tests and strict production build passed; desktop 87 passed / 1 skipped; all six
CI typing batches passed for linux/darwin/win32 (212 selected files), with final
changed sources rechecked separately. Contract drift and pip consistency checks
passed. Native Chrome CPU fixtures verified two briefs, pause/reload/resume,
cancellation, two-tab observation, saved playback and 80–150% zoom layouts.
Fixture backend stopped; real app remains on loopback with an empty queue and
YuE restored. Real model/GPU transitions and rebuilt desktop installers are not
claimed; no weights downloaded and no commit/publish/deployment performed.
