# Music LoRA implementation plan

**Goal:** Support native local adapter training and loading where the selected music runtimes support them.

**Architecture:** Preserve ACE's native workflow. Add a managed adapter library, supervised Stable training jobs, Stable inference selection and YuE AR/NAR session selection. HeartMuLa remains explicitly unsupported by its selected runtime.

**Tech stack:** Existing FastAPI/Pydantic, SQLite/private library directories, owned Python subprocesses, Vue/Pinia and generated contracts. No new dependency.

## 1. Adapter boundary (root ownership)

- [x] Add failing synthetic-header tests for managed import, native format/target mismatch, size bounds, symlink escapes, malformed manifests and changed checksums in `backend/app/music_lora_test.py`.
- [x] Define `MusicAdapter`, `MusicAdapterSelection`, `MusicAdapters`, `MusicLoraCapabilities` in `backend/app/music_lora_contracts.py`; selections use UUID IDs and strengths in [0,2].
- [x] Implement immutable atomic import/list/resolve/export in `backend/app/music_lora.py` and same-origin bounded routes in `backend/app/api/routes_music_lora.py`. Validate native safetensors metadata and shapes; never load pickle.
- [x] Add optional Stable adapter selection to `local_music_contracts.py`, resolve it in `local_music_runtime.py`, and save provenance in `local_music.py`; reject Heart selections.
- [x] Regenerate contracts and wire routes, training lifecycle, admission, migration and strict CI typing.

## 2. Stable training (exclusive worker ownership)

- [x] Read pinned native trainer/pre-encoder and existing owned-worker lifecycle. Add isolated regression tests before implementation.
- [x] Add `stable_lora_contracts.py`, `stable_lora.py`, `stable_lora_runtime.py`, `api/routes_stable_lora.py`, and `scripts/stable_lora_worker.py`.
- [x] Implement bounded track/caption requests, preflight base/T5/SAME encoder files, dataset copies via owned ffmpeg, real pre-encoding and bounded fresh-run native training. Persist requests and worker identity. Export only verified final checkpoint through managed adapter import.
- [x] Verify failures, descendant cancellation, recovery, publication interruption, admission and shutdown with mocked engine boundaries. No model downloads or full-state resume claim.

## 3. YuE loading (exclusive YuE ownership)

- [x] Add failing tests for AR/NAR managed selections, strength bounds, incompatible targets and clearing resident adapters.
- [x] Extend `yue_contracts.py`, `yue_jobs.py`, affected generation settings and frontend YuE form/store/tests. Resolve selection before session load, explicitly compare all four session options and persist provenance.
- [x] Use shared `MusicAdapterSelect.vue` with props engine/target and v-model `MusicAdapterSelection | null`. Preserve legacy settings/jobs and cancellation behavior.

## 4. Shared UI (exclusive frontend ownership)

- [x] Add typed `api/musicLora.ts` and `api/stableLora.ts`, `MusicAdapterSelect.vue`, `/music/lora` page and translated strings with tests for compatibility, import, training, cancellation and teardown.
- [x] Replace ACE-only sidebar entry gating with the shared LoRA page; preserve the ACE route. Add Stable generation selector and submission field.
- [x] Reuse library selection/upload and existing safe error/polling patterns. Show missing training weights and exact unsupported capabilities.

## 5. Review and verification

- [x] Review integrated diff, generated schema ripple, storage containment, worker ownership, admission and old request compatibility.
- [x] Run `npm test` and `npm run build` in frontend, isolated backend unittest discovery, contract generator `--check`, strict mypy Linux/darwin/win32 loop from CI and affected desktop tests.
- [x] Update `docs/local-music.md` and README with format, training weights, capabilities and hardware limitations. No commit/publish unless requested.

## Verification evidence

- Frontend: 1,369 tests and strict production build passed.
- Backend: full discovery ran 1,745 tests successfully (10 skipped); after final compatibility/cancellation corrections, 142 focused integration and regression tests passed.
- CI strict mypy commands: all 204 checked sources passed for Linux, macOS and Windows typing paths. This is checked-core coverage, not whole-backend typing.
- Generated contracts check and whitespace diff check passed.
- Desktop: 85 passed, one Windows-specific test skipped on macOS.
- Source audit and synthetic adapter/CPU lifecycle checks only; no real GPU adapter inference or training was performed.

The development app was reloaded on loopback ports 9000/5173, YuE was restored as the active engine, and the LoRA page plus capabilities, adapter library, training status and history returned HTTP 200. Stable runtime/weights are not installed. Live visual inspection was unavailable because computer-use reported no Chrome window; component tests and HTTP responses provide the UI/API evidence.
