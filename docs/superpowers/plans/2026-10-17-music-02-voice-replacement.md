# Replace Voice v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish uploaded-song voice replacement with durable convert, prepared-vocal and hybrid workflows, reviewed alignment, reusable stems, measured mixing, and registered RVC inference while preserving originals and existing artist-production restrictions.

**Architecture:** Keep the existing `/api/voices/replace` upload-and-Seed-VC workflow compatible. Add a typed, revisioned replacement owner that consumes immutable library identities and the shared analysis/alignment/DSP services from [plan 01](2026-10-17-music-01-analysis-dsp.md), stores completed stages independently of the browser, and publishes each reviewed mix as a new library track. Reuse existing process supervision, GPU leases, export provenance, controls and audio measurements; do not make a second engine server or put new workflow business logic in Vue.

**Tech Stack:** Existing FastAPI, strict Pydantic contracts, SQLite additive migrations, NumPy/SciPy/librosa in the approved analysis environment, FFmpeg measurements/rendering, optional independently installed Seed-VC/RVC engines, Vue 3/Pinia/Vitest and generated TypeScript contracts. The shared foundation selects and verifies any stretch dependency; this plan does not independently add one.

---

## Status, evidence and scope

This is an implementation plan, not an implementation or performance report. The repository was inspected on 17 October 2026. **No future implementation tests, model inference, downloads or listening evaluations described here have been run.** Targets below are proposed release gates, not measured capabilities. The research report's two-week Phase 1 estimate combines several independent, risky subsystems; it is not evidence that alignment, RVC integration and release-quality mixing fit that schedule.

Coverage: report §§3.1–3.7, relevant separation/conversion/DSP candidates in §7, evidence and licensing obligations in §9, R2/R3/R4/R7/R9/R13 in §10, and the replacement work in roadmap Phase 1 (§11). Housekeeping and shared license evidence live in plan 00/01; this plan consumes that policy and never creates a second license registry.

Verified repository anchors:

| Existing code | Verified behavior and consequence |
|---|---|
| `backend/app/voice_replacement.py:31,65,75,101,115,142` | `ReplacementRoute` bounds multipart streams; upload validation accepts six extensions, 256 MiB and at most two hours; the accepted request stores independent original/result rows and owns the apply task before responding. Preserve these properties. |
| `backend/app/api/routes_voices.py:126,142` and `backend/app/client_contracts.py:162,175` | Existing apply/replace response requires `voice_id` and `ApplyStatusResponse`; it cannot honestly model a prepared vocal with no converter. |
| `backend/app/voice_build.py:178,1425,1488,1558,1795,1827,2015,2080` | `ApplyJob` assumes a voice; start/cancel/removal/shutdown/status persistence, artifact pinning, consent rechecks and cleanup ownership already exist. Its conversion path separates with default Demucs and eventually deletes its work directory. |
| `backend/app/voice_build.py:522,1941` | Conversion uses mean-level gain bounded to roughly ±12 dB, duration checks, limiter, and complete-file replacement. This is not LUFS matching. |
| `backend/app/stems.py:116,131` | `quality='high'` already selects `htdemucs_ft`; `separate_file` holds the GPU lease and permits a caller-owned spawn callback. It returns drums/bass/other/vocals. |
| `backend/app/voice_separation.py:35,55` | Fast/high/RoFormer capability options and owned spawn already exist, but `separate_vocal` only returns a vocal. RoFormer code currently finds one vocal output; it does not establish a matching accompaniment. |
| `backend/app/audio_quality.py:77,117` and `audio_quality_contracts.py` | Integrated LUFS, loudness range, oversampled true peak and measured two-pass FFmpeg normalization already exist. Reuse them; adding pyloudnorm just because the report names it would duplicate measurement policy. |
| `backend/app/optional_engines.py:231,294,356,562` | RVC has local-file inference, independent worker receipts, recovery and shutdown. Its public request uses filesystem paths and is awaited through the standalone optional-engine admission owner. It is not a registered voice replacement engine. Calling `_run` inside an admitted replacement can reject its own owner as busy. |
| `backend/app/voice_artifacts.py:17,35,42,104` | Existing immutable voice registry is Seed-VC-shaped: checkpoint, config and reference are required for trained models. Do not stuff an RVC `.pth` into it with fake YAML/reference entries. |
| `backend/app/audio_version_contracts.py:18` and `audio_version_store.py:26` | Audio versions support only `original` and `voice`, with database constraints. Prepared/hybrid revision results cannot be silently introduced as a new kind or disguised with a dummy voice. |
| `backend/app/production_render.py:64,119,154` and `artist_production_contracts.py:130` | Strict clean rendering requires an isolated authorized human guide, independent reviewed accompaniment, pinned Seed-VC identity, current consent and singer-range approval. It rejects incompatible source durations. General replacement is a separate workflow and must never weaken those checks. |
| `frontend/src/api/voices.ts:203`, `stores/aceStep.ts:351`, `views/ace-step/GenerateForm.vue:337` | Existing form/store require a ready voice and tie cancel/retry to apply-by-track. Keep old saved cards working while routing new plans through a separate typed store. |
| `frontend/src/components/shared/BatchABPlayer.vue` | Existing A/B player has playback arbitration, generation guards and teardown. Extend it narrowly if phrase loops need support; do not create an independent player with unowned audio. |

### Decisions that constrain the implementation

1. **No new product-wide audio-version kind in this slice.** Replacement plans own revisions and artifact identities. A final render creates a new `model='upload'` track with provenance and `params.source='voice_replacement_v2'`; its own first audio version may be `original`, meaning the immutable initial audio of that new track. This does not assert that the audio is human/original content. Its derivative history belongs in the replacement/provenance records. Existing `voice` versions and source tracks stay intact. Never call `prepare_application` to fabricate a prepared-vocal voice version.
2. **One replacement workflow owner, shared conversion implementation.** New prepared mode has no `voice_id`; therefore a new typed replacement owner is preferable to making `ApplyJob.voice_id` optional across ACE, YuE, audio versions, artist production and every caller. Extract only the converter boundary actually shared with `_apply_inner`; keep legacy ApplyJob lifecycle and strict-production orchestration intact.
3. **RVC first; Applio is a separate compatibility spike.** The installed RVC adapter is evidence of an integration starting point, not of singing quality or Apple Silicon performance. Applio must prove its own command contract, model compatibility, cancellation and license inventory before being offered; no “RVC/Applio” label over a single untested adapter.
4. **High separation is initially recommended, not automatically downloaded.** Expose fast/high/RoFormer with actual readiness and evidence. Do not make RoFormer the default because a report predicts better quality. Recommend it only after the same held-out songs establish a benefit on the target machine and its exact checkpoint rights are recorded.
5. **An alignment warning is a result.** A different lyric, missing chorus, overlapping harmonies or incompatible take can require manual anchors or a new performance. Do not force every input through a mathematically valid but musically absurd warp.
6. **Generated/prepared is not clearance.** New replacement renders remain general derivative outputs with inherited restrictions and unknowns. The existing strict artist path continues to require a human guide and its release review; selecting “prepared” or “hybrid” does not produce its eligibility badge.
7. **Hybrid means align prepared take, then convert, then validate timing.** Record both intermediate artifacts. Never repeatedly stretch a converted waveform while editing anchors; regenerate from the retained dry prepared source. Convert mode preserves the original performance timing and uses identity alignment unless measured output drift fails validation.

## Cross-plan boundary

Plan 01 owns proposed `music_analysis_contracts.py`, `music_analysis.py`, `music_analysis_store.py`, `music_analysis_worker.py`, `music_dsp_contracts.py`, `music_alignment.py`, `music_dsp.py`, `music_dsp_worker.py`, and analysis routes. It defines:

- `MusicSourceRef`: discriminated `kind='version'` with `track_id/version_id`; `kind='stem'` with `track_id/stem/sha256`; `kind='production_asset'` with `asset_id/sha256`. Public requests never accept filesystem paths.
- `AnalysisResult`: immutable source identity and digest, channel/rate/duration evidence, known/unknown rhythm and key, monotone `beats_sec`/`downbeats_sec`, `AudioMetrics`, and vocal activity only when separated-vocal evidence supports it.
- `WarpAnchor(source_sec, target_sec)`: **prepared/source time → original/reference time**, both in seconds.
- `AlignmentResult`: both input digests, revision, ordered anchors, phrase diagnostics, `accepted|needs_review|unavailable`.
- Proposed internal `align_prepared_vocal(...)` and `render_transform(request, staging_root, run)`. These names are proposed interfaces from plan 01, not existing functions. DSP has no mandatory independent queue or store; replacement owns its use.

Replacement persists the exact analysis IDs, input hashes, engine fingerprints and settings used. It must not look up “current track audio” again halfway through a render. Coordinate the shared schema before implementation; keep the seconds convention throughout. Resolve intermediate identities through existing evidence assets: `artist_production.register_source` (`artist_production.py:175`) accepts a contained immutable source copy with `SourceDeclaration.role=reference`, and `create_transform` (`:239`) accepts contained retained vocals/backing/aligned files with `role=stem`, explicit parent IDs and transformation description. Use the existing shared `production_asset(asset_id,sha256)` reference for their analysis/DSP inputs. These helpers accept files anywhere under `FILES_DIR`, so no hidden library tracks, new source-reference discriminator or fake audio versions are needed. Keep generated/unknown sources classified and reference-only; analysis permission does not require or confer strict-production eligibility. Registering a stem never turns its full-mix/reference ancestors into a valid human guide or independent backing.

