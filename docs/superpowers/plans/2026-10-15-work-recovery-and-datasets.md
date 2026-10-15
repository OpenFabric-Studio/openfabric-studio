# Work Recovery and Dataset Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Recover live and saved music work after disconnects and provide a safe saved-dataset library plus clear model-specific LoRA navigation.

**Architecture:** Keep native jobs in their current engines and reconnect using their documented latest-task APIs. Backend-owned, atomically replaced metadata stores dataset identities, soft-deletion and workspace context; generated contracts connect this to the existing Vue/Pinia UI. No new service or dependency is needed.

**Tech Stack:** FastAPI/Pydantic, Python atomic-file helpers, Vue/Pinia, Vitest, unittest.

---

### Task 1: Backend dataset library and workspace

Files: new `backend/app/lora_dataset_contracts.py`, `backend/app/lora_datasets.py`, `backend/app/api/routes_lora_library.py`, regression tests alongside them; integrate `backend/app/main.py`, `backend/scripts/generate_contracts.py`, `.github/workflows/ci.yml`.

- [x] Write temporary-root regressions for legacy discovery, stable identifiers, creation, revision-safe rename, trash/restore, source preservation, interrupted writes and unsafe paths. Run the new unittest module before implementation and confirm failure.
- [x] Implement bounded schema-validated catalog entries and workspace metadata under the configured datasets root. Use `document_lock` around read-modify-write and atomic replacement. Soft deletion retains original files, keeping shared audio references intact; trash is a recoverable catalog state, with no permanent deletion endpoint.
- [x] Reject conflicting mutations while native labeling, preprocessing or training is running; fail closed when a running engine cannot be queried. Validate roots and identifiers before filesystem access. Preserve existing upload compatibility.
- [x] Expose list/create/rename/trash/restore/workspace routes with stable errors and local-origin checks. Register generated contracts and strict typing scope.
- [x] Run targeted regressions and contract generation.

### Task 2: Reconnect LoRA observers

Files: `frontend/src/stores/loraTraining.ts`, `frontend/src/api/aceStepTraining.ts`, new `frontend/src/stores/loraTraining.test.ts`, affected page tests.

- [x] Write fake-timer regressions for transient auto-label/preprocess failures, failed initial training discovery, reload/latest discovery, stale responses and ambiguous submission. Run them and confirm the current implementation fails.
- [x] Add validated idle/active latest-task responses using installed ACE API source. Keep observation alive on transport failure; expose reconnect state separately from native task failure.
- [x] Rediscover accepted work without resubmission, keep cancellation/generation guards, and resume labeling only remaining items. Do not imply full training checkpoint continuation.
- [x] Restore samples from the current native builder while observing a live task; avoid loading a different dataset into an active native builder.
- [x] Run focused store/page tests.

### Task 3: Dataset controls and saved context

Files: `frontend/src/api/loraDataset.ts`, `frontend/src/views/ace-step/LoraTrainingPage.vue`, new dataset-library component/test, `frontend/src/locales/en.ts`.

- [x] Add generated-contract client methods and tests; show existing datasets/counts, New, Rename, Trash and Restore using accessible existing styles.
- [x] Restore selected dataset and form settings from backend workspace, with teardown guards and conflict handling. Save snapshots before labeling, and save manual edits durably.
- [x] Merge uploaded/rescanned samples with saved labels on the backend; refuse competing operations while the native builder is in use.
- [x] Reuse tensors only with a matching dataset/model/settings receipt; otherwise explain the mismatch and use a new output folder. Preserve previous training run folders.
- [x] Test selection, recovery, saved edits, conflicts and upload preservation with mocked model boundaries.

### Task 4: LoRA overview and model tabs

Files: `frontend/src/router.ts`, music/ACE layouts, new overview/model navigation components and tests, existing `MusicLoraPage.vue` and its tests, locale text.

- [x] Verify current route and capability contracts. Test overview/default navigation, every model tab and preserved direct links before implementation.
- [x] Add overview plus ACE-Step, Stable Audio, YuE and HeartMuLa routes/tabs, reusing the existing forms. Explain dataset → captions → tensors → training → export and the existing control options.
- [x] Show capability-specific unsupported states, and keep durable job observation retrying after an initial connection failure. Add explicit music queue/network-loss regressions where missing.
- [x] Run focused navigation and music tests.

### Task 5: Integration, review and verification

- [x] Review spec coverage and the final diff, including filesystem confinement, stale actions, schema drift, failed writes and live-job ownership. Resolve review findings before completion.
- [x] Run `npm test` and `npm run build` from `frontend/`.
- [x] With temporary `OPENFABRIC_CONFIG`, `OPENFABRIC_DATA_DIR`, `SEED_VC_DIR`, `OPENFABRIC_MODULE_ROOT`, run backend unittest discovery, contract drift check and every strict CI mypy batch for Linux/macOS/Windows. Run desktop tests if startup/layout is affected.
- [x] Verify UI behavior in component tests and the isolated HTTP fixture library. Browser automation disconnected after overview inspection, so interactive browser actions remain unverified. Never restart the user's engine while a native task is active.
- [x] Leave code uncommitted and report actual verification and the unverified GPU/checkpoint boundary.


## Verification record

- Frontend: 164 files / 1,503 tests passed; strict TypeScript and production build passed.
- Backend: full regression run 1,956 tests, 10 skipped, exit 0; final affected-scope rerun 63 tests passed after the last source-path fix.
- Contracts: generated artifacts match backend schemas; no new dependency.
- Python: all six CI typing batches passed for Linux, macOS and Windows using an isolated cache. The final four dataset production files were rechecked on all three platforms after the last fix.
- Desktop: 87 passed, 1 skipped.
- Isolated HTTP verification: legacy discovery, create, workspace selection/revision, rename, trash and restore passed without using the real library.
- Spec and production-code reviews approved after fixing reload-hidden training, misleading unmatched status, discarded manual drafts and malformed-path isolation.
- Local rollout: native engines, labeling, preprocessing, training, local music and queue were verified idle before restarting the owned source app. All four existing datasets remain available, with label counts 21/21, 10/10, 1/1 and 22/22.
- No live training/GPU test or full checkpoint continuation is claimed. Browser control became unavailable after the overview was read; component and HTTP checks cover the changed interactions.
- The maintainer's follow-up on 2026-10-15 requests merging `feat/work-recovery-datasets` into `main`, pushing `main`, and removing the feature branch afterward.
