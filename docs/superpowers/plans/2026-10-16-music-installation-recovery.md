# Music installation recovery implementation plan

> **For agentic workers:** Use focused agents for independent backend and recovery work; review the integrated result. Do not commit or push without a fresh branch selection from the maintainer.

**Goal:** Make Stable Audio's selected model readiness accurate, prepare its missing generation files, and recover engines under the user-selected data home without replacing either library.

**Architecture:** Extend the existing typed module plan/job lifecycle for an explicit Stable Audio generation variant. Reuse the setup review component and native pinned downloads. Use the existing library migration lifecycle for `OpenFabricStudioData/live/library`; stage and verify managed engines under `live/runtime`, preserving source data and the older backup.

**Tech stack:** Python/Pydantic/FastAPI, generated TypeScript contracts, Vue, SQLite, local APFS storage.

## 1. Selected Stable Audio model readiness and setup

Files: `backend/app/local_music_contracts.py`, `local_music_runtime.py`, `local_music.py`, `module_contracts.py`, `module_catalog.py`, `module_jobs.py`, `module_music_models.py`, `stable_lora_setup.py`, their existing regression tests, and generated `frontend/src/api/generated.ts`.

- [x] Add regression coverage before implementation: Small installed must not make Medium ready; training weights alone do not make generation ready.
- [x] Add `small_weights_ready` and `medium_weights_ready` booleans to the engine response. Retain aggregate `weights_ready` for engine catalogs.
- [x] Add optional `stable_generation_variant` to setup request/plan/job schemas. Reject mixed training/generation requests, another feature selection, reinstall, or disabled downloads. Include the selected variant in the immutable plan scope and persist it through cancel/reload/resume.
- [x] Plan and install only the selected generation bundle, preserving existing weights and runtime. Reuse the existing Stable Audio asset-plan code and validated downloader.
- [x] Verify Small's presence does not suppress a Medium setup plan, existing shared encoder files are reused, changed variant tokens are rejected, and cancelled downloads resume safely.
- [x] Reject missing selected weights at submission, retaining the worker-side check for files removed while queued.
- [x] Regenerate client contracts using `backend/scripts/generate_contracts.py`.

Example regression assertion:

```python
self.assertTrue(status.weights_ready)
self.assertFalse(status.model_dump().get('medium_weights_ready'))
```

Run focused unit tests in a temporary environment with `OPENFABRIC_CONFIG`, `OPENFABRIC_DATA_DIR`, `OPENFABRIC_MODULE_ROOT`, and `SEED_VC_DIR` all isolated.

## 2. Generator feedback and setup review

Files: `frontend/src/views/music/LocalMusicPanel.vue`, `MusicTrainingSetup.vue`, their tests, and `frontend/src/locales/localMusic.ts`.

- [x] Reproduce selecting missing Medium while Small is ready: generation must be disabled and selected-model setup offered.
- [x] Make readiness depend on the selected model. Show each model's readiness without changing a selected LoRA's matching base.
- [x] Reuse the existing setup card in generation mode; send `stable_generation_variant`, retain explicit plan review and progress, and refresh readiness after completion.
- [x] Abort stale reviews on model/mode changes and prevent late responses updating another selection. Preserve training behavior.
- [x] Verify setup completion, failed/cancelled setup, navigation cleanup, and no automatic generation.

Example UI expectation:

```typescript
await input(node, 'variant', 'medium')
expect(button(node, 'Generate music').disabled).toBe(true)
```

Run focused Vue tests, then frontend tests and strict production build.

## 3. Local recovery and validation

- [x] Confirm all generation, setup, queue and training jobs are terminal before shutting down or migrating.
- [x] Back up private configuration and obtain consistent SQLite backups outside Git. Preserve existing library/media/checkpoints until replacement verification is complete.
- [x] Migrate the current library to a fresh `live/library` destination using the existing migration API; never select the older `library` snapshot as the current catalog or overwrite it.
- [x] Stage the surviving runtime under `live/runtime`, verify/rebuild relocated environments, and update explicit stale engine paths only after replacements are prepared. Keep machine paths out of Git.
- [x] Restore pinned engine code and only necessary missing model files; reuse surviving video/Demucs weights. Restore backed-up ACE user data without guessing newer missing datasets or overwriting current entries.
- [x] Prepare the missing Medium generation model/decoder through the reviewed setup job. Check that the saved trained adapter is unchanged.
- [x] Restart only after active-work checks; verify inventory, selected model readiness, and a bounded local generation through the real API. Do not claim audio quality without listening or full GPU support without runtime evidence.
- [x] Run contract checks, required typing checks, relevant backend regressions, and frontend tests/build. Inspect final diff and report exact validation and any missing data after the cleanup.

## Recovery blockers verified on this host

- Large whole-body HTTP transfers repeatedly closed early. A 32 MiB range completed correctly; the shared downloader now bounds requests, keeps partial prefixes, retries each fixed window, and verifies the full pinned checksum before publication. A real 218,090,820-byte artifact completed and passed its checksum after the fix.
- The pinned YuE tar archive contains a legitimate root directory marker. Extraction now permits typed `.`/`./` directory entries while retaining member-count, size, path and file-type safety checks.
- Native Stable Audio Medium generation with the preserved trained adapter merged 168 layers and produced a five-second stereo 44.1 kHz PCM WAV. This verifies runtime execution, not listening quality.

- ACE initialization required unused default Turbo/LM weights despite an installed XL model. The maintained vendor patch now checks shared assets separately from the selected DiT and requested LM. Both relocated adapters loaded and activated through the real app, their original checksums remained unchanged, and unused defaults stayed absent from the live checkpoints.

## Verification and recovery limits

- Backend full suite: 2,289 tests run, 16 skipped; 71 focused regressions, including the six new ACE initialization tests, passed after the final vendor change.
- Frontend: 1,667 tests passed; strict TypeScript/Vite production build passed. Generated-contract check passed.
- Strict CI Python typing: all seven command groups passed for Linux, macOS and Windows; the changed installer/core group was rerun successfully for all three.
- Desktop: 87 passed, one platform skip. Independent source reviews found no blockers.
- The active library and managed runtime use new live subfolders; pre-migration backups and the older snapshot remain intact. Both moved ACE adapter originals are unchanged.
- Two newer dataset folders were not in the retained backup and remain honestly marked unavailable. Speech retains its existing NumPy/py3langid dependency conflict. Other engines' audio/video quality was not measured. Missing shared ACE assets retain the vendor's full-default-bundle download fallback.
- No commit or push performed for this recovery change.
