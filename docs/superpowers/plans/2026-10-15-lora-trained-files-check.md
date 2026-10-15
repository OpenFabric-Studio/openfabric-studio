# Saved LoRA files check implementation plan

**Goal:** Let the user check the selected ACE-Step training run for an existing adapter and proceed to export without starting another training run.

**Design:** Add an explicit read-only check beside the training controls and beside the export source folder, with one shared result. Reuse the safe export source selection: prefer a complete `final`, otherwise the latest complete numbered checkpoint. Incomplete saves allow fallback; unsafe candidates and storage errors remain errors. Report no adapter when no complete layout exists, without creating files. An adapter's presence does not prove its training dataset, model quality, or resumable optimizer state.

**Architecture:** Extend the existing LoRA export contracts/service/router with `POST /api/lora-export/check`. The strict request contains only `lora_output_dir`; the response contains a nullable checkpoint with canonical source, adapter path and `final`/`checkpoint` kind. Keep filesystem work on the backend and serialize owned worker threads through the existing lock. Checking requires no running engine or GPU lease. Generate the frontend contract and parser from Pydantic.

**Scope:** Existing uncommitted progress and export safety repairs remain intact. No global run scanning, automatic training, checkpoint continuation, native engine modification, dependency, commit or push.

## Backend

- [x] Write and run failing service/API tests for final/latest checkpoint, missing/incomplete adapter, unsafe paths/symlinks, stopped/busy engine, stable errors and no writes.
- [x] Implement `LoraTrainedFilesRequest`, `LoraTrainedCheckpoint`, `LoraTrainedFilesResponse`, source inspection and the check endpoint in the existing export files. Reuse `_source` rather than duplicating selection or path rules.
- [x] Run focused backend regressions and strict mypy for Linux/macOS/Windows typing paths.

## Frontend

- [x] Write failing tests for explicit check, found/missing/error, stale results after folder/dataset changes, unmount cancellation and no training/export side effects.
- [x] Regenerate `frontend/src/api/generated.ts`; add the checked API function to `aceStepTraining.ts`.
- [x] Add a shared request/result state to `LoraTrainingPage.vue`. Clear/cancel checks when the folder or dataset changes and on unmount. Show the checkpoint path and a Go to export action only for a found adapter. Keep Start training's fresh-run behavior.
- [x] Add translated text to the existing LoRA export locale. Use existing button styles and accessible status/error messages.

## Verification and rollout

- [x] Run `npm test` and `npm run build` in `frontend/`, full backend regressions using temporary configuration/library/engine paths, generated-contract drift check and the CI strict Python platform loop.
- [x] Review the task diff for boundary validation, data preservation, types and scope. Verify the real current run read-only through the app endpoint; no new training or export.
- [x] Make the updated app available for testing. Restart only after checking active jobs; preserve any running work. Document actual checks and limits here.

## Review and live evidence

Independent review identified and confirmed fixes for incomplete saves hiding usable checkpoints and check/export source-text differences. The new source field button sits outside its input label; a shared atomic live region reports checking, found and missing states.

The live API and browser button both found the existing final adapter in the selected training run while ACE-Step was stopped. Source sizes/modification times remained unchanged; a missing-run request returned null without creating a folder, and traversal returned the stable 422 error. No new training or export was submitted. The idle engine and queue were checked before restarting the app. The running backend imported the final source-selection implementation.

Frontend: 165 test files / 1,531 tests passed, strict TypeScript and production build passed. Focused backend: 74 export/API/native/proxy/progress regressions passed. The final full backend suite ran 2,027 tests successfully with 10 skips, using isolated configuration/library/engine paths. All 21 strict-mypy commands across Linux/macOS/Windows typing paths and the generated-contract drift check passed. No desktop startup, packaging, native engine source or model files were changed by this feature; desktop tests were not repeated for this scope.

The browser verification screenshot is `/tmp/openfabric-trained-files-check-detail.png`. Adapter integrity/quality and GPU training were not evaluated. No commit or push was made.