## File map and responsibility

Create these focused files when their tasks are reached:

| File | Responsibility |
|---|---|
| `backend/app/replacement_contracts.py` | Discriminated requests, plan/revision/public artifact/status/error contracts. |
| `backend/app/replacement_store.py` | Additive constrained SQLite migration, CAS updates, attempts/artifacts/publication attachment. |
| `backend/app/replacement_jobs.py` | Workflow admission, owned stages, cancellation, recovery, shutdown, publication and invalidation decisions. |
| `backend/app/replacement_mix.py` | Replacement-specific analysis selection, bleed/pitch diagnostics and mix configuration using shared DSP. |
| `backend/app/voice_conversion.py` | Small internal converter boundary shared by legacy Seed-VC apply and new jobs; RVC dispatch uses existing engine facilities. |
| `backend/app/rvc_models.py` | Contained immutable RVC registry/import inspection, model identity/evidence and voice association; no training. |
| `backend/app/api/routes_replacement.py` | Bounded v2 assets/plans/edits/previews/remix and RVC registry endpoints. |
| `backend/app/replacement_contracts_test.py` | Boundary/model rejection and no-voice prepared requests. |
| `backend/app/replacement_store_test.py` | Migration, transaction races, publication and recovery metadata. |
| `backend/app/replacement_jobs_test.py` | Mode routing, invalidation, stage reuse, cancel/retry/shutdown/crash/low disk. |
| `backend/app/replacement_mix_test.py` | Numeric loudness/bleed/pitch and render evidence using CPU fixtures. |
| `backend/app/rvc_models_test.py` | Import/path/trust/evidence and mutation rejection. |
| `backend/app/replacement_routes_test.py` | Multipart/JSON limits, stable errors and source containment. |
| `backend/app/replacement_listening_test.py` | Manifest validation and metrics aggregation only; no automatic GPU jobs in unit discovery. |
| `backend/testdata/music/replacement_cases.json` | Tiny licensed/synthetic case declarations and expected tolerances, no downloaded songs. |
| `backend/scripts/evaluate_voice_replacement.py` | Explicit opt-in held-out evaluation using supplied local cleared fixtures and selected engine, never auto-download. |
| `frontend/src/api/replacement.ts` and `.test.ts` | Generated schema parsers, typed actions, abort and error translation. |
| `frontend/src/stores/replacement.ts` and `.test.ts` | Reloadable plan state, mutation sequencing, observation generation and stale revision handling. |
| `frontend/src/views/ace-step/VoiceReplacementWizard.vue` and `.test.ts` | Four-step workflow using existing design controls. |
| `frontend/src/views/ace-step/ReplacementAlignmentEditor.vue` and `.test.ts` | Phrase/anchor editor with equivalent keyboard and numeric controls. |
| `frontend/src/views/ace-step/ReplacementMixPanel.vue` and `.test.ts` | Target selection, bounded processing controls, server preview, warnings. |
| `frontend/src/locales/voiceReplacement.ts` | Existing English locale module pattern; all new text lives here. |
| `docs/voice-replacement-quality.md` | Reproducible fixture/evaluation protocol, measurements and honest limitations. |
| `docs/evaluations/voice-replacement/README.md` | Held-out selection and listener record format; results added only after actual evaluation. |

Modify the existing integration files explicitly:

- `backend/app/voice_replacement.py` only for reusable bounded upload acceptance/legacy compatibility; do not alter its old response shape.
- `backend/app/voice_separation.py`, `voice_separation_test.py`, `reference_separation_test.py`, `stems.py`, `stems_test.py`: typed vocal+backing result and safe fresh staging, preserving all `separate_vocal` callers.
- `backend/app/voice_build.py`, `voice_build_test.py`, `voice_replacement_test.py`, `job_lifecycle_test.py`: shared converter extraction and legacy behavioral invariants.
- `backend/app/production_render.py`, `artist_production_test.py`: only necessary shared-converter call adaptations and regression coverage; strict request/gates unchanged.
- `backend/app/optional_engines.py`, `optional_engines_test.py`: reuse inspected RVC command preparation and supervised launch without invoking standalone admission recursively.
- `backend/app/main.py`, `work_busy.py`, `resource_admission.py`, `setup_admission_test.py`: new owner start/recovery/shutdown and resource accounting.
- `backend/app/db.py`, `api/routes_tracks.py`, `audio_versions.py`, `audio_versions_test.py`: migration registration, transactional final-track attachment, reference-aware deletion/retention. `audio_version_contracts.py` and existing kind constraints are deliberately unchanged.
- `backend/app/artist_production.py`, `artist_production_test.py`, `export_provenance.py`, `export_provenance_contracts.py`, `audio_exports.py`, `audio_export_quality_test.py`, `audio_exports_test.py`: replacement lineage/evidence through ordinary exports, no false clean-production provenance.
- `backend/app/module_licenses.py`, `module_evidence.py` and their tests only for exact separator/RVC dependency evidence required by plan 01, not for guessed license claims.
- `backend/scripts/generate_contracts.py`, `frontend/src/api/generated.ts` by generation, `.github/workflows/ci.yml`: contracts and strict-type scope.
- `frontend/src/views/ace-step/GenerateForm.vue`, `GenerateFormVoiceReplacement.test.ts`, `JobCard.vue`, `VoiceReplacementCard.test.ts`, `frontend/src/stores/aceStep.ts`, `stores/voiceReplacement.test.ts`: entry point and mixed legacy/v2 saved cards.
- `frontend/src/components/shared/BatchABPlayer.vue`, `BatchABPlayer.test.ts` only for the concrete optional phrase-loop/controlled selection API needed by this wizard; preserve other callers.
- `frontend/src/locales/en.ts`, `docs/voice-replacement.md`, `ROADMAP.md`: locale registration, user workflow and measured status.
- `desktop/` source changes only if future inspection finds a genuine packaging path change; run desktop tests because startup/file layout ownership changes are in this slice.

## Proposed contract and storage rules

These are proposed new contracts, not descriptions of APIs that already exist. Keep responses separately typed from persisted records; paths and worker receipts are private. Reuse shared types instead of copying `AnalysisResult`, `AudioMetrics` or `MusicSourceRef`.

The following core schema is a concrete starting implementation for `replacement_contracts.py`; mode-specific required values cannot accidentally depend on an irrelevant ready voice:

```python
from __future__ import annotations
from typing import Annotated, Literal
from pydantic import ConfigDict, Field, TypeAdapter
from .contracts import Contract
from .music_analysis_contracts import MusicSourceRef

Identifier = Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]
Sha256 = Annotated[str, Field(pattern=r'^[0-9a-f]{64}$')]

class ReplacementBoundary(Contract):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, strict=True)

class SelectedSource(ReplacementBoundary):
    source: MusicSourceRef
    audio_sha256: Sha256

class SeedTarget(ReplacementBoundary):
    engine: Literal['seed-vc']
    voice_id: Identifier
    model_revision_id: str = Field(min_length=1, max_length=128)

class RvcTarget(ReplacementBoundary):
    engine: Literal['rvc']
    registered_model_id: Identifier

VoiceTarget = Annotated[SeedTarget | RvcTarget, Field(discriminator='engine')]

class ReplacementFitSettings(ReplacementBoundary):
    max_stretch: float = Field(default=1.25, ge=1.0, le=1.5)
    pitch_shift_semitones: int = Field(default=0, ge=-12, le=12)
    pitch_nudge_semitones: float = Field(default=0, ge=-1, le=1)
    cleanup: Literal['off', 'light'] = 'off'
    keep_breaths: bool = True

class ReplacementBase(ReplacementBoundary):
    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=300)
    source: SelectedSource
    separator: Literal['fast', 'high', 'roformer'] = 'high'
    fit: ReplacementFitSettings = Field(default_factory=ReplacementFitSettings)

class ConvertReplacementRequest(ReplacementBase):
    mode: Literal['convert']
    target: VoiceTarget

class PreparedReplacementRequest(ReplacementBase):
    mode: Literal['prepared']
    prepared: SelectedSource

class HybridReplacementRequest(ReplacementBase):
    mode: Literal['hybrid']
    prepared: SelectedSource
    target: VoiceTarget

ReplacementRequest = Annotated[
    ConvertReplacementRequest | PreparedReplacementRequest | HybridReplacementRequest,
    Field(discriminator='mode'),
]
replacement_request_adapter = TypeAdapter(ReplacementRequest)
```

`strict=True` is intentional for new JSON boundaries; numeric UI values must be JSON numbers, not guessed/coerced strings. Actual JSON validation uses `TypeAdapter.validate_json`; tests should distinguish JSON representation from Python construction. The shared generator must emit and validate the discriminator; export concrete variants plus wrapper response models if its union handling requires roots. Do not relax runtime validation to accommodate the generator. Add the combined coarse-plus-nudge bound validator while implementing this core skeleton, with both ±12/±1 endpoint cases and sum-overflow rejection tests.

Finish the schema with the following exact domain fields and constraints in Task 2:

