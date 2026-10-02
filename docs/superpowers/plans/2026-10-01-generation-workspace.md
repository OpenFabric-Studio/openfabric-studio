# Generation workspace implementation plan

> For agentic workers: use focused parallel agents with exclusive ownership and review each completed subsystem for spec compliance and quality. The existing AGENTS.md governs authorization; do not commit or publish as part of this implementation.

**Goal:** Deliver the approved fork improvements and durable configurable generation history with managed presets while preserving our existing functionality.

**Architecture:** Extend the current FastAPI/SQLite library and generated browser contracts. Native jobs, reference preparation and downloads have explicit owners, bounded inputs and recoverable publication. Reuse existing Vue components, audio versions, export settings and resource admission.

**Tech stack:** Python 3.12, Pydantic/FastAPI, SQLite, Vue 3/Pinia, strict TypeScript/Vitest, Electron/Node tests and pinned audio.cpp source.

## 1. Backend history and preset foundation

Files: new `backend/app/generation_contracts.py`, `generation_library.py`, `generation_library_test.py`, `api/routes_generation_library.py`; integrate `db.py`, `main.py`, `api/routes_tracks.py`, `ace_jobs.py`, and `scripts/generate_contracts.py` with owned hooks.

- [x] Define bounded history settings, immutable records and preset CRUD/duplicate/import contracts from actual current form and native parameter shapes.
- [x] Add SQLite migration and one-time legacy track backfill, record generated tracks idempotently, retain nullable track links after deletion, and prune metadata in deterministic order.
- [x] Add tests demonstrating default 100, changed limits, preserved media/presets, deletion, duplicate names, revision conflicts, invalid payloads, migration idempotence and safe legacy imports; run failing cases before implementation.
- [x] Expose generated API contracts and typed routes; run targeted unit tests, mypy and contract regeneration/check.

## 2. Generator and library UI

Files: `frontend/src/views/ace-step/GenerateForm.vue`, `views/yue2/GenerateForm.vue`, `views/yue2/Yue2Page.vue`, `components/shared/TagInput.vue`, new generation-library API/store/components and Settings controls; matching Vitest tests/locales.

- [x] Reproduce lost final tags and YuE restart draft loss, then fix with IME/suggestion/keyboard regressions.
- [x] Reset copied defaults, add caption/reference controls, and preserve current generator/voice/preset semantics.
- [x] Provide history/preset browse, filter, preview/reuse, save/rename/update/duplicate/delete and idempotent browser preset migration; setting changes never remove audio.
- [x] Add optional completion notification preferences and unread state, scoped to durable completed work with permissions/deduplication tests.
- [x] Run focused frontend tests and strict build, then integrate generated backend contracts.

## 3. Installer and desktop recovery

Files: `desktop/src/bootstrap/{components,checks,patch,run}.js`, `src/{main,preload,server}.js`, `renderer/{app,i18n}.js`, tests; model resume source patch and Linux setup scripts.

- [x] Test checkpoint conflicts and interrupted source swaps, then preserve all installed checkpoints on recovery.
- [x] Add immutable identity/content validation and compatible partial cleanup to resumable YuE model downloads; keep existing archive hashes/Range protections.
- [x] Enforce pending-only disk checks on every install path; validate IPC boundaries and version document URLs.
- [x] Add safe native notification bridge and atomic patch writes; retain version/branding and existing library layout.
- [x] Fix repeat Linux setup diagnostics/env preservation with isolated script regressions. Run desktop tests and source patch checks without executing installers against the user library.

## 4. Native YuE lifecycle, progress and memory

Files: new `backend/app/yue_jobs.py`, native progress contracts/routes and tests; `orchestrator/{manager,process}.py`, `resource_admission.py`, `config.py`; `frontend/src/api/yue2.ts`, `stores/yue2.ts`, `views/yue2/TrackCard.vue`; native source patches/build helpers.

