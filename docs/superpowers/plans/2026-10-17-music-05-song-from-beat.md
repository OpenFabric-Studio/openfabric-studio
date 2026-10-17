# Song from Beat and Beat/Song Mixing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This planning artifact does not authorize implementation, installation, downloads, live jobs, migrations, commits, pushes or deployment.

**Goal:** Turn an immutable Beat Maker render into a model-conditioned arrangement, and combine a beat with an existing song through an editable, recoverable, musically aligned project.

**Architecture:** Add typed conditioning and retained source snapshots to the existing immutable music queue rather than create a second GPU scheduler. Offer ACE Complete/Lego only after pinned base-model capability proof, keep Cover/Remix a separate rewriting mode, and make Stable Audio audio-to-audio/inpainting an explicitly verified instrumental extension. Beat/song assembly uses shared analysis/DSP from plan 01, backend-rendered piecewise assets and the existing editor.

**Tech Stack:** Existing FastAPI/Pydantic/SQLite music queue and ACE jobs, isolated pinned ACE/Stable runtimes, owned process supervision, shared analysis/DSP, Vue 3/Pinia/vue-i18n/Vitest and existing timeline/mixer/export modules.

---

## Evidence, assumptions and delivery boundary

This plan covers report §§5.1, 5.5–5.6, 5.8–5.9 and phase 3. Complete the deterministic Beat Maker in plan 03 first. Analysis, retained-source identity, piecewise DSP, loudness/mastering and license records come from plans 00/01. Regional beat authoring remains plan 04's responsibility.

Verified from the current code, rather than the report's more optimistic summary:

1. `frontend/src/views/ace-step/GenerateForm.vue` exposes Complete/Lego/Extract only when the selected inventory entry lists a supported task. It submits source as `ctx_audio`, Complete classes as `track_classes` and Lego name as `track_name`.
2. `backend/app/generation_contracts.py` already stores `taskType`, `trackClasses`, `trackName`, model and reference settings. Those fields alone do not make the durable queue source-conditioned.
3. `music_queue_plan.py::_payload` constructs an ACE text-generation request. `music_queue_engines.py::_ace_params` explicitly rejects `taskType != 'text2music'` and `useRefAudio`; `EngineSession.submit` passes source `None` to `ace_jobs.submit`. Source handling must change across schemas, fingerprints, queue persistence and execution together.
4. `music_queue_store.py` has SQLite constraints/triggers protecting immutable payload, seed, adapters and attempt identity. Existing queue-control schema version is 1; any new conditioning field needs corresponding immutable protection and a real migration.
5. `module_music_models.py::_required_artifacts` accepts Turbo/XL-SFT only; `assets/music-models.json` contains the existing pinned artifacts. `config.py` reads `ACE_STEP_DIT_MODEL`, and module-plan tokens include selected config. Current automatic setup cannot honestly offer arbitrary base models without new pinned files and selection persistence.
6. ACE source is pinned by `desktop/manifest.json` at `ca1e85fe9430179831e6bc6be790c332190a3866`. Stable source pin in `module_install.py` is `77e0398c26f7d22d801ec1bb036307e8b231f741`. Current Stable adapter only passes text, model/decoder, duration, seed and optional LoRA to its MLX CLI; `LocalMusicRequest` has no audio-to-audio/inpaint fields.
7. `timelineTypes.ts` supports one uniform `originalBpm/projectBpm` stretch, not a variable tempo map. Existing mixer supports EQ/filter/compressor, but its settings have no cross-lane sidechain or automation.
8. The report's private-machine statement that XL-base shards already exist is not evidence about this checkout or its users. Do not reuse or overwrite a personal model directory based on that statement. HeartMuLa has since been removed from this app; do not expose it through this wizard.

Unverified prerequisites: exact Complete/Lego field semantics and supported track-class set at the pinned source, whether the installed base path supports those tasks on MLX, whether native outputs contain isolated additions or a complete regenerated mixture, and Stable MLX audio-conditioning/inpaint support at its pinned commit. Task 1 turns each unknown into a checked capability record before the UI enables it. Marketing text is not a test fixture.

“Keep the beat unchanged” has two meanings. Keeping source bytes retained is guaranteed by storage. Keeping those samples unchanged inside the final mix can only be guaranteed when the application mixes the original beat with separately returned additions and excludes generated drums. If the model returns a full mixture, label it “Generated arrangement conditioned on your beat”; never add the original on top and call the result faithful, because that may double its drums. No acceptance test should pretend sample correlation proves unchanged mixing.

## Proposed contract seam

New models are defined in `backend/app/song_from_beat_contracts.py` and generated into client contracts. Existing API names are identified above; everything in this section is proposed.