- `ReplacementPlanResponse`: `id`, `revision >= 1`, `request: ReplacementRequest`, `status: draft|queued|running|needs_review|done|failed|cancelled|interrupted`, `phase: inspecting|separating|analyzing|cleaning|aligning|converting|mixing|measuring|publishing|complete`, `active_attempt_id | None`, `alignment: AlignmentResult | None`, `artifacts: list[ReplacementArtifact] <= 64`, `renders: list[ReplacementRender] <= 100`, `error_code | None`, `warnings <= 64`, `updated_at`. Do not reuse `ApplyStatusResponse` for these states.
- `ReplacementArtifact`: `id`, `evidence_asset_id: Identifier`, `role: source_vocal|backing|prepared_dry|prepared_clean|aligned_dry|converted_dry|matched_vocal|preview|master|impulse_response`, `sha256`, `source_sha256s <= 8`, `settings_sha256`, `duration_sec`, `sample_rate_hz`, `channels`, `alignment_revision | None`, `audio_url | None`, `measurement: AudioMetrics | None`. No public path. Artifacts are immutable; a request can reference only artifacts belonging to its plan or a resolved library source.
- `ReplacementMixSettings`: `vocal_level: match_original|manual`, `manual_gain_db [-12,12]`, `master_level: match_original|custom|off`, `loudness: LoudnessSettings`, `eq_enabled=false`, `compression_enabled=false`, `space: off|owned_ir`, `ir_asset_id | None`, `wet [0,.25]`. Use an after-validator so owned IR requires an actual imported IR identity; `off` forbids an IR and nonzero wet. A mix request does not accept FFmpeg filter text. Reuse bounded shared EQ settings; cap correction at ±6 dB, compressor makeup at +3 dB. Define finite attack/release and threshold/ratio limits in the shared DSP contract, not arbitrary dictionaries.
- `AlignmentEditRequest`: `expected_revision >= 1`, `alignment: AlignmentResult`, `reviewed_phrase_ids <= 1024`. Require unchanged source digests and stable phrase IDs; the server revalidates bounds and calculates revision. Never trust a submitted confidence/status as a measurement.
- `ReplacementRenderRequest`: `expected_revision`, `mix: ReplacementMixSettings`, `request_id: Identifier`. A unique request ID makes a retried HTTP operation idempotent; reusing one with a different body fails with `replacement_request_conflict`.
- `ReplacementRender`: `id`, `plan_id`, `revision >= 1`, `request_id`, `status: queued|running|done|failed|cancelled|interrupted`, `master_artifact_id | None`, `track_id: strict int >0 | None`, `settings_sha256`, `created_at`, `error_code | None`. A done final render requires both artifact and track; a preview is an artifact, not an empty final render.
- `ReplacementErrorCode`: finite literals in the errors table below; user text derives from translations, backend technical details stay in logs.

The selected pitch shift is a user-approved whole-take transposition. “Auto” in the UI means a displayed recommendation of at most ±2 semitones requiring acceptance, never a hidden correction based on an uncertain key. Manual coarse shifts are bounded to ±12, fine prepared nudge to ±1, and an after-validator requires their combined DSP shift to remain within ±12; shifts above ±2 show an artifact/formant warning and require measured native-adapter support. The fine nudge defaults to zero. Neither promises note-by-note tuning. Keep an engine's F0-transpose option separate from postprocessing pitch so it is never applied twice.

Persist in additive tables registered from `db.get_db()` alongside current migrations:

1. `replacement_plans`: ID, current revision, status/phase, typed request JSON, current attempt, timestamps and bounded stable error; CHECK valid IDs/states/revision.
2. `replacement_revisions`: `(plan_id, revision)` primary key, immutable typed request/settings/alignment snapshot and digest, created time. Never replace earlier revisions.
3. `replacement_attempts`: ID, plan/revision/stage/settings digest, status, private worker receipt/identity, started/finished timestamps and cleanup state. Record evidence asset IDs for accepted sources and retained transform artifacts so existing `production_asset` source resolution works without public paths. Keep interrupted attempts so retry never claims continuation of a completed computation.
4. `replacement_artifacts`: ID, plan ID, content hash, role, private relative path, settings digest, source identities and validated metadata; unique `(plan_id, role, settings_sha256, sha256)`.
5. `replacement_renders`: ID, plan/revision/request ID, render/settings digest, status, `track_id REFERENCES tracks ON DELETE SET NULL`, master artifact ID, provenance digest. UNIQUE request ID; CHECK metadata for pending/done publication. Preserve a tombstone if a final result track is explicitly deleted.

Use `BEGIN IMMEDIATE` and `UPDATE ... WHERE revision=?` for read/modify/write. Revision contention returns 409 with the current revision; no last-write-wins. Extend `db.insert_track` with a concrete optional `replacement_render_id` attachment, following its existing local-music job attachment pattern, so the track row and render mapping commit together. A store helper that commits before the track insert would defeat this guarantee.

Create private paths under `FILES_DIR/replacements/<plan-id>/`: immutable `artifacts/<artifact-id>.wav`, fresh `attempts/<attempt-id>/`, and private receipt files. Resolve the root and each path; reject traversal, absolute public inputs, symlink roots/escapes and ambiguous files. Check decoded duration/rate/channels/frame counts and actual file sizes. Stage files with exclusive creation; fsync/replace complete files, then commit catalog references. Recovery reconciles files promoted before the transaction. Keep verified completed artifacts when later stages fail.

Do not recursively remove a plan or library directory to retry. Clean only the failed attempt's explicitly recorded partial files after its writer is proven drained. Complete artifacts and source snapshots remain referenced. A dedicated deletion action may remove only unreferenced contained files after checking active jobs and manifests. Unknown files are retained and surfaced for review.

## Proposed endpoint surface

All endpoints below are new except the legacy multipart `POST /api/voices/replace`, which remains compatible. New route prefix is `/api/voices/replace`; declare static `/assets`, `/plans` and `/rvc-models` paths before any broad dynamic route. JSON actions have a 64 KiB pre-parser body limit except alignment edits, whose separate cap is 256 KiB with at most 1000 anchors/phrase decisions. The public v2 alignment cap is 1000 even if the shared foundation allows up to 1024 diagnostic phrases; larger results require bounded pagination/review, never truncation of saved evidence. Large audio/binary imports have explicit streaming caps. The app remains loopback-bound; apply the existing local-origin write guard and do not add remote-network exposure.

| Method/path | Typed input/output and action |
|---|---|
| `POST /assets` | Multipart one audio, role and bounded declaration; returns immutable saved version/source/hash. Source/prepared 256 MiB/2 h; IR 10 sec. |
| `POST /plans` | `ReplacementRequest`; creates draft and returns `ReplacementPlanResponse`. No model starts. |
| `GET /plans` | Bounded cursor/limit (1–100); returns plan summaries, not all artifact/anchor bodies. |
| `GET /plans/{id}` | Returns current plan/revision/artifact/render summaries. |
| `PATCH /plans/{id}` | `ReplacementUpdateRequest(expected_revision, request: ReplacementRequest)`; validates identities, appends revision and applies invalidation table. |
| `POST /plans/{id}/prepare` | `ReplacementActionRequest(expected_revision, request_id)`; runs separation/analysis and fit proposal to a review checkpoint. |
| `GET /plans/{id}/alignment` | Current or explicit saved revision, returns `AlignmentResult`. |
| `PATCH /plans/{id}/alignment` | `AlignmentEditRequest`; validated manual revision, never trusts client confidence. |
| `POST /plans/{id}/preview` | `ReplacementRenderRequest`; renders bounded selected phrase/window, at most 30 sec, under the same settings as final mix. |
| `POST /plans/{id}/render` | `ReplacementRenderRequest`; runs only missing dependent stages and publishes a new track after verification. |
| `POST /plans/{id}/remix` | `ReplacementRenderRequest`; requires completed retained dry inputs, never executes separator/converter. |
| `POST /plans/{id}/cancel` | Idempotent owner drain; returns durable state including cleanup blocked if unresolved. |
| `POST /plans/{id}/retry` | `ReplacementActionRequest`; retries explicit failed/interrupted stage, reuses verified completed predecessors. |
| `DELETE /plans/{id}` | Reject active owner unless drained; remove plan records/unreferenced private files, retain independently published source/result tracks and provenance. |
| `GET /plans/{id}/artifacts/{artifact_id}/audio` | Validates plan membership, retained status and containment; serves completed audio only, supports ordinary player ranges. |
| `GET /rvc-models` | Registry summaries/readiness/evidence, no paths or checkpoint bytes. |
| `POST /rvc-models` | Bounded trusted-local checkpoint and optional index import plus typed evidence/voice association/trust acknowledgment. Caps: combined 256 MiB +64 KiB metadata; no archive extraction. |
| `DELETE /rvc-models/{id}` | Reject referenced active jobs; retain pinned files/evidence used by immutable render manifests until their retention rules permit removal. |

`ReplacementActionRequest` requires finite positive `expected_revision` and 32-hex `request_id`. `ReplacementUpdateRequest` carries exactly `expected_revision` and the complete validated `ReplacementRequest`, avoiding an untyped partial-update dictionary. Extend `ReplacementRenderRequest` with optional `preview_window` containing finite `start_sec >= 0`, `duration_sec in (0,30]`, validated inside the selected source duration; final render/remix forbids that field. Requests return 202 with the saved plan when work is accepted, 200 for completed idempotent action replay, or the stable typed errors below. Cancellation does not require a matching stale editor revision because draining an owner must remain possible.

## Ordered implementation tasks

### Task 1: Freeze current behavior and prepare isolated verification