- [x] Test cancellation before/after inference, stop failure, restart failure, queued work and stale run identities; implement owned stop/reset and admission without global unowned resets.
- [x] Parse bounded atomic snapshots into typed progress keyed by run identity; add store-owned polling with post-await generation checks.
- [x] Expose elapsed and known phase counts; derive ETA only from compatible measurements and use unknown otherwise.
- [x] Apply supported metadata arena options and inspect actual resident session configuration before reuse; test request option compatibility.
- [x] Adapt native progress/workspace patches and reproducible build commands for pinned engines; verify clean application/compilation where available and document actual platform limitations.
- [x] Optimize base64 conversion without interrupting owned playback; run backend/frontend regressions.

## 5. Stem exports

Files: `backend/app/audio_exports.py`, API routes and contracts where needed; `frontend/src/components/shared/StemsPanel.vue` and tests.

- [x] Add owned per-stem MP3 exports sourced from the exact original stem and global encoding settings.
- [x] Test invalid/missing sources, cancellation, output identity and failure states; preserve existing WAV/player controls.

## 6. Optional reference preparation and text tools

Files: new typed reference-import/preparation modules/routes/UI/tests and setup helper; preserve third-party license attribution.

- [x] Restrict provider URLs and validate redirects/resolved addresses before network access; preserve original bytes and bound duration/size/time/processes.
- [x] Persist unique staged import/preparation jobs and drain owned work before deletion/retry/cancellation.
- [x] Reuse existing separation and native melody services; expose optional lyrics/subtitles/Whisper stages with explicit missing dependencies and review before applying results.
- [x] Implement corrected bounded ABC/lyrics transformations and pitch-equivalence/bar/accidental tests; avoid the prototype's lossy MP3 master and generic arbitrary tool invocation.
- [x] Add UX regressions and isolated pipeline tests with mocked native/network boundaries.

## 7. Integrated review and verification

- [x] Review each subsystem for the design requirements and code quality; resolve gaps before marking complete.
- [x] Run `npm test` and `npm run build` in frontend; `npm test` in desktop.
- [x] With temporary OPENFABRIC_CONFIG, OPENFABRIC_DATA_DIR and SEED_VC_DIR, run backend unit discovery and generated contract check.
- [x] Extend/run the CI strict mypy platform loop for every new maintained backend module.
- [x] Review final diff for lost features, unsafe boundary types, unowned work, lossy provenance, branding or private-data changes.
- [x] Document actual checks and any unverified native hardware/model behavior. Leave changes reviewable on the feature branch without commits/pushes.


## Final verification — 2026-10-02

Implementation and integrated review were completed in the isolated `feat/generation-workspace` worktree. At that verification checkpoint, no changes had been committed or pushed and the primary `master` checkout remained clean. The maintainer subsequently authorized committing, merging and pushing on 2026-10-02. Tests and browser checks used temporary configuration, libraries and engine folders; no user models or media were changed.

- Frontend: 641 tests across 88 files passed; `npm run build` passed strict policy checks, TypeScript and Vite production compilation.
- Backend: full discovery ran 653 tests in 60.419 seconds, with 643 passing and 10 skipped. Six skips require real Windows Job Objects; four require optional installed video vendor checkouts/runtime components.
- Desktop: all 47 tests passed, including installer recovery, pinned model resume, notification boundary validation, Linux setup and packaged native-patch resources.
- Python: the CI strict scope of 75 modules passed for Linux, Darwin and Win32 typing paths. Generated contracts `--check` passed.
- Native source: complete patches validated on both official pins; an isolated macOS arm64 CPU source build compiled and passed server startup/health/model-list/device probes without weights. CUDA, Metal and Windows compilation and GPU generation were not tested.
- Browser against a real isolated backend: saved and reused generated lyrics/seed, created/renamed/duplicated presets, changed history retention to 120 and confirmed persistence after reload; no browser JavaScript errors were reported. A fresh application lifespan/API smoke also verified default 100, empty new history/preset lists, reference capabilities and the startup/shutdown admission gate.
- Final integrated review covered source preservation, bounded public inputs, path confinement, native/process ownership, failure recovery and concurrent deletion/retry. `git diff --check` and `bash -n setup_linux.sh` passed.

Optional provider imports, Whisper/SheetSage inference, operating-system notification delivery, real Windows kernel behavior and generated audio quality/performance still require installed tools/models and platform evidence. Detailed YuE native progress is available only with the validated optional patched source build; stock engines retain elapsed/stage status. These limitations are documented in the user guides and native setup instructions.