- `SongBeatSource`: `beat_id`, `beat_revision`, `render_id`, `audio_version_id`, `sha256`, `duration_sec`, sample rate, frame count, plus optional known BPM, meter and tuning provenance. Source must identify a completed plan-03 render, never a mutable browser URL.
- `QueueSourceSnapshot`: 32-hex `source_id`, immutable audio-version/render identity, byte hash, PCM hash if available, duration/sample metadata and complete license/provenance fingerprint. The private confined disk path is held in a server-only record, never accepted as public input.
- `SongFromBeatRequest`: source, title ≤120, style ≤3500, lyrics ≤20000, language ≤80, seed 0–2³¹−1, variations 1–8, selected verified model, `mode=ace_complete|ace_lego|ace_cover|stable_audio_to_audio|stable_inpaint`, requested duration and typed mode options. Discriminate mode options; do not put every possible field in an untyped dictionary.
- `AceCompleteOptions`: unique validated `track_classes` (1–16, exact allowed names established in Task 1), no class that duplicates already supplied source roles unless explicitly choosing regenerate mode.
- `AceLegoOptions`: one verified `track_name`; each layer produces a new immutable source/output revision before a later layer can queue.
- `AceCoverOptions`: `cover_strength` 0–1, explicit `rewrites_source=true`. Do not auto-fallback to Cover when a base model is unavailable.
- `StableAudioToAudioOptions`: strength semantics/range fixed from inspected pinned runtime; input/output duration within its measured model/variant capability; instrumental only.
- `StableInpaintOptions`: finite ordered `start_sec<end_sec`, bounded duration, padding/crossfade policy and context identity; instrumental only. “Continue” is a UI operation that creates a validated tail region plus context, not a guessed CLI task name.
- `SongFromBeatPlan`: revision/fingerprint, immutable source snapshot, task/model/engine identities, lyrics/style, duration/BPM/key adjustments, runtime readiness, warnings, output semantic capability and exact queue item snapshots.
- `BeatSongMixPlan`: immutable beat/song source IDs, analysis IDs, `mode=layer|replace_drums`, `tempo_strategy=beat_to_song|song_to_beat`, chosen bass/kick leadership, explicit source/destination beat anchors, pitch strategy, duck/EQ parameters, section placements, revisions and provenance.

The shared `AnalysisResult` in plan 01 has `schema_version=1`, stable `id`, source `MusicSourceRef`/audio hash, `*_sec` coordinates, nullable/unknown tempo, meter and key. Do not force a Western key onto a regional tuning. Use its exact implemented types rather than duplicate analysis fields in this plan. Shared DSP models are `DspTransformRequest`, `DspMixRequest` and `DuckSettings`; callers own their subprocesses and durable workflow state. Plan 01 does not introduce a generic workflow scheduler.

## File map

Create `backend/app/song_from_beat_contracts.py`, `song_from_beat.py`, `song_sources.py`, `song_capabilities.py`, `beat_song_mix.py`, `backend/app/api/routes_song_from_beat.py`; tests with the same `_test.py` names and `backend/app/api/routes_song_from_beat_test.py`. Create source-only pinned regression fixtures under `backend/app/fixtures/song-conditioning/` with README and upstream license attribution; no model weights/audio corpus in Git.

Modify `backend/app/music_queue_contracts.py`, `music_queue_plan.py`, `music_queue_store.py`, `music_queue_engines.py`, `music_queue.py`, corresponding existing tests, `ace_jobs.py`/tests, `generation_contracts.py` where snapshot metadata requires it, `local_music_contracts.py`, `local_music_runtime.py`, `local_music.py`, `backend/scripts/local_music_worker.py` and their existing tests. Modify `module_contracts.py`, `module_catalog.py`, `module_install.py`, `module_jobs.py`, `module_music_models.py`, `config.py`, `assets/music-models.json`, `module_licenses.py`, `data_root.py`, `api/routes_modules.py`, `api/routes_settings.py` and setup/settings tests. Modify `db.py`, `main.py`, `work_busy.py`, generated-contract script, `.github/workflows/ci.yml`, export-provenance and the plan-03 `beat_projects.py` integration.

Create `frontend/src/api/songFromBeat.ts`, `frontend/src/api/songFromBeat.test.ts`, `frontend/src/stores/songFromBeat.ts`, `frontend/src/stores/songFromBeat.test.ts`, `frontend/src/views/beats/SongFromBeatPage.vue`, `frontend/src/views/beats/SongFromBeatPage.test.ts`, `frontend/src/views/beats/BeatSongMixPage.vue`, `frontend/src/views/beats/BeatSongMixPage.test.ts`, `frontend/src/locales/songFromBeatWorkspace.ts`. Modify Beat Maker action/export panel from plan 03, `frontend/src/router.ts`, `frontend/src/components/shared/AppSidebar.vue`, `frontend/src/views/music/MusicQueuePage.vue`, `frontend/src/views/settings/SettingsPage.vue`, `frontend/src/views/settings/ModuleSetupPanel.vue`/test, `frontend/src/views/settings/ModuleSetupJob.vue`, `frontend/src/api/modules.ts`/tests, `frontend/src/views/ace-step/GenerateForm.vue`/tests, `frontend/src/i18n/index.ts`, existing editor help/link/parser tests. `SettingsPage.vue` embeds `ModuleSetupPanel.vue`; keep model selection there rather than create a second settings panel.

## Task 1: Prove exact native tasks and output semantics before integration

**Files:** Create `song_capabilities.py`, `song_capabilities_test.py`, `fixtures/song-conditioning/README.md` and reviewed source fixtures; `docs/music-song-from-beat-proof.md`.