**Files:** Modify `backend/app/voice_replacement_test.py`, `voice_build_test.py`, `artist_production_test.py`, `frontend/src/stores/voiceReplacement.test.ts`; create the case manifest and quality protocol files from the file map.

- [ ] Read `AGENTS.md`, current diff and every caller of `start_apply`, `separate_vocal`, `convert_rvc`, `retain_original`, and `db.insert_track`. Preserve unrelated work, including the supplied research PDF.
- [ ] Add a regression asserting a replacement retry reads the independently retained original even after the result exists. Capture source bytes/hash before cancellation and after retry; assert identical and different from the result path.
- [ ] Add a regression asserting revoked consent fails before conversion and again before final publication, without deleting original/prepared audio. Keep `HumanGuidePipelineTests` rejection of generated guides and mixed-master-derived accompaniment.
- [ ] Add frontend deferred-response tests: upload finishing after removal/unmount does not recreate a deleted card; an old polling response after cancel cannot restore running status; a retry uses the saved source rather than uploading the rendered result.
- [ ] Run the isolated targeted commands in the verification section. Expected: existing invariants pass; if a new invariant exposes a current defect, record its failing output before a focused fix. Do not rewrite expected results to bless the defect.
- [ ] Add CPU fixture declarations: identity alignment, 240 ms delay, 1.10 duration expansion, two unequal phrase shifts, long silence, missing/repeated phrase, unrelated melody/lyrics, antiphase stereo, silence and near-full-scale peaks. Keep generated fixture PCM temporary; describe exact construction in Task 8.

**Checkpoint:** current behavior documented with source preservation, existing strict gate tests and baseline timings. No inference engines need to be installed for this checkpoint.

### Task 2: Introduce discriminated contracts and negative boundary tests

**Files:** Create `replacement_contracts.py`, `replacement_contracts_test.py`; modify `generate_contracts.py`; generate `frontend/src/api/generated.ts`.

- [ ] Add failing contract tests for all three valid modes; prepared has neither a voice nor an engine and succeeds. Convert requires a target; hybrid requires target and prepared source; unexpected `voice_id`/target in prepared fails rather than being ignored.
- [ ] Add failures for bool-as-int track IDs, empty/overlong title, unknown mode/engine, malformed IDs/hashes, NaN/infinity, max stretch outside [1,1.5], pitch outside bounds, over-capacity anchors/artifacts, reversed or duplicate anchor times and unknown fields.
- [ ] Implement the schema above and the response/edit/mix/render contracts. Validate strictly at request, worker-output and stored-record boundaries; return stable errors, not raw Pydantic exception content containing paths or input bodies.
- [ ] Add after-validation for IR combinations, matching input hashes, source limits and valid state transitions. Server recomputes measured diagnostics and decisions after edits.
- [ ] Register all public concrete request/response models in contract generation. Generate and inspect the TypeScript discriminated unions and parsers; never edit generated code by hand.
- [ ] Run contract tests and `python backend/scripts/generate_contracts.py --check`; expected future result: every invalid body rejected and no contract drift.

Concrete proposed regression test, self-contained once Task 2 introduces the defined schema:

```python
import json
import unittest
from pydantic import ValidationError
from app.replacement_contracts import PreparedReplacementRequest, replacement_request_adapter

class ReplacementModeTests(unittest.TestCase):
    def test_prepared_mode_needs_no_unused_voice(self) -> None:
        body = {
            'mode': 'prepared', 'title': 'Prepared take',
            'source': {'source': {'kind': 'version', 'track_id': 1,
                                  'version_id': 'a' * 32}, 'audio_sha256': 'b' * 64},
            'prepared': {'source': {'kind': 'version', 'track_id': 2,
                                    'version_id': 'c' * 32}, 'audio_sha256': 'd' * 64},
        }
        result = replacement_request_adapter.validate_json(json.dumps(body))
        self.assertIsInstance(result, PreparedReplacementRequest)
        self.assertNotIn('target', result.model_dump())
        body['target'] = {'engine': 'seed-vc', 'voice_id': 'e' * 32,
                          'model_revision_id': 'trained'}
        with self.assertRaises(ValidationError):
            replacement_request_adapter.validate_json(json.dumps(body))
```

### Task 3: Add plan persistence, compare-and-swap revisions and publication attachment

**Files:** Create `replacement_store.py`, `replacement_store_test.py`; modify `db.py` and its affected tests.

- [ ] Start with a temporary pre-feature catalog containing original and voice versions. Add a migration test checking old rows, version IDs, source references and files are unchanged; run the migration twice.
- [ ] Add concurrent-edit tests using two independent SQLite connections: both read revision 1; one advances to 2; the other receives `replacement_revision_conflict` and does not alter revision 2 or append an orphan edit.
- [ ] Add constraint tests for invalid IDs/statuses, duplicate render request ID, duplicate revision, missing artifact parent and publication of an incomplete artifact.
- [ ] Implement the five additive tables and the exact CAS/transaction behavior above. Store typed JSON only after validation. Put all update reads and writes inside one transaction.
- [ ] Add `replacement_render_id` to `db.insert_track`; attach only the expected pending render row and reject a second attachment in the same transaction. Keep existing callers and commits unchanged.
- [ ] Inject failure after file promotion, before track insertion, and after commit before response. Recovery must yield one reusable artifact and at most one result track; a retry returns that track instead of duplicating it.
- [ ] Run store/publication tests. Expected: old catalogs migrate without audio-version kind changes, stale writers fail, duplicate requests are idempotent.

### Task 4: Add bounded source/prepared/IR intake without requiring a voice

**Files:** Create `api/routes_replacement.py`, `replacement_routes_test.py`; modify `voice_replacement.py` only where bounded intake is reused; integrate `audio_versions.py` source pinning.

- [ ] Test `POST /api/voices/replace/assets` with one `audio` plus a typed `role=source|prepared|impulse_response` and declaration metadata; successful intake returns a saved source track, retained version identity and hash. Role is workflow metadata, not a rights review. Source/prepared/IR intake registers its immutable bytes with existing evidence `SourceDeclaration.role=reference` and the actual origin/provider; this deliberately cannot become a strict human guide. For a prepared take selected from already registered human-guide evidence, preserve that original source role/graph rather than rewriting it. A derived replacement stem remains tied to all its parent evidence.
- [ ] Test library selection from an exact existing version; mutation between selection and acceptance returns `replacement_source_changed`. Source and prepared may not resolve to the same bytes in prepared/hybrid mode.
- [ ] Test unknown source versions, foreign plan artifact IDs, absolute/traversal paths embedded in malformed JSON, symlink escapes, local-file playlists, remote URLs, malformed container headers, encoded duration above two hours, decoded byte cap and simultaneous identical names.
- [ ] Extend actual multipart pre-parser bounds to the new route; the existing `ReplacementRoute` checks an exact path set, so merely importing it does not protect `/assets`. Limit one upload to 256 MiB plus 64 KiB metadata/overhead; reject count/type before reading entire bodies.
- [ ] Reuse `_filename`, `_copy_upload`, `_duration`, `_decode` and `retain_original` after checking their actual signatures. Close uploads after drain. On failure roll back only rows/files created by that unaccepted intake. Preserve a previously accepted source if a later plan creation fails.
- [ ] Enforce an IR-specific decoded maximum of 10 seconds, one/two channels and bounded wet level. Keep its raw bytes/declaration for provenance. IR source rights inherit restrictions; no sample downloads.
- [ ] Measure estimated working space before decode/separation/render; reserve worst-case staged float PCM and artifacts plus a proposed 1 GiB free-space margin. Catch ENOSPC during every write too; estimates do not make disks cooperative.
- [ ] Run route and legacy upload tests. Expected: source/prepared acceptance succeeds without any ready voice; legacy endpoint still rejects unknown voices before library writes.

### Task 5: Return paired vocals and backing from existing separators

**Files:** Modify `voice_separation.py`, `stems.py`, `voice_separation_test.py`, `reference_separation_test.py`, `stems_test.py`; use shared DSP for sample-accurate residual/rendering.

- [ ] Add tests for new proposed `SeparatedVocalBacking` immutable dataclass: vocal path, backing path, actual separator/checkpoint fingerprints and whether backing was model-produced, summed stems or residual. Keep `separate_vocal` return type unchanged.
- [ ] Add a high-quality Demucs fixture test asserting `htdemucs_ft` and owned spawn; backing is drums+bass+other rendered with normalization off, original frame count/rate, and no limiter/gain alteration at this stage.
- [ ] Inspect the installed/pinned RoFormer configuration and source output names before writing parser logic. Test one valid vocal/backing pair, vocal-only output, duplicate vocals, duplicate backing, symlink output, missing/truncated channels and unequal decoded lengths.
- [ ] Add proposed `separate_vocal_and_backing(...)` beside existing `separate_vocal`; both delegate to shared engine execution. Prefer actual paired accompaniment. If the pinned model only returns vocals, create `backing = decoded_source - decoded_vocal` only after sample-rate/layout/latency validation; label it `residual_backing` with explicit artifact diagnostics. Reject unknown latency/phase/frame mismatch rather than manufacturing an apparently valid backing.
- [ ] Replace retry-time `rmtree(out_dir)` with fresh attempt staging in changed paths. Preserve all owner callbacks; release GPU lease only after verified process-tree drain. Failed drain retains receipt/files and blocks reuse.
- [ ] Test cancellation during spawn, model inference and backing render, plus wrapper exit with surviving child. Existing voice preparation, trial and reference import callers still receive only a vocal and preserve their behavior.
- [ ] Run separation regression tests, including `app.reference_separation_test`. Expected: paired artifacts are complete, contained, sample-compatible and reusable; no new network model fetch occurs in tests.

