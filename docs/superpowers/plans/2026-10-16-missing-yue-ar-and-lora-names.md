# Missing YuE AR and LoRA name uniqueness implementation plan

**Goal:** Finish the AR part of an existing NAR LoRA from its immutable saved
source, and prevent new duplicate names without disturbing existing runs.

**Architecture:** Extend the existing durable training job and owned worker.
Use an optional source-job reference for AR-only requests and the existing
registry lock plus an OS file lease for atomic name checks across processes. Preserve old request fingerprints and publication.

**Tech stack:** Pydantic/generated TypeScript, existing SQLite job JSON, file
registry locks, Vue, pinned YuE trainer and native score CLI.

The user approved the behavior in this turn. Preserve the dirty workspace and
the active Stable Audio job. No commit, push or live restart is authorized while
that job is active. Focused agents have exclusive ownership below.

## Backend AR job and source recovery

Files: music_training_contracts.py, music_training.py, music_training_runtime.py,
api/routes_music_training.py, yue_planner_source.py and corresponding tests.

- [x] Reproduce missing planner action, changed/missing cached sources, duplicate
  completion, interrupted publication and legacy fingerprint behavior.
- [x] Add optional yue_ar_source_job_id, server-computed can_train_missing_ar and
  POST /jobs/{id}/planner with existing YueLoraPairSettings as its body.
- [x] Derive AR request from the completed NAR run; validate and pass exact
  cached excerpts and expected hashes. Register AR with the preserved NAR link.
- [x] Integrate the shared name guard under the registry lock around job insert.
- [x] Run focused temporary-library tests and strict platform typing.

## Worker control flow

Files: yue_lora_runtime.py, scripts/yue_lora_worker.py and corresponding tests.

- [x] Reproduce AR-only invocation, immutable source checks, failure and
  cancellation paths with owned fake children.
- [x] Verify and copy saved excerpts without decoding again, extract scores,
  run only AR, convert and write the existing single-artifact receipt format.
- [x] Keep paired and legacy NAR behavior and fingerprints; reject unsupported
  AR-only portable package requests before preparing an archive.
- [x] Run focused tests and strict typing on Linux, macOS and Windows paths.

## Names and integration (root)

Files: music_lora_names.py, music_lora.py, stable_lora.py, their tests, affected
ACE registration boundaries if needed, CI typing scope and generated contracts.

- [x] Reproduce case/whitespace collisions, same-name imports, concurrent
  creation, same-family AR completion and completion of pre-existing jobs.
- [x] Add a shared backend guard over saved adapters and durable job history;
  serialize checks and creation with the existing registry lock.
- [x] Integrate imports and Stable training; leave legacy publication intact.
- [x] Regenerate contracts and run required integrated checks.

## Frontend

Files: api/musicTraining.ts, ModelLoraTraining.vue, MusicLoraPage.vue,
TrainingJobProgress.vue, locales/musicLora.ts and corresponding tests.

- [x] Reproduce eligible history action, review/start, source-family request,
  old backend hiding, duplicate-name errors and paired preview behavior.
- [x] Implement the reviewed AR-only action and clear model-specific history;
  report duplicate names without silently renaming or changing saved jobs.
- [x] Run frontend tests/build using generated contracts.

## Final verification

- [x] Review requirements, scoped final diff, source safety, naming transaction
  boundaries and cancellation/recovery. Obtain independent focused review.
- [x] Run backend suite with temporary roots, frontend tests/build, contracts,
  all strict platform typing groups, and desktop tests if affected.
- [x] Verify the UI in an isolated test app if the live training remains active;
  preserve its progress and report any restart still required for activation.


## Verification evidence (2026-10-16)

- Backend full suite: **2,423 tests, 17 skipped**, passed with temporary config,
  library, module and Seed-VC roots. Latest focused planner/name/runtime/worker
  coverage: **64 tests passed** after the final capability check changes.
- Frontend final suite: **1,740 tests in 170 files passed**; strict build passed.
  Final regression closes the AR review on KeepAlive deactivation while job
  polling continues and keyboard ownership is released.
- Generated contracts check passed. All seven CI strict mypy groups passed for
  Linux, macOS and Windows typing paths. Desktop: **87 passed, 1 skipped**.
- Independent worker/backend/frontend reviews completed. Review findings fixed:
  valid large name-history records, cheap polling instead of full artifact
  hashing, usable score verification at publication, and cached dialog cleanup.
- An isolated copy of the user's legacy NAR run passed source verification,
  including its exact saved audio and registered NAR. Live NAR SHA-256 remained
  unchanged; no AR training job was started.
- Live jobs were checked immediately before restart: Stable training cancelled,
  other training/generation done or failed, queue idle, video idle and no setup
  installer running. Gracefully restarted the owned source launcher, then
  verified the actual parent advertises `can_train_missing_ar: true` and its
  review card opens. Duplicate-name warning also verified in the live form.
- Browser evidence is outside Git under
  `/Users/seth/Documents/OpenFabricStudioData/live/recovery/missing-yue-ar-2026-10-16/`:
  `train-missing-ar-live.jpg`, `duplicate-name-warning.jpg` and
  `saved-source-verification.json`. Isolated preview server stopped afterward.
- Reviewed the scoped diff; `git diff --check` passed. Existing unrelated changes
  were preserved. No commit or push performed.

**Limits:** No new AR-only GPU training or audio-quality evaluation was run.
CUDA and actual Windows execution remain unverified; platform typing checks
are not hardware tests. Legacy duplicate names remain visible and reserved.
A failed AR child with a retained completion receipt must be recovered instead
of silently training again.