- [ ] Read the pinned ACE source tree from the manifest commit through GitHub/raw source or a temporary source-only checkout. Inspect the actual `/release_task` upload parser, task dispatcher, Complete/Lego/track class validation, model inventory, base-model MLX preparation and result writer. Do not execute imported model code during this inspection.
- [ ] Record exact paths/line references, accepted field types, source sample-rate/duration preparation, required model IDs and whether the output is one generated track, additions or a full mixture. Preserve upstream terms for any fixture. If report claims differ from code, update the capability record and this task's adapter design before implementation.
- [ ] Inspect pinned Stable `optimized/mlx/scripts/sa3_mlx.py` and its pipeline definitions for actual audio-conditioning/inpaint arguments and mask semantics. If absent, inspect a specific upstream revision that implements them; propose a separate reviewed pin update with dependency/license/LoRA compatibility tests. Do not invent `--input-audio` or `--inpaint` flags.
- [ ] First write offline mocked-dispatch tests that verify a source is decoded and used by each task, classes are parsed with the native expected type, the selected base path reaches the task implementation and result semantics are identified. The test must fail if a text-only method receives the source but ignores it.
- [ ] Implement proposed `SongTaskCapability` fields: engine/source commit/model variant/task, supported runtime/platform, accepted source limits, accepted track names, input-conditioning evidence, output semantics `isolated_addition|separate_additions|full_mix|unverified`, proof status `source_verified|runtime_verified|unverified` and proof artifact hash. Runtime evidence is distinct from structural artifact presence.
- [ ] Run `python -m unittest app.song_capabilities_test -v` offline. Runtime proof on M4 Max is a later explicit manual acceptance task; until then UI says “Source reviewed; generation unverified,” and unsupported modes remain disabled with their concrete reason.

## Task 2: Add base-model setup without changing existing model selection silently

**Files:** Modify module contracts/catalog/install/model artifacts/config/settings paths, `module_music_models_test.py`, `module_contracts_test.py`, setup/installation tests and frontend setup tests.

- [ ] Write a failing `module_music_models_test.py` case for a selected `acestep-v15-base` bundle containing every required indexed shard, config, tokenizer/embedding, VAE and selected language-model artifact. Add a distinct XL-base case only if Task 1 proves its task support; no assumption that “XL” means compatible.
- [ ] Resolve model publisher repository revision, exact filenames, byte counts and SHA-256 from primary artifacts. Add `ace-base` and, after evidence, `ace-xl-base` bundle IDs with immutable `/resolve/<40hex>/` URLs and truthful license records. Shared components reuse existing entries; do not duplicate/download the complete Turbo bundle.
- [ ] Extend `ModulePlanRequest/ModulePlan` with a typed nullable `ace_generation_model` selection, constrained to `features=['ace_step']` with model downloads and no contradictory Stable/training choices. Thread it through plan-token hashing, byte estimates, destination disk checks, installer dispatch, response echo and frontend parser. Otherwise a reviewed preview could install a different model.
- [ ] Persist a validated `ace_generation_model` config key with `data_root.read_config`/`write_config` under the existing config transaction lock, preserving every unrelated config key. Add a narrow public read/update function in `data_root.py` rather than use the artist/album settings document or edit `.env`. `ACE_STEP_DIT_MODEL` remains an explicit environment override and wins with a visible explanation. Update `config.py` launch config, module inventory readiness and queue selected-model checks to use the same resolved selection. Changing a model while an owned queue/ACE/LoRA job is active returns a conflict; it does not restart that engine underneath the job. Resolve the selected configuration at owned launch time rather than expect a previously imported constant to refresh itself.
- [ ] Implement `_required_artifacts` by a validated model-to-bundle mapping, including source/config compatibility and all real shards referenced by indexes. Reuse matching existing files only after integrity evidence; never overwrite unknown existing files or assume the maintainer's private hashes match the manifest.
- [ ] Test config changes invalidate old plan tokens, base selection survives restart, environment override is visible, external/unmanaged installs are not rewritten, interrupted downloads resume safely and wrong shard/hash disables Complete. Keep Turbo/XL-SFT behavior backward compatible.
- [ ] Run `python -m unittest app.module_contracts_test app.module_music_models_test app.ace_model_initialization_test -v` plus the affected existing module/setup test files located by `rg --files backend/app | rg 'module.*test|setup.*test'`.

## Task 3: Immutable owned source snapshots and retention

**Files:** Create `song_sources.py`, `song_sources_test.py`; modify `db.py`, audio-version retention/deletion integration and beat render/source link tests.

- [ ] Write tests for source change after preview, path/symlink escape, deleted library track, cancellation during copy, simultaneous same-content snapshots, disk full, restart after fsync before DB insert, retained-source garbage collection and source file tampering before dispatch.
- [ ] Add additive `song_source_snapshots` and `song_source_holds` tables. Store immutable identity/hash/sample metadata/manifest JSON in the snapshot row; holds refer to queue item, preview reservation (short bounded expiry), song layer, rendered project or export. Use unique content+manifest identities and foreign keys. Old tracks and projects remain unchanged.
- [ ] Proposed `snapshot_beat_source(source: SongBeatSource) -> QueueSourceSnapshot` resolves a completed render by ID, validates its retained original, checks requested revision/hash, copies/reflinks into an owned staging file, hashes bytes while reading, probes/decodes within limits, fsyncs and publishes by immutable source ID. Never accept browser path/URL or use hard links to mutable source media. At admission retain ≤512 MiB and ≤300 s per source for ACE, further clamped by the exact task capability.
- [ ] Atomically create the source hold and enqueue item; a failed queue transaction releases only its own new hold. A race between preview and enqueue either pins the same snapshot or returns `song_source_changed`; it never substitutes the current latest beat.
- [ ] `open_snapshot(source_id)` rechecks confined path, size and hash before upload; return a managed binary handle whose lifetime includes the complete native submit. A changed/missing snapshot blocks the item and preserves references for diagnosis. Retry keeps the original source/model/seed snapshot.
- [ ] Source deletion in the UI distinguishes original library track removal from retained queue/project sources; retained copies are removed only when no holds exist and no owned worker reads them. Test last-hold cleanup with a pending writer and an interrupted delete.
- [ ] Store assets beneath `db.FILES_DIR / '_song_sources'` and workflow output beneath `_song_mixes`, with root-relative asset IDs. Register any absolute receipt/path JSON columns in `data_root.py` relocation and its migration tests; do not add untracked top-level library roots. Add these consumers to existing audio-version/stem deletion/replacement guards and test idle library relocation plus restart.
- [ ] Run `python -m unittest app.song_sources_test -v` against temporary libraries/modules/engines.