### Task 6: Extract the shared conversion boundary without changing legacy output

**Files:** Create `voice_conversion.py`; modify `voice_build.py`, `voice_build_test.py`, `voice_replacement_test.py`, `artist_production_test.py`, `job_lifecycle_test.py`.

- [ ] First add call-trace tests around existing `_apply_inner`: target artifact pinned at admission, phase-safe mono inputs, same Seed-VC preset/options, duration validation, authorization recheck immediately before engine launch and publication, correct GPU lease and watcher drain.
- [ ] Extract only the engine conversion portion into proposed `convert_vocal(...)`, returning a contained converted dry artifact descriptor. Give it typed source/target settings, owner-owned spawn/command callback, cancellation predicate, progress callback and authorization recheck callback. It must not create tracks, authorize arbitrary voice IDs, own an independent queue, or decide release eligibility.
- [ ] Make the legacy `_apply_inner` call that Seed-VC branch while preserving its current apply/audio-version/provenance flow and failure codes. Keep production `offline=True`, pinned model snapshot, singer range and `require_run` rechecks. Do not broaden strict production's engine literal.
- [ ] Test engine failure, malformed/multiple/no output WAVs, antiphase stereo, silence, truncated output and duration delta above `max(0.25 sec, 1% of source duration)`. A rejected output remains unavailable for mix; raw internal errors stay logged.
- [ ] Run existing replacement/build/production/lifecycle tests. Expected: the extraction changes no legacy endpoint contract or valid mode behavior.

### Task 7: Register RVC models and integrate inference under the replacement owner

**Files:** Create `rvc_models.py`, `rvc_models_test.py`; modify `optional_engines.py`, `optional_engines_test.py`, `api/routes_replacement.py`, license evidence records/tests; extend `voice_conversion.py` and replacement contracts.

- [ ] Write registry tests before import logic: model must bind a real voice profile, checkpoint SHA256 and optional index SHA256, observed engine revision/runtime, auxiliary model identities, source URL/evidence, code/weights restrictions and singer/voice rights declaration. A Seed-VC-ready status is not required for an RVC model association. Import/list/delete use the explicit RVC registry endpoints in the endpoint table; all binary imports have their own stream/metadata limits, supported suffix checks and loopback/local-origin guard rather than passing `.pth` through the audio filename validator.
- [ ] Test changing checkpoint/index bytes after registration, wrong engine type, malformed entry, symlink escapes, absent auxiliary HuBERT/RMVPE, absent license/rights evidence, revoked artist consent and deleting a model referenced by an active job. Return stable blocked/readiness reasons.
- [ ] Implement a separate contained immutable RVC registry. Keep `StoredModel`/`ModelArtifact` Seed-VC semantics intact. Import opaque checkpoint bytes by bounded local upload/selection into managed storage; do not deserialize `.pth` in the FastAPI process.
- [ ] Inspect exact installed RVC `infer/cli.py`, pin/record its revision and confirm supported flags, expected index semantics, F0 options, outputs and offline dependencies. Existing `convert_rvc` arguments are a starting point only; they are not proof that an arbitrary upstream revision supports them.
- [ ] Gate unsafe checkpoint deserialization: verify the pinned engine's loader and trust model. If it can execute arbitrary pickle, allow only explicit trusted-local model import with a prominent concrete trust acknowledgment and documented limitation; reject untrusted/downloaded model import and archive extraction. Do not claim that process supervision is a security sandbox. Future safe-loader conversion is separate unless needed to make this path acceptable.
- [ ] Extract the inspected RVC command preparation from `convert_rvc`, preserving its standalone endpoint. Add an internal owner-driven conversion path that receives contained resolved model/source/output paths, records model/auxiliary hashes, uses existing supervised launch and GPU lease, and does not call `_run`'s global standalone admission from inside the replacement owner.
- [ ] Reuse `voice_authorization.authorize/recheck/record` for the associated voice at admission, engine launch and publication, including retention/freeze rules. An unbound legacy voice remains explicitly unreviewed; a registered artist cannot bypass consent through the RVC route.
- [ ] Mock model execution and assert new replacement inference, timeout, cancellation, reload, failed descendant drain and standalone optional-engine calls preserve worker ownership. No `.pth` is actually loaded by unit tests.
- [ ] Add engine selection/readiness data for Seed-VC and registered RVC models. License badges distinguish code, weights and user-trained voice evidence; “MIT code” is not singer permission or a commercial-release decision.
- [ ] Run RVC/optional-engine/artist authorization regressions. Stop the engine selector release if CLI or trust/evidence gates fail; keep Seed-VC and prepared workflows usable.

**Applio candidate spike, independent of RVC completion:**

- [ ] Record official pinned code/license/model/auxiliary sources, inspect its inference CLI and supported RVC checkpoint/index versions, and compare against the same CPU/M4 Max fixture inputs. No dependency or checkpoint download occurs without a later authorized implementation step.
- [ ] Measure memory, wall time, pitch preservation, duration drift, offline operation and child cancellation under the same owner. Verify MPS rather than repeating a README platform label.
- [ ] Produce a compatibility record in `docs/voice-replacement-quality.md`: accepted with concrete evidence and a separate adapter task, or rejected with exact blockers. Do not relabel RVC as Applio or require the spike to finish to ship reviewed prepared mode.

### Task 8: Integrate bounded alignment and revisioned manual edits

**Files:** Modify shared `music_alignment.py`/tests only through plan 01 ownership; create replacement alignment integration tests in `replacement_jobs_test.py`; implement plan/edit handling in `replacement_jobs.py` and routes.

- [ ] Add synthetic fixtures from float PCM, generated at 48 kHz: three one-second harmonic phrases with a 20 ms attack, separated by 0.6/1.0-second silences; fundamental sequence 220/277.18/329.63 Hz with deterministic low-level noise and overtone envelopes. Known transformations: +0.240 sec initial delay; duration ratio 1.10; independent phrase shifts +0.120/−0.180/+0.090 sec; missing middle phrase; repeated last phrase. Preserve exact sample onset labels separately from detected features.
- [ ] Add failing integration expectations: identity anchors produce identity timing within one output sample when processing is off; aligned known shifts are within ±20 ms on these simple synthetic fixtures; missing/repeated/unrelated material yields `needs_review`/unmatched phrases, never an invented high-confidence match. Synthetic success does not establish the report's real-song target.
- [ ] Request features only on bounded vocal phrase regions. Use 10–20 ms hop, robust per-feature normalization and explicit low-information rejection. Shared foundation must use banded/segmented computation, not dense whole-song `librosa.sequence.dtw`; cap local phrase length at 30 sec, phrase count at 1000, analyzed total at 2 h, feature cells per phrase at 2 million and alignment worker wall time at 120 sec per phrase. Return `replacement_alignment_capacity` with a manual-anchor path when exceeded.
- [ ] Use vocal-activity/silence boundaries to separate phrase assignment from within-phrase warp. Match phrases monotonically with bounded candidate search; represent gaps/unmatched phrases. Do not stretch minutes of silence or map repeated choruses backwards.
- [ ] Convert path into `WarpAnchor(source_sec=prepared time, target_sec=original time)`, with endpoint/frame bounds, positive deltas, no crossing, supported crossfades and `max(0.75, 1/max_stretch) <= delta_target/delta_source <= min(1.5, max_stretch)`. Default 1.25 means [0.8,1.25], not [0.75,1.5]. Never clamp an invalid path silently: split at real silence, request review or reject the edit.
- [ ] Render each phrase from retained dry prepared PCM with shared stretch, preserve channel coherence and frame count, crossfade joins only inside permitted boundaries, and compose into the original timeline. Never call a pitch-changing resampler to perform tempo-only alignment.
- [ ] Persist per-phrase onset deviation, stretch extremes, feature mismatch and confidence basis. “Confidence” is a heuristic screening score until calibrated; do not label it probability of correct lyrics/singer identity.
- [ ] Implement `GET /api/voices/replace/plans/{id}/alignment` and `PATCH .../alignment` with `expected_revision`. Manual anchors are validated server-side, append a revision, invalidate dependent previews/conversion as needed, and retain prior alignment for undo/history.
- [ ] Test concurrent edits, 409 revision mismatch, edit during conversion, cancel during feature extraction/worker spawn, corrupt worker report, low disk during stretch and source hash mutation. Freeze render requests to one revision; either reject edit as busy or cancel/drain old attempt before accepting it. Choose the latter only if UI states make it explicit; never publish an old attempt as the new revision.
- [ ] Run alignment and workflow tests. Expected: bounded safe behavior and honest review state, with no real-song accuracy claim yet.

### Task 9: Implement the durable mode pipeline and stage invalidation

**Files:** Create `replacement_jobs.py`, `replacement_jobs_test.py`; modify `main.py`, `work_busy.py`, `resource_admission.py`, `setup_admission_test.py` and routes.