## Task 4: Typed conditioned planning and backward-compatible queue migration

**Files:** Create `song_from_beat_contracts.py`, tests; modify all music queue contract/plan/store files/tests and contract generation.

- [ ] Start with regression tests proving a current text-only queue fixture reads and runs unchanged, a retired HeartMuLa row remains visible only through existing incomplete-history behavior, and conditioned payload changes cannot bypass immutable SQL triggers.
- [ ] Add proposed API routes in `api/routes_song_from_beat.py`: `POST /api/song-from-beat/preview` accepts a typed draft and returns an immutable `SongFromBeatPlan`; `POST /api/song-from-beat` requires its fingerprint, expected revision and idempotency key and returns the existing queue snapshot; `GET /api/song-from-beat/{id}` returns the durable workflow/result; `POST /api/beat-song-mixes/preview` and `POST /api/beat-song-mixes` provide reviewed mix-plan creation/admission; `GET /api/beat-song-mixes/{id}`, `POST .../{id}/cancel` and `POST .../{id}/retry-save` observe/control the owner. Native conditioned cancellation uses existing queue item control endpoints rather than a competing cancellation registry.
- [ ] Register routes before catch-all proxy routing and require local-origin checks on new mutating native/setup endpoints through the existing `module_security.require_local_origin` pattern. Test traversal/forged source IDs, stale revision, unknown mode fields, nonfinite masks, text-only legacy requests, same-key conflict and raw-error suppression in `api/routes_song_from_beat_test.py`.
- [ ] Define typed mode option models from the interface section and a discriminator on `mode`. Add optional `conditioning` to `MusicQueuePreviewRequest`, normalized items and preview; its default is `None`. A `SongFromBeatRequest` translates into this queue request on the backend, so the wizard is a specialized queue client.
- [ ] Keep the legacy fingerprint/canonical JSON path exactly unchanged when conditioning is absent. For conditioned requests use `fingerprint_schema=2` and canonical source snapshot ID/hash, native task/model identity, task options, exact lyrics/style/seed and output semantics. The seed is resolved once per variation before enqueue. Never hash a temporary path or mutable timestamp as source identity.
- [ ] Add queue schema migration 1→2 in one transaction: preserve all legacy JSON bodies verbatim, replace the immutable-item trigger to compare `json_extract(body_json,'$.conditioning')` in addition to every existing protected field, record schema version 2 and create source-hold references. Do not rewrite old immutable payloads just to populate null defaults. Startup must recognize versions 1/2 and reject unknown newer versions without deleting rows.
- [ ] Test migration interrupted at every DDL boundary rolls back and retries, fixtures missing optional fields parse with defaults, duplicate idempotency submissions after upgrade remain duplicate, old retries retain seed/adapters, and edited conditioning gets a SQLite abort.
- [ ] Use a minimal contract test before implementation:

```python
from unittest import TestCase
from pydantic import ValidationError
from app.song_from_beat_contracts import AceCoverOptions, StableInpaintOptions

class SongOptionsTests(TestCase):
    def test_cover_is_explicitly_rewriting(self) -> None:
        options = AceCoverOptions(cover_strength=0.6)
        self.assertTrue(options.rewrites_source)
        with self.assertRaises(ValidationError):
            AceCoverOptions(cover_strength=0.6, rewrites_source=False)

    def test_inpaint_region_must_advance(self) -> None:
        with self.assertRaises(ValidationError):
            StableInpaintOptions(start_sec=4.0, end_sec=3.0)
```

- [ ] Run `python -m unittest app.song_from_beat_contracts_test app.music_queue_plan_test app.music_queue_store_test -v`; regenerate contracts and run the drift check. Reject unsupported task/engine selection combinations in the backend, even if a user bypasses the UI.

## Task 5: Exact ACE task dispatch with source and native ownership

**Files:** Modify `music_queue_plan.py`, `music_queue_engines.py`, `ace_jobs.py`, existing tests; create `song_from_beat.py`, `song_from_beat_test.py`.

- [ ] Write a failing queue adapter test asserting a Complete item produces `task_type='complete'`, validated `track_classes`, pinned model, seed and an actual retained-source multipart `ctx_audio`; a source-free or Turbo Complete item must fail before native submit. Add Lego and explicit Cover tests. Also assert a text2music item still passes `audio=None`.
- [ ] Extend `_payload` and `_ace_params` with a strict branch for supported conditioned modes. Replace the blanket text-only rejection with explicit invariants per task; never forward arbitrary user-native dictionaries. Use `AceGenerationSettings(useRefAudio=True, taskType='complete'/'lego'/'cover', ...)` consistently with the source snapshot held outside those settings.
- [ ] Under the queue's existing reservation, `EngineSession.ensure` loads the selected verified base model using only the native API proven in Task 1. If the native API cannot hot-switch it reliably, change the server-owned launch config and restart the exact owned instance before dispatch, after draining prior child jobs. Recheck loaded model and supported tasks under ownership after loading; structural file presence alone is insufficient. Task-specific LM/encoder requirements come from that proof, replacing `_prepare_ace`'s current text-caption-only assumption without loading an unnecessary LM for ordinary text queue items.
- [ ] Build a managed source upload from `song_sources.open_snapshot`; pass it as the `audio` argument to `ace_jobs.submit`, with `voice_id=None` and no style audio unless a separately validated mode needs it. Keep file and hold alive until native acceptance is recorded; close them under cancellation-safe cleanup. Separate source-conditioning from optional style-reference semantics.
- [ ] Retain accepted native child ID before any awaited lease-release/cancellation boundary, as the existing adapter already does. Model/adapter mismatch, source hash mismatch or unproven native acceptance returns a stable failure and never auto-resubmits; uncertain acceptance becomes interrupted/blocked according to existing ownership rules.
- [ ] Persist task/source/model/engine proof identities with the child and saved track metadata. Add generation-history metadata only through typed contracts; do not overload `referenceImportId` with a beat-render ID from a different namespace.
- [ ] Run `python -m unittest app.music_queue_engines_test app.music_queue_ownership_test app.music_queue_test app.ace_jobs_test app.song_from_beat_test -v` with a fake native service checking multipart contents.

## Task 6: Complete/Lego result assembly and truthful layering

**Files:** Modify `song_from_beat.py`, tests, `beat_projects.py` from plan 03, export provenance and saved-track metadata.

- [ ] Test native results representing `isolated_addition`, `separate_additions`, `full_mix` and an unknown/unverified format. Use distinct impulse source/addition fixtures to catch accidental double-mixing and per-lane gain errors.
- [ ] Complete produces a generated arrangement and stems/project only through Task 1's verified output contract. If it returns full mix only, publish that mix, retain the original beat as a muted reference lane and label the source retention accurately. Optional separation yields estimated stems with separator/checkpoint provenance; it does not prove the original beat was preserved.
- [ ] For isolated additions, mix original source bytes as one unprocessed lane with the addition lanes through the shared backend mix/master pipeline. Record source lane gain/pan/master processing; “source unchanged” refers to that lane before master processing. Never mix original beat into a full model mix automatically.
- [ ] Lego adds exactly one verified track. “Add next layer” snapshots the reviewed result as a new source ID/revision and creates a new queue item; it does not mutate an existing item's payload or append files while another item reads them. Save source roles to prevent unknowingly stacking a second bass/drum part.
- [ ] Keep original drum stem/sample-accurate lane available for comparison. Provide section A/B, level matching and original/generated solo controls; quality and rhythmic fidelity are measured, not inferred from the task name.
- [ ] Build an editable editor project using plan 03's `beat_projects.build_audio_project`/`publish_audio_project`, with one actual available stem per lane, model output mix/reference lane muted as appropriate, placement labels, analysis IDs and source/transform manifests. Reuse `RenderedProjectLane` and `MusicProjectProvenance` rather than add another exporter. New library tracks retain originals through existing audio versions; no new version kind is needed.
- [ ] Run `python -m unittest app.song_from_beat_test app.beat_projects_test -v`; verify every saved project reloads without native service running.

## Task 7: Optional Stable Audio audio-to-audio/inpaint extension

**Files:** Modify local music contracts/runtime/job/worker and tests, music queue contracts/plan/engines/tests; extend `song_capabilities.py` and wizard.

- [ ] This is a gated extension, not a dependency for ACE/beat-song mixing release. First satisfy Task 1's pinned-MLX proof. If the current pin lacks audio support, keep the feature disabled and propose an isolated runtime update; do not copy undocumented flags into `prepare_worker`.
- [ ] Add a discriminated `task=text_to_audio|audio_to_audio|inpaint` to `LocalMusicRequest` with text-only default, typed source snapshot/region/options for the latter tasks and backward-compatible saved-request parsing. Instrumental-only remains enforced, including empty/nonempty lyrics and a vocal request that bypasses UI.
- [ ] Update `local_music_runtime.prepare_worker` to build the exact inspected CLI or pinned direct Python adapter invocation; the source path is resolved server-side and kept within the job root. Pass encoder assets required for conditioning, not only the decoder used by text-to-audio. Add their pinned artifacts/bytes/license records and setup readiness in `module_music_models.py`.
- [ ] Extend worker command validation with explicit typed source IDs/resolved confined files, task/mask data and limits. Preserve stdlib-only wrapper isolation; model imports stay in the engine environment. Set offline environment after setup. Source duration/strength/mask units and output bounds follow verified native semantics.
- [ ] For inpainting, save original outside the mask and construct the final result using original PCM outside the replacement region plus a documented short crossfade inside the allowed boundary. This application assembly provides an actual invariant the model does not promise. For continuation, define the new tail interval, retained context length and final duration; never erase/rewrite earlier beat audio unless user chose the audio-to-audio mode.
- [ ] Add regression tests for mask boundary sample identity, invalid region/strength, unsupported variant/runtime, required encoder missing, worker cancellation, source changed, queue retry using same source, and old text generation. Model boundary is mocked; no weights are downloaded.
- [ ] Run `python -m unittest app.local_music_test app.local_music_runtime_test app.music_queue_engines_test app.song_capabilities_test -v`; run the affected worker tests located by `rg -n 'read_command|local_music_worker' backend/app/*_test.py`. Record real M4 Max inference separately before enabling a runtime-verified badge.

## Task 8: Beat plus song planning, roles and analysis review