- [ ] Add tests proving mode dispatch: convert separates and converts once; prepared separates, aligns and renders with no model/voice lookup; hybrid aligns dry prepared first and converts that exact artifact once. None invoke ACE generation merely to submit a replacement.
- [ ] Implement accepted JSON `POST /api/voices/replace/plans` (draft), list/get plans, explicit `POST .../{id}/prepare`, `.../preview`, `.../render`, `.../cancel`, `.../retry`, and PATCH alignment/mix requests with revision. The four-step UI drives these actions; creating a draft does not unexpectedly run a GPU model.
- [ ] Persist selected source snapshots and attempt/phase before launching work. Use existing resource admission and GPU lease machinery, one active attempt per plan and bounded replacement concurrency of one initially. Do not hold the global admission lock while waiting for model inference or a human edit.
- [ ] Call paired separation and shared analysis; publish verified stems/prepared/align/convert artifacts after each stage. Register immutable source copies as evidence references and retained vocal/backing/aligned files as transform assets with explicit parents, then resolve analysis via `MusicSourceRef(kind=production_asset)` and exact hashes. Reuse the existing evidence graph without changing strict eligibility rules; never overwrite existing source-track stems to make analysis fit. Follow mode order above. Do not force conversion to complete before the user can A/B separated stems or review an ambiguous alignment.
- [ ] Implement deterministic stage keys from exact input hashes, engine/checkpoint/auxiliary fingerprints and settings. Validate cached bytes before reuse. An artifact settings key is evidence of matching inputs, not proof of perceptual quality.
- [ ] Implement the invalidation table below, with call-count assertions proving which stage reruns. Any identity change produces a new immutable artifact rather than overwriting one used by a previous render.
- [ ] Add owner startup recovery before accepting work, `work_busy()` resource accounting, cancellation draining and shutdown registration. Record unverified workers as cleanup blocked; do not mark idle merely because a Python task completed.
- [ ] Test closing the browser mid-separation/mix: backend completes or records failure and a reload finds its plan/artifacts. Recover exact supervised process identity after backend crash; never kill a bare PID reused by an unrelated process. Mark interrupted work interrupted; do not claim full training/inference resume unless actually restored.
- [ ] Test repeated cancel, cancel during spawn/publication, shutdown during each stage, wrapper exits with a living child, failed process termination followed by a successful drain, and retry after disk-space recovery.
- [ ] Run workflow/lifecycle/admission tests and desktop unit tests. Expected: no surviving owned child, no lost complete artifact, no false completed job and no setup/runtime collision.

| Edit | Stages invalidated | Stages explicitly reused |
|---|---|---|
| Master source version/hash | Separation onward | Earlier renders remain separate outputs only. |
| Separator/checkpoint | Separated vocals/backing, source analysis, fit/convert/mix | Raw accepted uploads. |
| Prepared version or cleanup/breath setting | Prepared processing, alignment, hybrid conversion, mix | Original separated stems and their analysis. |
| Alignment anchors/max stretch | Aligned artifact, hybrid conversion, mix | Source/prepared raw artifacts; original separation. |
| Converter/voice/checkpoint/index | Converted dry and downstream | Separation, prepared/aligned dry, source analysis. |
| Approved pitch setting | Pitch-processed input and conversion/mix where applicable | Original sources/stems; original alignment geometry if features are unaffected, otherwise regenerate and review. |
| Vocal gain/master LUFS/EQ/compression/IR/wet | Matched vocal/preview/master | Separated backing, aligned dry and converted dry. |
| Plan title | Metadata only | Every audio stage. |

### Task 10: Measure loudness and render repeatable mixes

**Files:** Create `replacement_mix.py`, `replacement_mix_test.py`; use shared `music_dsp.py` and existing `audio_quality.py`; add integration cases to `audio_export_quality_test.py`.

- [ ] Before changing mixing, add tests for original-vocal LUFS vs a new vocal with different silence/peak distribution; show why mean-level matching can differ. Add silence, <3-sec phrase, non-finite measurement, true-peak overload and clipped input cases.
- [ ] Measure original vocal, backing, new dry vocal and original master using existing `audio_quality.measure` in owner-controlled staging. Store method/FFmpeg version and hashes. Do not compare a full-master LUFS number directly against a solo vocal target.
- [ ] Apply `target_vocal_lufs - measured_new_vocal_lufs` bounded to ±12 dB; record requested/applied gain and warning if capped. Use whole-vocal/programme measurements for final gain; per-phrase readings are diagnostics because gated short material can be inconclusive.
- [ ] For silent/too-short/unmeasurable material, keep it unnormalized with `replacement_loudness_unavailable` and visible review requirement; never turn `−inf` into huge gain. A preview remains possible with bounded manual gain and explicit warning, not a claimed LUFS match.
- [ ] Mix matched vocal with retained backing without normalizing the backing independently. Match master loudness to the original only within explicit profile/bounds and true-peak constraints; show infeasible target warnings. Existing `LoudnessSettings` custom target range is −40 to −8 LUFS, so do not silently clamp an original outside that range or pretend `music` means “match original.”
- [ ] Reuse existing two-pass normalization/true-peak processing. Measure the completed output again after encoding; the limiter setting alone is not evidence. Prefer headroom/true-peak safety over forcing original-master loudness. Persist residual LUFS error when both cannot be achieved.
- [ ] Use server-rendered previews with the same settings/filters as export; a browser preview may show a pending revision but must not pretend it is playing unrendered backend settings. Float PCM intermediates preserve headroom; final encoding is an existing export choice.
- [ ] Add numeric tests with known gain: the attainable vocal target is within ±1 LU and final measured true peak is at most −1 dBTP plus documented measurement tolerance ≤0.1 dB. Include a hard clipped source: report source clipping; do not claim to repair it.
- [ ] Test preview/export transform equivalence at equal PCM format, exact intended duration, no extra tail, no duplicate backing, mono compatibility, channels preserved and no stale cache after a setting change.
- [ ] Run numeric and export-quality tests. Expected: actual output evidence supports badges and warnings; no claim that a limiter makes an output “streaming compliant.”

### Task 11: Add bounded cleanup, pitch/tonal/space/bleed diagnostics

**Files:** Modify `replacement_mix.py`, `replacement_mix_test.py`, shared DSP contracts/helpers under plan 01; extend replacement mix response.

- [ ] Add tests distinguishing cleanup disabled from enabled, transient breaths from sustained silence, valid/out-of-range pitch suggestions, known delayed correlated backing from unrelated signals, and a deliberately phase-inverted signal.
- [ ] Define `cleanup='light'` as existing inspected conservative vocal cleanup, default off, preserving dry originals. Inspect `clean_vocal.py`/`prep_vocal.py` before reuse: training preparation may crop/normalize and cannot be assumed safe for full-length performance timing. If unsafe, implement bounded shared DSP cleanup without trimming the timeline.
- [ ] Treat de-reverb/de-echo as separate capability-gated processing with a pinned independently reviewed model/algorithm and preserved dry output. Do not display a working checkbox that maps to ordinary denoising. If no verified engine exists, show unavailable with a concrete reason while retaining the rest of the wizard.
- [ ] Estimate voiced F0/range against reference melody/key with uncertainty and singer-range restrictions where available. Display proposed transpose/nudge and confidence; no automatic scale-snapping, identity inference or imposed pitch correction. Keep artist range assessment unchanged.
- [ ] Implement optional gentle matching EQ using robust long-term spectral bands, silence masking and ±6 dB cap per band. Disable invalid/under-supported bands; list actual corrections, compare pre/post metrics, and preserve bypass. Gentle compression is opt-in with explicit bounded threshold/ratio/attack/release and no hidden makeup gain beyond +3 dB.
- [ ] Render optional owned/CC0 IR convolution through shared DSP, maximum IR 10 sec, wet ≤25%, latency compensated, bounded tail and explicit truncate/fade policy to the master duration. Record IR identity/license evidence. No IR URL/file path in render requests.
- [ ] Implement a **bleed risk diagnostic**, not a singer detector: estimate lagged correlation/coherence between retained accompaniment and source/converted residual in active vocal regions; store lag/window/sample basis and inconclusive cases. Waveform subtraction of two different voices does not measure original-singer audibility. High correlation can be legitimate shared instrumentation; low correlation is not proof of no bleed.
- [ ] Show original vocal/backing/new vocal A/B, phrase-loop and an honest warning for medium/high/inconclusive bleed; user review can annotate a warning but must not erase measured evidence. De-reverb may improve or damage timbre; require listening, not a guaranteed “clean” badge.
- [ ] Run bounded DSP/diagnostic tests. Expected: disabled processing is identity within numeric tolerance and each enabled process is bounded, attributable and reversible through retained dry sources.

### Task 12: Retain stems and implement remix without another model run

**Files:** Modify `replacement_jobs.py`, `replacement_store.py`, routes and tests; `db.py`, `api/routes_tracks.py`, `audio_versions.py`, related deletion tests.