**Files:** Create `beat_song_mix.py`, `beat_song_mix_test.py`; extend song contracts/routes, source snapshots and shared analysis callers.

- [ ] Add failing tests for song/beat sources with fixed tempo, live tempo drift, unknown beats/downbeats/key, absent separated drums, pitched beat in an incompatible tuning, and user-edited beat anchors.
- [ ] Build source snapshots for the original song and beat, request shared analysis by audio-version/hash and retain analysis IDs. Display BPM confidence, downbeat/meter evidence and key/tuning uncertainty. Unknown analysis opens a manual bar/beat anchor editor; no guessed 120 BPM or automatic Western key conversion.
- [ ] Offer `layer` without separation as the simple path. `replace_drums` requires a completed separator output with original song drums and complementary stems; never implement it by EQ-notching the full mix. Store separator model/checkpoint/hash and measured alignment. Original song/stems remain retained.
- [ ] Default `tempo_strategy=beat_to_song` for a drifting live song. `song_to_beat` is explicit and shows the measured maximum/local stretch ratio with a preview. Keep half/double-tempo interpretation as a reviewed choice, not an invisible “fix.”
- [ ] For pitched Beat Maker lanes, choose a musically compatible root/scale with the user using actual known tuning. Prefer symbolic transpose/scale mapping and rerender through plan 03; save a new beat revision/render. Drums stay unchanged. For an audio-only beat or unknown scale, present shared DSP pitch-shift as an explicit approximation with limits and original comparison.
- [ ] Plan bass/kick leadership separately: `song|beat|none`; user can mute one bass, low-cut the nonleading part or duck it. Store all choices in `BeatSongMixPlan` before rendering; no hidden “AI mix” values.
- [ ] Run `python -m unittest app.beat_song_mix_test -v`; show a precise plan summary with source versions, duration, alignment anchors, transpose, replace/layer and estimated disk use before enqueue.

## Task 9: Piecewise warping and boundary-safe beat assembly

**Files:** Modify `beat_song_mix.py`/tests; use shared plan-01 time-stretch/DSP modules, add no second stretch engine.

- [ ] Write a synthetic click-track regression with song beat anchors at 0.0, 0.50, 1.01, 1.53 and 2.06 s, demonstrating that one global BPM ratio drifts while piecewise mapping aligns every reviewed anchor. Check sample positions at joins and tails, not only project metadata.
- [ ] Map each source beat/bar interval to the selected song interval using monotonic piecewise source/destination anchors. Reject duplicate/reversed/nonfinite anchors, zero spans, missing ends and ratios outside reviewed bounds; preserve the original plan and return `beat_mix_alignment_invalid` rather than silently clamp timing.
- [ ] Use the shared pitch-preserving stretch implementation per continuous span with adequate context, overlap/crossfade joins and sample-count reconciliation. Prefer one continuous piecewise-capable process if the actual library supports it; verify its API rather than invent an option. Across lanes use identical mapping/latency compensation so stems remain coherent.
- [ ] Loop source beat only at legal bar/chain boundaries and account for sample tails/chokes; record source occurrence and destination placements. Render ≥one context bar around internal joins where available. Handle partial pickup/outro bars explicitly in the plan and preserve song length.
- [ ] Bake variable-tempo assets on the backend and set imported clips `warpEnabled=false`; the browser's current `originalBpm` scalar is insufficient. Retain typed warp anchors in `BeatSongMixPlan` and editor provenance so rerender/edit is recoverable. A future visual warp editor can consume those anchors without changing this audio.
- [ ] Measure downbeat residual against known synthetic anchors and a held-out human-reviewed set. Adopt the report's ≤20 ms as a proposed screening threshold for grid lock, not a guarantee over unreliable detections. Report uncertain/failed analysis separately.
- [ ] Run `python -m unittest app.beat_song_mix_test -v` and existing DSP regression tests from plan 01. Check no lane length differs by a frame after assembly.

## Task 10: Sidechain, low-cut/EQ, loudness and identical preview/export

**Files:** Modify shared DSP/mix modules through plan 01, `beat_song_mix.py`/tests, export/provenance and editor handoff.

- [ ] Test deterministic envelope and gain reduction on synthetic kick/bass signals, attack/release continuity across blocks, stereo-linked detection, silence, NaN input, true-peak ceiling, cancellation and a transient exactly on a warp boundary.
- [ ] Reuse plan 01's typed `DuckSettings` in `DspMixRequest`: source input ID, threshold −60…0 dB, ratio 1–8, knee 0–12 dB, attack 1–100 ms, release 20–2000 ms, reduction 0–12 dB. `BeatSongMixPlan` adds detector role/target lane IDs around that shared payload; absence means disabled. Reuse shared EQ/filter settings for a low-cut of 20–500 Hz with the documented slope. A reviewed “avoid competing bass” preset may enable 3 dB maximum duck/100 Hz cut with visible values.
- [ ] Use shared DSP's deterministic stereo-linked envelope/compressor and EQ, driven by selected isolated kick/appropriate detector signal. Do not claim kick sidechain from a mixed full song without an actual detector, nor use the browser compressor as if it had an external sidechain input.
- [ ] For the initial editor handoff, render processed lane assets and keep the dry stems/reference plus the immutable mix plan. Import processed lanes with default mixer effects to prevent double-processing. Provide “Edit mix plan and rerender”; do not silently discard automation/ducking on reload.
- [ ] If later editable cross-lane automation is required, add one typed backend/frontend envelope representation and teach timeline live/offline rendering, parser, undo, serialization and export together. This is an explicit later task only after baked-plan re-edit satisfies baseline; no decorative automation sliders with no render path.
- [ ] Apply shared LUFS/true-peak policy after mixing and retain target/measurements. Preview plays the exact server master artifact; stems are aligned pre-master or post-lane-processing as labelled. Limiting means stems need not sum exactly to the final master; record master settings and avoid a false null-test claim.
- [ ] Run `python -m unittest app.beat_song_mix_test -v` and shared loudness/DSP tests. Add frontend test that reopening a processed project does not apply stored browser EQ/duck a second time.