- [ ] Add a call-count regression: first hybrid render calls separator/converter once; changing EQ/gain/master/IR and calling `POST /api/voices/replace/plans/{id}/remix` calls both zero additional times and creates a distinct master artifact and result track.
- [ ] Add retention tests: cancelling mix keeps verified original vocal/backing/aligned/converted dry; deleting a published result does not delete source/prepared/stems referenced by another revision; deleting a source used by an active plan is blocked until the owner is drained.
- [ ] Implement explicit retention references for accepted source version, prepared version, model snapshot, retained artifacts and published tracks. Track deletion protection must integrate with existing reference/version/apply/export removal contexts, including symlink/containment checks. Do not assume foreign keys alone retain files.
- [ ] Extend existing unshared-file/directory removal accounting to recognize replacement references, or block removal with `replacement_source_in_use` until the plan is explicitly deleted. Prefer a clear block to silent audio loss. Do not recursively remove a common separator directory to clean one result.
- [ ] Implement remix admission using a pinned completed dry vocal/backing pair and revision; a changed/missing artifact returns `replacement_artifact_changed`/`replacement_artifact_missing` and asks for explicit stage recomputation, never silently reruns a model.
- [ ] Publish each master atomically to a new library track with plan/revision/parent identity in typed metadata. Poll/list/render responses return saved track IDs and immutable artifact URLs, enabling DAW insertion through existing saved-track mechanisms.
- [ ] Test low disk after render but before publication, metadata fsync failure, DB rollback, cancellation immediately after promotion and reload after HTTP disconnect. Previously published tracks stay playable and retry publication is idempotent.
- [ ] Run deletion, audio-version, replacement and export tests. Expected: recoverable independent outputs, no unused voice lookup and no surprise model execution during remix.

### Task 13: Carry rights/evidence and source lineage through all exports

**Files:** Modify `export_provenance_contracts.py`, `export_provenance.py`, `audio_exports.py`, existing export tests; use plan 01 evidence schema, `artist_production.py` and authorization unchanged in policy.

- [ ] Add tests for prepared human, prepared AI, unknown-origin mix, restricted third-party source, NC source/model, unknown weight identity and owned IR; their restrictions survive conversion, remix, library insertion and re-encoding.
- [ ] Add a typed optional replacement provenance record with plan/revision/render IDs, source/prepared/stem/IR hashes, alignment/settings digests, converter/auxiliary identities, license evidence references and measured audio. Reuse `ProvenanceComponent` and shared evidence contracts. Never cram structured provenance into a free-form transformations string alone.
- [ ] Resolve known source generation records before accepting user declarations; an app-known AI source cannot be reclassified as human merely by changing the prepared-mode dropdown. Distinguish declared, observed and reviewed evidence.
- [ ] Compute commercial status on the backend from all upstream source/model/sample/IR restrictions and review state. Separate “no known NC component” from “approved for release”; unknown remains unknown/review required. GPL code provenance and model/singer/composition/master rights are different evidence axes.
- [ ] Recheck artist authorization/retention before serving a converted final export where existing rules require it. A previously prepared dry non-voice artifact does not grant consent for an associated registered singer model.
- [ ] Prove generic prepared/hybrid output does not acquire `production.approved_for_release` and cannot enter strict clean rendering without valid isolated human-guide lineage, independent accompaniment, current consent/range and release review. Preserve strict `VoiceModelSnapshot.engine='seed-vc'` until a separately scoped strict RVC production design exists.
- [ ] Test exporting through ordinary download and tagged/normalized export paths; both include the same parent components and current output measurement. Rights records remain local/informational, not legal certification.
- [ ] Run artist/authorization/provenance/export regressions. Expected: no generated-mode clearance bypass and no NC/unknown evidence disappearing at publication.

### Task 14: Add generated client APIs and a reload-safe plan store

**Files:** Create `frontend/src/api/replacement.ts`, `.test.ts`, `stores/replacement.ts`, `.test.ts`; modify generated contracts and existing ACE store only at the entry/history integration points.

- [ ] Write API tests for every planned endpoint, strict body schema parsing, JSON/multipart bounds, 409 revision details, stable typed errors and `AbortSignal` propagation. Responses are parsed from `unknown` using generated parsers.
- [ ] Write store tests for draft load/create, phase updates, prepared mode without voice, immutable revision selection, reconnection, cancel/retry, duplicate action prevention and remix call counts.
- [ ] Implement typed API actions with generated request/response models; do not duplicate backend enums or cast unknown responses to the desired shape.
- [ ] Add store observation generation and per-plan action generations. Stop timers/abort observation on teardown; a late list/get/upload/edit response cannot replace a newer revision, recreate a deleted plan or restart polling after stop.
- [ ] Make edit UI optimistic only while it holds the expected server revision. Keep an explicit pending patch and previous server snapshot; on failure revert the local change and refresh, on 409 display current server revision and require review/reapply. Never manufacture optimistic “done,” output URLs or legal approval.
- [ ] Rehydrate plans/artifacts/render tracks from server on reload, not local-storage business state. Show disconnected observation separately from backend job failure; browser navigation does not cancel server-owned work automatically.
- [ ] Preserve all old `aceStep.ts` replacement cards/actions and their old response parser. Recognize `params.source='voice_replacement_v2'` separately and route its action to the new store by actual plan ID/revision.
- [ ] Run API/store targeted tests. Expected: no stale-response resurrection, no hidden ready-voice requirement and no duplicate backend action from double click.

### Task 15: Build the four-step wizard and accessible alignment review

**Files:** Create wizard/alignment/mix Vue components and tests; modify `GenerateForm.vue`, `JobCard.vue`, existing replacement tests, `BatchABPlayer.vue` only as needed, locale module/registration.

- [ ] Write UI tests for Import → New voice → Fit & preview → Mix & export, including back/forward navigation preserving draft values and loaded source IDs. Display accepted source filenames/durations and upload validation errors.
- [ ] Implement three explicit mode cards: convert, prepared, hybrid. Show voice/engine selector only where needed; prepared can advance with only source/prepared audio. Library select/upload produces the same backend identity. No TTS-sing generator is added; already generated library vocals can be selected with their source classification preserved.
- [ ] Show separator/engine readiness, exact code/weights evidence badges, setup links and disabled reasons; selection never downloads a model. Provide A/B of original vocal and backing before a costly converter run.
- [ ] Implement phrase list and waveform anchor view using existing waveform/design components where possible. Each anchor has numeric source/target seconds fields plus pointer dragging; Tab/Shift-Tab navigates, arrow keys nudge 10 ms, Shift+arrow 100 ms, Enter commits validated edits and Escape restores uncommitted local movement. Keyboard controls operate only when the editor is focused.
- [ ] Render mismatch/stretch/confidence diagnostics as text and icons, not color alone. Unmatched/needs-review phrases expose “mark reviewed” and manual anchors; missing material requires an explicit gap/mute decision, never an invented performance.
- [ ] Add phrase A/B loops to existing `BatchABPlayer` through optional typed `loopStartSec/loopEndSec` and controlled selection only if necessary; test boundary seeking and async playback guards. Space toggles focused playback, A/B switches focused variants, Escape stops. Never register global shortcuts while text fields are being edited.
- [ ] Mix panel displays measured original/new vocal LUFS, master target/achieved LUFS, true peak, gain cap and bleed warning; pending server preview is visibly pending. Changing mix settings calls remix, while converter/anchor edits explicitly indicate their stage cost.
- [ ] Add accessible focus restoration when moving wizard steps, `aria-current='step'`, labels, error summaries, `aria-live='polite'` progress and keyboard alternatives for all pointer actions. Use existing translation pattern in `voiceReplacement.ts` and `en.ts`; no inline English control labels or fake additional-language support.
- [ ] Integrate saved result cards with original/result playback, revision, provenance/metrics, cancel/retry, open wizard, download and existing DAW insertion. Legacy cards remain distinguishable and functional after reload.
- [ ] Test small-screen layout, keyboard-only completion, unmount during upload/play promise, failed preview, unavailable engine, revision conflict from another tab and server work continuing after route change. Stop/release all complete audio graphs and media elements on teardown.
- [ ] Run UI/store tests and full frontend build. Expected: strict type-safe wizard with accessible no-voice prepared path and accurate progress, rather than an engine dropdown bolted to the old ready-voice guard.

### Task 16: Evaluate quality on held-out performances, then set defaults

**Files:** Create evaluation script/listening test/protocol artifacts from file map; update `docs/voice-replacement.md`, `docs/voice-replacement-quality.md`, `ROADMAP.md` with actual evidence only.

- [ ] Build a rights-cleared local held-out set of ten songs with isolated reference vocals and independently recorded prepared takes, phrase-onset annotations and source/master evidence. Include varied tempos, rubato, silence, breaths, sibilance, reverb, harmonies and one incompatible take. Held-out recordings never enter voice training/reference selection or tuning of thresholds.
- [ ] Validate fixture manifests in unit tests: every local input has hash, permission/evidence record, separation of training/evaluation roles, annotation units and expected source identity. Missing rights files fail before inference. Keep copyrighted/GPU media out of Git and ordinary test discovery.
- [ ] Implement the evaluation script with explicit input/output directories, model IDs/engine fingerprints, no network operation, known result metrics and bounded resource use. Save machine/OS/FFmpeg/engine/checkpoint IDs, wall time, peak memory, sample/frame count, onset match coverage, LUFS error, true peak, warnings and cancellation observations.
- [ ] Calculate onset matching against annotated phrase onsets with a 30 ms tolerance and one-to-one matching; report per-song precision/recall and the fraction of annotated phrases meeting tolerance. Do not average away missing phrases or cherry-pick only accepted outputs. `mir_eval` is optional evaluation-only after primary-source/license review; a tiny explicit one-to-one matcher is sufficient if it avoids an unnecessary dependency.
- [ ] Measure the proposed real-song target: ≥90% of phrase onsets within ±30 ms across the preregistered ten-song set, with per-song breakdown and all failures. If incompatible input is intentionally out of scope, predeclare exclusion before running and separately score its correct rejection; do not quietly remove it after seeing results.
- [ ] Measure attainable final vocal loudness within ±1 LU of reference and master true peak ≤−1 dBTP (measurement tolerance documented). Report target conflicts, gain caps and inconclusive cases instead of scoring them as passes.
- [ ] Run blind randomized level-matched A/B with five listeners, original vs new full mix and dry-vocal diagnostics; avoid telling listeners which engine produced each take. Record their permission, rubric and individual ratings for intelligibility, timing, timbre, artifacts and overall acceptability. Proposed gate: at least seven of ten songs rated acceptable-or-better by a predeclared majority of listeners. This is a proposed small-panel gate, not proof of market-wide preference.
- [ ] Compare high Demucs, verified RoFormer and available converters using identical sources/settings. Record target M4 Max memory/time and operational stability; do not extrapolate CPU/MPS/CUDA support without platform evidence.
- [ ] Set the recommended separator/converter from observed quality/latency/rights tradeoffs; keep RoFormer optional if the benefit, checkpoint evidence or installation reliability is unproven. Mark RVC experimental if singing quality/platform gates are missing.
- [ ] Document de-reverb/bleed/alignment failures, unsupported hardware and exact manual fallback. Update ROADMAP status only for evidenced gates; retain “experimental” labels for unverified real-song/platform behavior.