## Task 11: Durable mixed-workflow orchestration, cancellation and recovery

**Files:** Modify `song_from_beat.py`, `beat_song_mix.py`, queue/ownership/source integration, `main.py`, `work_busy.py`, tests and routes.

- [ ] Test CPU analysis/render phases, GPU separation/generation phases and their boundaries independently. Follow the repository's lifecycle/receipt conventions in `song_from_beat.py`/`beat_song_mix.py`; plan 01 owns its analysis jobs and runs DSP under the caller. The existing music queue owns GPU-conditioned generation and existing separator owns its jobs. Do not hold a GPU lease while waiting for user anchor review or copying a large source.
- [ ] Persist immutable plan before start and stage checkpoints after snapshot, analysis, separation, native generation, warp, mix and publish. Reuse completed outputs by source/task/plan fingerprint; restarting an app must not silently regenerate a song with a fresh seed or duplicate library tracks.
- [ ] A cancelled job terminates/awaits exact owned worker descendants, releases source holds only after readers stop and preserves completed publishable artifacts. Unproven native cleanup keeps queue blocked. Failed final DB linking exposes retry-save; do not rerun models because the final insert failed.
- [ ] Distinguish `queued`, `snapshotting`, `analysing`, `separating`, `loading`, `generating`, `warping`, `mixing`, `publishing`, `done`, `cancelling`, `cancelled`, `interrupted`, `blocked`, `failed`. Native steps without real progress remain indeterminate. Backend logs retain internal details; public codes are stable.
- [ ] Add source/task-specific codes `song_source_changed`, `song_source_missing`, `song_task_unsupported`, `song_base_model_required`, `song_model_changed`, `song_runtime_unverified`, `song_output_semantics_unverified`, `song_conditioning_invalid`, `song_native_acceptance_unproven`, `beat_mix_drums_required`, `beat_mix_alignment_invalid`, `beat_mix_key_review_required`, `song_disk_full`, `song_save_failed`, `song_cleanup_failed`. Existing queue errors stay compatible; map new codes in frontend locales without exposing raw exception text.
- [ ] Bound one active conditioned native item using existing queue admission, variations 1–8, source bytes/duration per capability, ≤600 s final CPU mix, ≤32 source lanes and queue's existing history capacity. Estimate source copies+intermediates+aligned stems+master+archive with 25% overhead plus 512 MiB and verify all destination filesystems before admission.
- [ ] Run targeted source/queue/ownership/mix/API tests and explicit simulated crash recovery. No background job may depend on browser polling to save its result.

## Task 12: Song wizard, beat/song mix UI and existing generator alignment

**Files:** Create frontend API/store/pages/locales/tests; modify Beat Maker, router/sidebar, queue page, existing GenerateForm and setup UI/tests.

- [ ] Write failing interaction tests for the actual flows: frozen beat source→engine readiness→mode/options→plan review→enqueue→reload/result; and beat+song→analysis/anchor review→layer/replace→lead bass/kick→render→open editor. Include an edit arriving while preview/save is pending.
- [ ] “Make a song” from Beat Maker requires a completed selected revision. If unsaved edits exist, provide the concrete save/render action and label which source will be used. The wizard never silently uses the last exported beat with newer settings displayed.
- [ ] Show Complete (“arrange missing parts”), Lego (“add one track”), Cover (“rewrites source”) and Stable instrumental modes as separate choices, each with exact model/setup/runtime reason when unavailable. The Complete button navigates to the selected base setup plan, including bytes/license/disk evidence; it does not download in the background merely because the user opens the wizard.
- [ ] Lyrics/language/style/instrumental controls are task-aware; unsupported vocal requests on Stable are rejected both in UI and backend. Track classes come from the proven capability set; duplicate/source-role conflicts are explained. Source/rerender/adapter/model identity changes invalidate the preview fingerprint.
- [ ] Store uses generated parsers, AbortControllers, session/revision tokens and existing queue polling. Leave queued jobs running across navigation; stop view polling on unmount; reject stale callbacks for another source. Completed results offer A/B and separate mute/solo lanes with level-matched comparison.
- [ ] Update `GenerateForm.vue` to share truthful base-task availability/model wording with the wizard, without rewriting its existing generation form. Show when its selected model differs from queue setup/default and retain current LoRA gating. Missing base support must not select Cover automatically.
- [ ] Keep tab order, visible labels, focus return from setup/help, keyboard anchor edits, transport shortcuts and `aria-live` stage/error summaries. Provide translated help for source retention versus generated fidelity, layer versus replace, uncertainty, microtonal pitch mapping, piecewise warp, masks/continuation, sample/master processing and export rights. Register all locale entries through existing conventions.
- [ ] Run `npm test -- src/api/songFromBeat.test.ts src/stores/songFromBeat.test.ts src/views/beats/SongFromBeatPage.test.ts src/views/beats/BeatSongMixPage.test.ts src/views/music/MusicQueuePage.test.ts src/views/ace-step/GenerateFormSettings.test.ts src/views/ace-step/GenerateFormVoiceReplacement.test.ts` from `frontend/`, then `npm run build`.

## Task 13: Measured platform proof and full integration verification

**Files:** `.github/workflows/ci.yml`, `docs/music-song-from-beat-proof.md`, `docs/music-song-from-beat.md`, `ROADMAP.md`; affected desktop packaging/startup tests.

- [ ] Extend the existing three-platform strict mypy loop with new song/source/capability/mix/routes modules; keep existing queue/local-music checks. Run all checks in the isolated environment below.
- [ ] With explicit real-runtime authorization and a temporary library, prove Complete and Lego on M4 Max using the reviewed base model and a user-owned synthetic beat. Record machine RAM, OS, source/engine/model hashes, options, wall time, peak memory, cancellation evidence, source/output frame counts, failed runs and output semantics. Source inspection alone does not establish MLX generation support or musical quality.
- [ ] Where available, test optional Stable audio-to-audio/inpaint using its actual pinned MLX adapter/encoder and inspect exact preserved regions. Keep unavailable platforms/modes labelled unverified. Do not infer Windows/Linux GPU success from the typing loop or Mac inference.
- [ ] Evaluate at least ten held-out beats spanning supported meters/tempos, pitched/regional tuning, sparse/dense drums and one live-tempo song. Review grid drift, source/generation drum doubling, lyrics/language, key clashes, transitions, loudness/true peak, saved stems and project reopen. Screening targets must be labelled proposals until a listening panel and actual metrics substantiate them.
- [ ] Verify final source rights/provenance at export, not just model license. App AGPL/MIT terms do not grant rights to a user's uploaded recording; preserve user declarations/unknown evidence rather than making an automatic legal guarantee.
- [ ] Review final diff and list actual checks, unverified native modes/platforms and any migration compatibility limits. No commit/push/deployment/release is authorized by this planning task; obtain the maintainer's requested branch before any later commit.

## Exact future checks, not results from planning

From repository root, activate the existing test virtualenv (`source backend/.venv/bin/activate` on the inspected macOS checkout) before invoking `python`. The host shell currently has no bare `python`; the test virtualenv provides it.

```bash
SONG_TEST_ROOT="$(mktemp -d -t openfabric-song-tests)"
export OPENFABRIC_CONFIG="$SONG_TEST_ROOT/config.json"
export OPENFABRIC_DATA_DIR="$SONG_TEST_ROOT/library"
export SEED_VC_DIR="$SONG_TEST_ROOT/seed-vc"
export OPENFABRIC_MODULE_ROOT="$SONG_TEST_ROOT/modules"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
python -m pip install -r backend/requirements-test.txt
python backend/scripts/generate_contracts.py
python backend/scripts/generate_contracts.py --check
```

Targeted commands, from `backend/` after the corresponding files exist:

```bash
python -m unittest app.song_capabilities_test app.song_sources_test app.song_from_beat_contracts_test app.song_from_beat_test app.beat_song_mix_test app.api.routes_song_from_beat_test -v
python -m unittest app.music_queue_plan_test app.music_queue_store_test app.music_queue_engines_test app.music_queue_ownership_test app.music_queue_test app.ace_jobs_test app.local_music_test app.local_music_runtime_test app.module_music_models_test app.module_contracts_test -v
for target in linux darwin win32; do
  python -m mypy --strict --follow-imports=silent app/song_from_beat_contracts.py app/song_sources.py app/song_capabilities.py app/song_from_beat.py app/beat_song_mix.py app/api/routes_song_from_beat.py --platform "$target"
done
python -m unittest discover -s app -p '*_test.py' -v
```

Run the complete existing strict CI loop from repository root (same extraction as plan 03):

```bash
python - <<'PY'
from pathlib import Path
import subprocess
text = Path('.github/workflows/ci.yml').read_text()
start = text.index('          for target in linux darwin win32; do\n')
end = text.index('          done\n', start) + len('          done\n')
script = '\n'.join(line[10:] for line in text[start:end].splitlines())
subprocess.run(['bash', '-eu', '-c', script], cwd='backend', check=True)
PY
```

From `frontend/`: `npm test` and `npm run build`. From `desktop/`: `npm test` because model selection/startup and packaged source/artifact layout are affected. Expected: exit 0 after implementation; initial new tests fail at the missing module/behavior. These checks do not download weights or establish GPU support. Full inference proof is the explicit separate Task 13 procedure.

## Done means

- Text-only queue history and generation remain compatible; immutable conditioned items contain exact retained source/model/task/seed evidence and cannot be mutated by a later edit.
- The selected base model is installed through a reviewed manifest, survives restart, is loaded under owned admission and demonstrably receives source audio for the task. Complete/Lego output labels match proven native semantics.
- Cover rewrites are explicit; Stable extensions are disabled until supported by actual pinned code/runtime evidence. No unsupported engine is advertised to fill a feature matrix.
- Beat/song layer and replacement modes use valid sources/stems, reviewed beat anchors, coherent piecewise time mapping, explicit bass/pitch decisions and a retained, re-editable mix plan.
- Browser closure, cancellation, source changes, app restart and save failure preserve finished work and ownership; retry-save does not rerun expensive inference.
- A reopened project plays the stored result without model services, retains dry/source references and avoids duplicate processing; preview/export, meters, tuning, lyrics and provenance are honest about their limits.