## Stable failure behavior

All codes below are proposed public literals for this workflow. Keep HTTP shape consistent with the chosen response contract and existing client error parsing. Do not return raw stderr, server paths, prompt text or exception traces.

| Code | Status/action | Data behavior |
|---|---|---|
| `replacement_invalid_request` | 422; correct fields | No job accepted. |
| `upload_too_large`, `invalid_audio`, `audio_too_long`, `unsupported_audio_format` | Existing upload semantics | Roll back only unaccepted intake. |
| `replacement_source_changed`, `replacement_artifact_changed`, `replacement_model_changed` | 409; review pinned identity | Keep earlier good artifacts/renders, block new publication. |
| `replacement_source_missing`, `replacement_artifact_missing` | 404/409 by resource vs dependency | No silent model rerun; explicit recovery/reselection. |
| `replacement_revision_conflict` | 409 with current revision | Preserve both server history and local pending edit for review. |
| `replacement_request_conflict` | 409; request ID reused for other content | No duplicate/altered action. |
| `replacement_busy`, `replacement_cleanup_pending` | 409; wait or retry cleanup | No overwrite/deletion while owner exists. |
| `replacement_engine_unavailable`, `replacement_separator_unavailable`, `replacement_engine_incompatible` | 409; setup/choose available engine | No automatic download or fallback that changes selected evidence. |
| `replacement_alignment_capacity`, `replacement_alignment_unavailable`, `replacement_alignment_invalid` | Needs review or 422 for bad manual edit | Preserve dry input; manual anchors/new take available. |
| `replacement_loudness_unavailable`, `replacement_target_unattainable` | Review warning | No infinite gain or false target-met badge. |
| `replacement_output_duration_mismatch`, `replacement_output_invalid` | Failed stage | Reject partial/bad output, retain inputs. |
| `replacement_disk_full`, `replacement_storage_failed` | Failed stage; retry after repair | Keep verified prior artifacts, no recursive cleanup. |
| `replacement_source_in_use`, `replacement_model_in_use` | 409; drain/delete dependent plan explicitly | No cascading data loss. |
| `replacement_model_untrusted`, `replacement_evidence_required` | 409; trusted-local/evidence review | No checkpoint load or commercial approval. |
| `replacement_interrupted`, `replacement_timeout`, `replacement_cancelled`, `replacement_failed` | Durable terminal state | Own/drain children, retain verified stages and structured diagnostics. |

Existing artist consent/retention/range error codes pass through only if present in the typed public union; retain their existing meaning. Missing evidence, cancellation and quality review are different states.

## Future verification commands

Run these during implementation, **not as a claim that this plan has passed them**. Use Python 3.12 environment with repository test dependencies. These commands intentionally isolate every configured storage and engine root before imports. Do not point them at the user's library or installed Seed-VC checkout.

From repository root, create a fresh verification root and set environment in the same shell:

```bash
MUSIC_VOICE_TEST_ROOT="$(mktemp -d -t openfabric-voice-tests.XXXXXX)"
export OPENFABRIC_CONFIG="$MUSIC_VOICE_TEST_ROOT/config.json"
export OPENFABRIC_DATA_DIR="$MUSIC_VOICE_TEST_ROOT/library"
export SEED_VC_DIR="$MUSIC_VOICE_TEST_ROOT/seed-vc"
export OPENFABRIC_MODULE_ROOT="$MUSIC_VOICE_TEST_ROOT/modules"
python -m pip install -r backend/requirements-test.txt
```

Run targeted backend checks from `backend/` after the corresponding files exist:

```bash
python -m unittest app.replacement_contracts_test app.replacement_store_test app.replacement_routes_test -v
python -m unittest app.voice_separation_test app.reference_separation_test app.stems_test -v
python -m unittest app.voice_build_test app.voice_replacement_test app.optional_engines_test app.rvc_models_test -v
python -m unittest app.replacement_jobs_test app.replacement_mix_test app.job_lifecycle_test app.setup_admission_test -v
python -m unittest app.artist_production_test app.audio_versions_test app.audio_exports_test app.audio_export_quality_test -v
```

Run frontend targeted checks from `frontend/`:

```bash
npm test -- src/api/replacement.test.ts src/stores/replacement.test.ts src/views/ace-step/VoiceReplacementWizard.test.ts src/views/ace-step/ReplacementAlignmentEditor.test.ts src/views/ace-step/ReplacementMixPanel.test.ts src/stores/voiceReplacement.test.ts src/views/ace-step/VoiceReplacementCard.test.ts src/components/shared/BatchABPlayer.test.ts
```

Final required suite, retaining the same isolated environment:

```bash
# From repository root:
python backend/scripts/generate_contracts.py
python backend/scripts/generate_contracts.py --check
# From backend/:
python -m unittest discover -s app -p '*_test.py' -v
# From frontend/:
npm test
npm run build
# From desktop/:
npm test
```

Add new owned code/contracts/workers to the existing strict CI scope. Run the **entire existing** strict mypy loop in `.github/workflows/ci.yml`, preserving all its legacy checked files, then add the following group from `backend/`:

```bash
for target in linux darwin win32; do
  python -m mypy --strict --follow-imports=silent \
    app/replacement_contracts.py app/replacement_store.py app/replacement_jobs.py \
    app/replacement_mix.py app/voice_conversion.py app/rvc_models.py \
    app/api/routes_replacement.py scripts/evaluate_voice_replacement.py \
    --platform "$target"
done
```

This additional command does not replace the existing loop or imply whole-backend typing. `voice_build.py` remains legacy code; extracted boundaries must be strict and annotate all new public helpers/callbacks. Follow plan 01's accurate third-party declarations rather than introducing `Any` or blanket ignores.

For the later explicit quality gate, the proposed script interface must be implemented and tested in Task 16 before use:

```bash
python backend/scripts/evaluate_voice_replacement.py \
  --manifest /absolute/path/to/cleared-heldout/manifest.json \
  --output-dir /absolute/path/to/private-evaluation-results \
  --engine seed-vc \
  --voice-id 0123456789abcdef0123456789abcdef \
  --model-id reviewed-local-model-id
```

Do not run this command against an arbitrary fixture directory or invent a successful result. The shown voice ID is an illustrative syntactically valid ID and must be replaced by the evaluated local voice profile; the script resolves its actual model revision and verifies the fixture manifest. A separately recorded RVC run uses `--engine rvc` and its registered model ID, deriving/validating the associated voice; no automatic engine/model setup is part of the evaluation script.

## Completion and review gates

- [ ] Review the final diff for input validation, transaction boundaries, stale updates, source/model hash pinning, generated contract drift and rights propagation.
- [ ] Confirm legacy upload/convert/retry/cancel/reload and strict human-guide production behavior with actual test output.
- [ ] Confirm prepared mode needs no unused voice and hybrid consumes the reviewed aligned dry take.
- [ ] Confirm every new worker has startup recovery, busy accounting, timeout, descendant drain, cleanup-pending behavior and shutdown coverage.
- [ ] Confirm remix uses retained dry vocal/backing and publishes independent track/revision/provenance without separation or conversion calls.
- [ ] Confirm source/prepared/original stems and previously published results survive cancellation, low disk and failed publication.
- [ ] Confirm exact future commands ran successfully before reporting them as passed; report separately any platform/GPU/listening gates not run.
- [ ] Publish quality labels only after the held-out and listener gates; otherwise describe the path as experimental with concrete failure modes.
- [ ] Do not commit, push or deploy while executing this plan without the maintainer's explicit request and target branch. Stop at a reviewable diff with actual verification evidence.

Skipped intentionally: RVC training, built-in TTS singing generation, so-vits-svc/OpenVoice integration, automatic model downloads, note-by-note pitch correction, a second job server, default RoFormer without evidence and automatic commercial clearance.

Remaining risk: phonetic/content mismatch, polyphonic vocals and separator bleed can defeat alignment or timbre replacement; synthetic tests cannot establish singing quality, model trust, hardware support or legal permission. The held-out human listening and provenance gates remain necessary.
