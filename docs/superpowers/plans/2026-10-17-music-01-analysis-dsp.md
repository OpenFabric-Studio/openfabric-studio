# Shared Music Analysis and DSP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This is a planning artifact; none of its implementation steps authorize a commit, push, dependency installation, model download, live processing job, or production migration.

**Goal:** Give Replace Voice, Mashup, Beat Maker, Asian Beat Maker, and Song from Beat one trustworthy, persistent analysis format and one bounded, reproducible backend transformation path.

**Architecture:** Add a source-version-bound analysis cache and owned analysis jobs using the repository's SQLite, subprocess supervisor, admission, and generated-contract conventions. Keep DSP as a typed service called inside the owning voice/mashup/beat workflow; those workflows persist their own revisions, previews, and outputs. Keep model inference and native stretch dependencies in an explicitly installed, pinned environment, retain original sources, and return unknown/review-required results when musical evidence is insufficient.

**Tech Stack:** Existing FastAPI, Pydantic 2, SQLite, NumPy, FFmpeg/FFprobe, `audio_quality`, `video_process`, and `job_lifecycle`; proposed isolated `beat_this`, librosa, SciPy, and `python-stretch` dependencies after artifact/license/platform verification. Existing Vue 3 generated contracts and Vitest integration tests; Python unittest and strict mypy for Linux/macOS/Windows typing paths.

---

## Status, scope, and evidence

This plan implements report §2, the shared analysis/DSP parts of §3–4, and the shared processing needed by §5–6. Voice-engine integration, mashup arrangement, sample rendering, presets, datasets, and training are owned by the sibling plans. All new module names, wire models, functions, routes, thresholds, and limits below are **proposals**, not claims about existing APIs. Test commands and expected outcomes are future verification instructions; no implementation tests or performance measurements were executed while writing this plan.

Inspected repository HEAD: `4609467881dc90af4c232b6eb67b6a1db7747782`. The report used `d4f4a17`, so its inventory is historical. Preserve uncommitted work and re-read changed anchors before implementing. In particular, HeartMuLa is retired in the present tree. Do not add it back as an executable engine, setup option, queue choice, analyzer, or DSP dependency. Historical origin metadata remains readable through existing compatibility code.

Important corrections to the report:

1. LUFS, true-peak measurement, silence handling, measured normalization, and export target verification already exist. Reuse them; a second `pyloudnorm` implementation would create competing answers to the same question.
2. `video_analysis.py` produces overview heuristics at 8 kHz/50 ms hops. Its estimated beats/energy changes are unsuitable as the authoritative beat/downbeat grid for musical alignment. Preserve that feature; do not quietly relabel its output.
3. `music_queue.py` coordinates current generation engines. It is not a general arbitrary-operation task scheduler. Reuse its resource ownership conventions without inventing a queue API or rewriting working generation scheduling.
4. A Sakoe–Chiba constraint does not make librosa's DTW allocation sparse. Restrict phrase sizes and the matrix-cell budget before calling it.
5. A general Hungarian assignment does not guarantee that lyrics remain in order. The mashup plan must use an ordered dynamic program or otherwise explicitly constrain order.
6. A publisher's MIT/model-card declaration records supplied terms; it does not clear training recordings or a user's song. Reuse the existing evidence/release gate and never infer complete commercial clearance from an analyzer dependency.
7. Western major/minor key inference cannot honestly classify every tuning, raga, makam, or gamelan source. Unknown is a supported result and must not become C major on export.

## Verified integration anchors

Paths and symbols were read in this checkout. Line numbers are navigation aids, not stable patch positions.

| Existing path and anchor | Verified behavior | Planned use |
| --- | --- | --- |
| `backend/app/audio_quality.py:77` `measure` | FFprobe plus FFmpeg loudnorm; channel-preserving float measurement; source SHA-256; finite/null loudness values | Sole loudness/true-peak measurement implementation |
| `backend/app/audio_quality.py:117` `normalization_filter`, `:129` `target_result` | Builds measured normalization, classifies measured output target | Share normalization and output verification |
| `backend/app/audio_quality_contracts.py:8` `LoudnessSettings`, `:25` `AudioMetrics` | Explicit profiles, finite bounded fields, warnings, method/version identity | Import these models into new contracts |
| `backend/app/audio_export_quality_test.py` | Silence, intersample peak, dry-source preservation, output measurement, durable analysis/export regressions | Preserve and extend only where behavior is shared |
| `backend/app/video_analysis.py:1`, `:24`, `:83`, `:197` | Documented overview heuristic, finite chunk checks, streaming CLI, current POSIX-style selector implementation | Keep overview behavior separate; borrow measurement principles, not its cross-platform process loop |
| `backend/app/voice_build.py:510` `remix_filter`, `:522` `vocal_gain`, `:1957` | Existing FFmpeg limiter/RMS-derived gain, bounded ±12.0412 dB | Voice plan switches only new opt-in path to shared measured DSP; retain legacy compatibility tests |
| `backend/app/production_render.py:154`, `:198`, `:218` | Strict guide/accompaniment render, authorization/provenance checks | Call shared DSP through this owner without weakening rights checks |
| `backend/app/artist_production.py:89`, `:121`, `:239` | SQLite transaction, asset path resolver, transformation lineage | Production asset resolution and evidence lineage |
| `backend/app/artist_production_contracts.py:11`, `:72` | 32-hex identifiers, 64-hex digests, source review and parent metadata | Reuse identifier/digest conventions |
| `backend/app/audio_versions.py:256` `resolve_source`, `:385` `protect_track_versions_removal` | Immutable version source resolution and removal protection | Resolve/snapshot source versions and coordinate deletion |
| `backend/app/audio_exports.py:934` `stem_source`, `:917`, `:1020`, `:1065` | Validated stem lookup; guards against replacing/deleting active source files | Follow these guards for analysis/DSP consumers |
| `backend/app/audio_version_store.py:29` `migrate`, `backend/app/db.py:195` | Additive SQLite migrations called during DB initialization | New analysis tables without modifying existing audio-version kinds |
| `backend/app/atomic_files.py:24`, `:42` | Per-document transaction locks, atomic temp-file replacement | Worker manifests and publication metadata where SQL cannot own bytes |
| `backend/app/video_process.py:25`, `:42`, `:141`, `:203`, `:251` | Token/receipt worker identity, bounded output capture, owned process launch, verified recovery termination | Every analyzer/native DSP subprocess uses this ownership |
| `backend/app/job_lifecycle.py:19`, `:59`, `:72`, `:88` | Owned threads, shielded cleanup, cancellation and descendant termination | Drain before deleting scratch files or releasing leases |
| `backend/app/resource_admission.py:26`, `:80`, `:120` | Setup idle checks, native admission, idle-native reservation | Integrate new work with existing admission rules |
| `backend/app/gpu_lease.py:29` `gpu_lease`, `backend/app/stems.py:131` `separate_file` | Owned accelerator lock and separation | Acquire only if a future verified accelerator analysis mode is used |
| `backend/app/music_queue_ownership.py:28`, `:53`, `:76` | Coordinator capability inherited in task context; release requires idle proof | Allow an authorized coordinator's children, block competing manual jobs |
| `backend/app/work_busy.py:67` `local_work_busy` | Composite busy check for local operations | Add analysis busy state; workflow owners account for DSP |
| `backend/app/module_contracts.py:12`, `backend/app/module_catalog.py:33`, `:94` | Fixed module-id/catalog schema and environment roots | Explicit optional analysis installation, not implicit pip calls |
| `backend/app/module_install.py:192`, `backend/app/music_training_setup.py:66`, `:95` | Digest-checked artifacts, pinned recipes, environment identity | Follow the pattern for reviewed analysis/runtime pins |
| `backend/app/main.py:68`, `:158` | Startup recovery, unconditional teardown, route inclusion | Register analysis recovery/shutdown/router |
| `backend/scripts/generate_contracts.py:52`, `:96` | Explicit model registry; runtime schemas generated with client types | Add contract families and regenerate, never hand-edit generated.ts |
| `frontend/src/api/contracts.ts:1`, `frontend/src/api/schemaValidation.ts` | Contract re-export and boundary validation | Generated wire types and runtime parsing |
| `frontend/src/audio/timeStretchEngine.ts:13` | Browser SoundTouch-derived warp with explicit tempo-factor convention | Keep existing editor behavior; new workflow previews play backend-rendered artifacts |
| `.github/workflows/ci.yml:107` | Test requirements, contract check, per-platform strict mypy, unittest | Extend this actual checked scope |

Before edits, `rg` every new call-site symbol in the table. New service work touches callers, API registry, generated types/decoders, deletion guards, lifecycle, setup catalog, fixtures, and CI; editing only one helper would leave the system incomplete.

## File map and ownership

Names in the Create column do not exist in the inspected tree.

| Action | Exact proposed path | Responsibility |
| --- | --- | --- |
| Create | `backend/app/music_analysis_contracts.py` | Source references, analysis result/status, request/response contracts |
| Create | `backend/app/music_analysis_store.py` | SQLite migrations, cache claims, immutable results, revisioned overrides, job transition CAS |
| Create | `backend/app/music_analysis.py` | Source resolution/snapshot, durable analysis owner, cache/admission, recovery/shutdown |
| Create | `backend/app/music_analysis_worker.py` | Isolated, bounded analysis CLI; validated request and result documents |
| Create | `backend/app/music_analysis_features.py` | Typed CPU feature/key/section/activity helpers; worker-only third-party imports |
| Create | `backend/app/music_analysis_environment.py` | Runtime discovery, pin/weights verification and offline preflight |
| Create | `backend/app/music_analysis_setup.py` | Existing module-installer integration for an isolated analysis environment |
| Create | `backend/app/assets/music-analysis-requirements.txt` | Reviewed exact package pins/hashes for the dedicated environment |
| Create | `backend/app/assets/music-analysis-models.json` | Reviewed immutable model source revisions/digests/license evidence |
| Create | `backend/app/music_dsp_contracts.py` | Warp/transform/alignment/mix requests and measured diagnostic result types |
| Create | `backend/app/music_alignment.py` | Phrase correspondence, bounded DTW, monotone anchor reduction, diagnostics |
| Create | `backend/app/music_dsp.py` | Backend orchestration using caller-owned commands and paths; no detached tasks |
| Create | `backend/app/music_dsp_worker.py` | Pinned native stretch, pitch, EQ/compression/ducking/fade processing |
| Create | `backend/app/api/routes_music_analysis.py` | Analysis status/start/cancel/retry/manual-override routes |
| Create | `frontend/src/api/musicAnalysis.ts` | Generated-type client with runtime decoding and AbortSignal |
| Create | `backend/app/music_analysis_contracts_test.py` | Bounds, invariants, status/unknown semantics |
| Create | `backend/app/music_analysis_store_test.py` | Migration, CAS, concurrency, cache invalidation, interrupted publication |
| Create | `backend/app/music_analysis_test.py` | Owned-job lifecycle, admission, snapshot and deletion races |
| Create | `backend/app/music_analysis_features_test.py` | Synthetic key/tempo/activity/section fixtures without model downloads |
| Create | `backend/app/music_analysis_environment_test.py` | Missing/tampered checkpoint and no-network behavior |
| Create | `backend/app/music_analysis_worker_test.py` | CLI bounds, stderr/stdout protocol, channel and window stitching |
| Create | `backend/app/music_dsp_contracts_test.py` | Source, anchor, pitch, gain/EQ/duck bounds |
| Create | `backend/app/music_alignment_test.py` | Identity/drift/silence/repetition/failure and matrix-budget regression tests |
| Create | `backend/app/music_dsp_test.py` | Output timing, pitch, stereo, filters, cancellation, measurement |
| Create | `backend/app/api/routes_music_analysis_test.py` | Validated requests and stable public error/status mapping |
| Create | `frontend/src/api/musicAnalysis.test.ts` | Malformed responses, cancellation, stale polling, unknown handling |
| Create | `backend/app/fixtures/music-analysis/README.md` | Fixture synthesis instructions, provenance and expected annotations |
| Create | `docs/music-analysis-dsp.md` | Formats, optional setup, limitations, performance evidence instructions |
| Modify | `backend/app/db.py` | Register additive analysis migration |
| Modify | `backend/app/main.py` | Analysis recovery/shutdown/router registration |
| Modify | `backend/app/work_busy.py`, `backend/app/resource_admission.py` | Admission/accounting integration without lock recursion |
| Modify | `backend/app/module_contracts.py`, `backend/app/module_catalog.py`, `backend/app/module_install.py`, `backend/app/module_jobs.py`, `backend/app/module_evidence.py`, `backend/app/module_licenses.py` | Optional `music_analysis` module and setup/verification dispatch |
| Modify | `backend/scripts/generate_contracts.py`, `frontend/src/api/generated.ts` | Register/generate contracts and decoders |
| Modify | `backend/app/audio_versions.py`, `backend/app/audio_exports.py`, `backend/app/api/routes_tracks.py` | Existing source-removal/stem-replacement guards include analysis consumers; exact entry points must be re-read |
| Modify | `.github/workflows/ci.yml`, `backend/requirements-test.txt` | New strict scope and mock-based behavioral tests; native proof separate |
| Integrate in sibling plans | `backend/app/voice_build.py`, `backend/app/production_render.py`, proposed mashup/beat render owners, editor project import | Call shared services and retain workflow-owned state/revisions |

Do not create a generic workflow framework, standalone DSP queue, dedicated DSP database, new daemon, Redis, or a browser-only export path. Analysis has a real shared cache requirement; DSP work already has an owning user workflow. Shared code runs under that owner.

## Proposed shared contract, frozen before callers

### Identity, units, and source boundaries

All wire time fields use seconds (`*_sec`) as finite floats. Processing uses integer source/output frames after one rounding conversion. Keep original sample rate and frame count; never derive precise duration from rounded track milliseconds. Use float64 for feature math, accumulated timeline positions, filter coefficient calculations, and mixing accumulators; float32 PCM/native engine buffers. Arrays sent to the browser contain summaries/anchors, never raw PCM or a dense feature matrix.

`MusicSourceRef` is a Pydantic discriminated union with `extra='forbid'`:

| Kind | Required fields | Resolution |
| --- | --- | --- |
| `version` | `track_id`: strict integer >0, `version_id`: 32 lowercase hex | `audio_versions.resolve_source` |
| `stem` | `track_id`, `stem`: `vocals|drums|bass|other|accompaniment`, `sha256`:64 hex | Existing validated stem resolver plus exact hash check |
| `production_asset` | `asset_id`:32 hex, `sha256`:64 hex | `artist_production.asset_path` plus evidence hash and policy checks |

The source resolver returns a **proposed internal** frozen `ResolvedMusicSource` with reference, controlled snapshot path, SHA-256, sample rate, channels, frame count, and original provenance references. User JSON never supplies a pathname, executable, model URL, ffmpeg filter string, arbitrary analysis algorithm, or arbitrary command-line option. If a workflow starts with a file upload, its upload/import path creates a retained source first.

Snapshot rules: acquire the existing deletion/replacement protection, copy to owner scratch using an owned thread, hash the completed snapshot, and bind all later work to it. A hard link alone cannot freeze bytes if a legacy writer mutates its inode. Recheck source identity before publishing; if it changed return `source_changed` and retain the original/cache entry for its actual digest. No cache entry is relabeled as belonging to newer bytes.

### AnalysisResult

The following field inventory is the proposed source of truth for downstream plans. `id` is 32 hex, matching existing IDs rather than introducing UUID string formatting.

| Field | Proposed type/bounds/meaning |
| --- | --- |
| `schema_version` | Literal `1` |
| `id`, `source` | 32-hex analysis ID; `MusicSourceRef` |
| `audio_sha256` | SHA-256 of the immutable analyzed source bytes, 64 hex |
| `sample_rate_hz`, `channels`, `duration_frames`, `duration_sec` | Integer rate 8,000–192,000, channels 1–2 for this workflow, frames >0; exact `frames/rate`, maximum 7,200 sec initially |
| `analyzer_revision`, `configuration_sha256`, `weights_sha256` | Bounded revision string (1–128), 64-hex config digest, nullable 64-hex weights digest for CPU fallback |
| `tempo` | `status: known|unknown`; nullable BPM 20–400; nullable confidence 0–1; `confidence_method`; `half_double_ambiguous:bool`; `map` maximum 20,000 `{start_sec,bpm}` points |
| `beats_sec`, `downbeats_sec` | Strictly increasing finite arrays in `[0,duration_sec)`; maximum 50,000 points each; downbeats match beat timestamps within one analysis hop |
| `meter` | `status:known|unknown`; nullable `beats_per_bar`:integer 1–32; nullable confidence; method; do not manufacture 4/4 when unknown |
| `key` | `status:known|unknown`; nullable `tonic_pc`:0–11; nullable `mode:major|minor`; nullable confidence; method; maximum 24 typed candidates and `unknown_reason` |
| `key_changes` | Maximum 1,024 local estimates `{start_sec,end_sec,key}` for modulation warnings; empty if insufficient evidence |
| `sections` | Maximum 1,024 ordered non-overlapping `{id,start_sec,end_sec,group,label,musical_label,confidence,bars}` records; `label` generic A/B/C, `musical_label` nullable `intro|verse|chorus|bridge|outro|instrumental`, `bars` nullable integer |
| `loudness` | Existing `AudioMetrics`, no duplicated LUFS definition |
| `vocal_activity` | `status:known|unknown`; `method`; maximum 10,000 ordered non-overlapping `{start_sec,end_sec,confidence}` intervals; unknown means no available vocal evidence, not an instrumental verdict |
| `energy` | At most 4,000 `{time_sec,rms_dbfs,centroid_hz,overlap_band_power}` summary points; features identify their analysis method |
| `warnings` | Bounded enum list (maximum 32): e.g. `silence`, `short_audio`, `beat_grid_uncertain`, `tempo_half_double`, `meter_unknown`, `key_unknown`, `key_changes`, `generic_sections`, `vocal_activity_unavailable`, `channel_disagreement`, `window_boundary_uncertain`, `analysis_partial` |
| `engines` | Typed components with `task`, `name`, `revision`, `configuration_sha256`, nullable `weights_sha256`, `device`, `precision`; at most 16 |
| `license_evidence_ids` | At most 64 bounded evidence identifiers; diagnostics only, do not change source-rights status |
| `created_at`, `override_revision` | UTC timestamp string; integer >=0 |

Cross-field validation is required in Python, even when schema generation cannot encode it: unknown key has null tonic/mode; unknown meter has no bar count; unknown tempo has null BPM; silence cannot contain a confident grid; downbeat/beat relationship and section containment are checked. A heuristic score is not a probability: `confidence_method` names the actual calibration/heuristic; an engine that returns no calibrated confidence uses null and diagnostics. Camelot names are derived display data only when a known major/minor key exists. Do not turn a display wheel into a second musical source of truth.

Manual overrides are a separate revisioned record `{analysis_id,expected_revision,beats_sec?,downbeats_sec?,tempo?,meter?,key?,sections?}` with the same bounds. Keep the raw algorithm result immutable and record which fields came from the user. PATCH is compare-and-swap; conflicting editors receive 409 `analysis_revision_conflict`. Derived plans bind both analysis ID and override revision, so changing a beat grid invalidates their render plan explicitly.

### Analysis API and state

All routes below are proposed and use existing FastAPI request/error conventions.

| Method/path | Request | Response |
| --- | --- | --- |
| `POST /api/music/analysis` | `{source:MusicSourceRef,profile:'music-v1'}`; no arbitrary model/device selection initially | 202 `MusicAnalysisJob`; exact cache hit can return 200 with done job/result |
| `GET /api/music/analysis/{job_id}` | 32-hex job ID | `MusicAnalysisJob` with nullable result |
| `POST /api/music/analysis/{job_id}/cancel` | Empty body | Terminal result after verified drain; persist cancellation intent while a failed drain still owns work; repeated request idempotent |
| `POST /api/music/analysis/{job_id}/retry` | Empty body | New attempt bound to the same retained source/config, after prior worker drained |
| `PATCH /api/music/analysis/{analysis_id}/overrides` | Typed override with expected revision | Effective result plus new revision; stale edit 409 |

`MusicAnalysisJob`: ID, source, status `queued|running|done|failed|cancelled|interrupted`, phase `waiting|snapshot|decode|beats|features|loudness|publishing|complete`, attempt, `cancellation_requested:bool`, nullable progress `{current,total,unit}`, timestamps, nullable stable error code, nullable result. Running work cannot claim a meaningful percentage for an opaque model call. Keep null progress rather than inventing a countdown. New family status adds `interrupted` locally; do not widen global `JobStatus` and break every existing consumer.

Public error mapping: malformed/bounds → 422; missing source/job → 404; source changed/revision conflict/setup busy/coordinator busy → 409; optional runtime/weights missing → 409 with setup action; decode unsupported → 422; resource budget/timeout → failed job with stable code; unsafe paths → 404 or stable forbidden code according to existing endpoint convention. Log internal exception detail only on backend. No file-system paths, checkpoint exception reprs, or stderr dump in public payloads.

### Shared DSP and alignment boundary

Proposed `WarpAnchor`: `{source_sec:float>=0,target_sec:float>=0}`. Order is always **source → target**, unlike the report's ambiguous `(orig_s,new_s)` sketch. Requested output duration is explicit; mapping prepared vocals into the original song means `source=prepared`, `target=original`.

Proposed `DspTransformRequest`:

- `schema_version=1`, `source:MusicSourceRef`, `source_sha256`, `revision:int>=0`.
- `sample_rate_hz`: preserve source or explicit `44100|48000`; `channels`: preserve 1 or 2, with explicit mono-to-stereo conversion only when required by mix layout.
- `anchors`: 2–4,096 monotone anchors, with strictly increasing source and target frames after rounding; anchor endpoints select a bounded source range and target placement. Content outside the selected range is handled by explicit clip placement, never accidental padding/trimming.
- `pitch_shift_semitones`: finite float -12..12 for the report wizard manual range; automatic mashup key shifts are restricted to the smallest interval in -6..6, automatic vocal shifts prefer -2..2, and prepared pitch nudge is -1..1. Conversion-engine pitch control is separately validated by the voice owner. Manual shifts above 2 carry a formant/artifact warning. No unverified claim of formant preservation.
- `min_duration_ratio=0.75`, `max_duration_ratio=1.5` define `Δtarget/Δsource`; never confuse speed ratio with duration ratio. A workflow's `max_stretch=1.25` maps to `[1/1.25,1.25]`, intersected with the global safe band.
- `gain_db`: -12.0412..12.0412; `eq_bands`: maximum 10 typed bands, center 20 Hz to min(20 kHz,0.45*sample_rate), Q 0.1..12, gain -6..6 dB; total automatic EQ boost capped to 6 dB at measured bands.
- `compression`: nullable threshold -60..0 dBFS, ratio 1..4, attack 1..100 ms, release 10..2,000 ms, makeup 0..6 dB; defaults bypass.
- `fades`: fade-in/out duration 0..10 sec bounded by clip length, with explicit `equal_power|linear` curve.
- `loudness: LoudnessSettings` imported from existing contracts; optional reference-loudness match is represented by a validated reference measurement and bounded gain plan, not a new magic string that overloads the existing model.

Proposed `DspMixRequest` contains maximum 32 `MixInput` records, each referencing a retained transform or dry version, start/duration/gain/pan, and an optional typed `DuckSettings`. `DuckSettings`: sidechain input ID, threshold -60..0, ratio 1..8, knee 0..12 dB, attack 1..100 ms, release 20..2,000 ms, max reduction 0..12 dB. No self-sidechain cycle; bound dependency graph and require dry sidechain evidence. Optional IR convolution uses an approved `MusicSourceRef` and wet gain 0..0.5, duration <=10 sec, plus sample/license lineage. De-reverb/de-echo is **not** convolution; the voice plan owns an optional reviewed separator/cleanup model and must keep it unavailable until actually integrated.

`AlignmentResult`: reference and prepared source refs/digests, `schema_version`, alignment revision, status `accepted|needs_review|unavailable`, anchors, at most 1,024 phrase diagnostics `{phrase_id,reference_start_sec,prepared_start_sec,reference_end_sec,prepared_end_sec,mean_cost,onset_error_sec,confidence,reason}`, used feature/matcher versions, warnings. Each phrase has a stable 32-hex ID derived from both source hashes, integer frame bounds and matcher version; changed phrase boundaries create new IDs and invalidate affected review acknowledgements. Confidence nullable until calibrated. Never claim onset accuracy from DTW cost alone. Low-cost periodic chroma alignment can still put the wrong lyric on the wrong bar.

Proposed internal orchestration signatures are intentionally owner-based, not new public worker handles:

```python
# Proposed boundary, not an existing API.
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeAlias

OwnedCommand: TypeAlias = Callable[[list[str], float], Awaitable[bytes]]

# music_dsp.render_transform(request, staging_root, run: OwnedCommand)
#   -> DspTransformResult
# music_dsp.render_mix(request, staging_root, run: OwnedCommand)
#   -> DspMixResult
# music_alignment.align_prepared_vocal(request, staging_root, run: OwnedCommand)
#   -> AlignmentResult
```

Concrete request/result model definitions are implemented in Task 1. Worker invocation is a proposed local protocol: `python music_analysis_worker.py --request <owner-manifest> --result <owner-result>` for tagged `analyze|align` requests and `python music_dsp_worker.py --request <owner-manifest> --result <owner-result>` for tagged `transform|mix` requests. Validate both request/result documents and permit only owner-controlled paths; these are new CLI contracts, not upstream command assumptions. `DspTransformResult` records owner-relative artifact name, SHA-256, exact frames/rate/channels, input/output `AudioMetrics`, applied parameters, warnings, and engine/pin identities. A caller-owned callback uses `video_process.spawn_owned`/bounded output and persists identity before awaiting work. Its returned bytes include bounded command diagnostics/FFmpeg loudnorm stderr as required by `audio_quality.RunCommand`; model/DSP results are atomically written to a private owner-selected result file, then read through their Pydantic model. Do not parse an interleaving of progress logs and JSON as a wire result. The shared service does not acquire an unrelated native lease while its parent already holds one, does not launch an untracked thread, and does not delete caller-owned dry sources.

## Storage, memory, and process decisions

### SQLite cache and publication

Create two narrow tables and one override table in `music_analysis_store.migrate(connection)`:

- `music_analysis_results(id PRIMARY KEY, cache_key UNIQUE NOT NULL, schema_version, source_sha256, configuration_sha256, result_json, created_at)`; bounded JSON validated before write/read. Results immutable.
- `music_analysis_jobs(id PRIMARY KEY, cache_key, source_json, status CHECK(...), attempt CHECK(attempt>=0), revision CHECK(revision>=0), request_json, result_id REFERENCES music_analysis_results(id), worker_identity_json, error_code, created_at, updated_at)`; index `(status,updated_at)` and partial unique index on `cache_key WHERE status IN ('queued','running')`.
- `music_analysis_overrides(analysis_id PRIMARY KEY REFERENCES music_analysis_results(id), revision>=0, overrides_json, updated_at)`; CAS update by expected revision.

Persist source associations in the job/request, not as a single track FK on content-shared result rows. Different tracks with identical content can share analysis only when all engine/configuration/source-role inputs match. Effective results rebind the caller's `source` reference while retaining raw digest identity; do not leak another track's ID/title/path. Production activity evidence cannot be reused across a full mix and vocal stem merely because a request forgot its role. Include role/method/context digest.

The raw analysis cache excludes manual overrides; downstream render/arrangement identity includes the effective analysis ID and override revision. Cache key = SHA-256 of canonical serialized schema version + source bytes SHA-256 + role + analyzer source revision + engine model digest + dependency/runtime recipe digest + selected algorithms/thresholds/hops/window/stitching settings + precision/device when numerically relevant. No timestamps, titles, or paths. Loudness uses its FFmpeg version/method identity. A dependency/preset/analysis setting change creates another cache key, not an in-place mutation.

Claim work in one `BEGIN IMMEDIATE` transaction with unique active cache-key enforcement; catch the specific uniqueness conflict and return the already-owned job. Do not hold SQLite transactions during decode/inference/hash copying. State transition/publish updates use `WHERE id=? AND revision=? AND status='running'`; zero rows means cancellation/another transition won and the result must not resurrect a canceled job.

Result JSON can live in SQLite initially because it is bounded. Scratch features/PCM live only in `DATA_DIR` under an owner-specific staging directory and are never served directly. Rendered files publish by same-filesystem atomic replacement, then a durable receipt, then workflow result commit. Recovery replays publication idempotently by exact hash and owner receipt; it never guesses success from `exists()`.

Avoid an eager cache-cleanup daemon. Provide explicit deletion hooks: source removal first cancels and drains jobs referencing it, then removes source associations; garbage collect only unreferenced completed results/scratch under controlled roots. Retained user-approved manual edits must remain attached to their old immutable analysis when a newer source version appears. Storage caps are constants initially, measured before adding eviction machinery.

### Proposed initial budgets

These are reviewable implementation limits, not measured performance claims. Keep them as backend constants and surface honest stable errors; do not add a settings page for each one.

| Resource | Initial proposal | Failure behavior |
| --- | --- | --- |
| Input | <=7,200 sec, 1–2 channels, 8–192 kHz, supported local formats | Reject unsupported processing; retain original upload/version |
| Full-rate decoded PCM | Preflight `frames*channels*4`; <=8 GiB per job plus disk reserve | `decode_budget_exceeded` before expensive processing; do not truncate at file limit and call it complete |
| Disk reserve | Estimated scratch + outputs + 1 GiB headroom, recalc after metadata probe | `insufficient_disk`; clean only proven-owned partials |
| Analysis audio | 22,050 Hz float32 windows <=120 sec with 10 sec context overlap | Bounded waveform buffers; document overlap stitching confidence |
| Feature storage | Disk-backed summaries with explicit shape limits; rolling chunk processing | `analysis_budget_exceeded`, partial result only with per-component unknown flags |
| Analysis concurrency | One heavyweight analysis worker initially; no concurrent GPU engine by default | Queue with actual waiting reason; admission avoids deadlock |
| Analysis deadline | Decode 180 sec per bounded window; whole job <=3,600 sec initially | terminate descendants, await drain, `analysis_timeout` |
| Worker JSON | <=8 MiB private result manifest; <=64 KiB retained native-worker diagnostic capture; existing FFmpeg measurement callback keeps its bounded 1 MiB capture; PCM never in stdout | `worker_output_limit`; drain/kill owner before cleanup |
| DTW phrase | Features at 10–20 ms; <=2,000 frames on either axis and <=4,000,000 cells | Split by stable phrases/user anchors; if no safe split require manual anchors |
| DTW memory | Reserve for cost + accumulated cost + step arrays + features; conservative 128 MiB per max-size phrase | One phrase at a time; reject before allocation |
| DSP buffers | <=30 sec active full-rate stereo blocks plus native algorithm state; temporary segment output files | Keep tails/context bounded; reject full-file native calls above measured allocation cap |
| Output | Explicit expected frame count; <=7,200 sec initial workflow limit | `output_duration_invalid`, preserve dry source |

A 120 sec mono 22,050 Hz float32 analysis window is about 10.6 MB before model/features. This arithmetic is an allocation estimate, not peak-RSS evidence. A two-hour 48 kHz stereo float32 decode is about 2.76 GB, so loading the entire decode several times is unacceptable even on a large Mac. Use streaming/windowed decode and local segment staging. CPU deadlines and memory must be profiled on actual hardware before they become advertised capabilities.

`audio_quality.measure` currently makes its own native-rate raw measurement file and hash pass; avoid overlapping it with another whole-source decode. Reuse its result within the same owner/source digest, execute sequentially, and clean its known scratch only after verified process drain. If its decode limit would be reached, reject using probed frames/bytes; a size-capped partial read is not a valid whole-track measurement. Do not refactor this mature path during foundation work unless a regression test demonstrates a required fix.

## Ordered implementation tasks

Each checkbox is one bounded action or one small reviewable patch. For numerical tasks, several individual checks may take longer than five minutes; stop at the named evidence checkpoint rather than claiming an entire MIR implementation is a five-minute edit. No commit commands are included because commits are not authorized.

### Task 1: Freeze source, analysis, and DSP contracts

**Files:** Create `music_analysis_contracts.py`, `music_dsp_contracts.py`, their `_test.py` files; modify `backend/scripts/generate_contracts.py`; generate `frontend/src/api/generated.ts`.

- [ ] Re-read the existing `Contract`, `AudioMetrics`, `LoudnessSettings`, source asset ID, and contract generator supported-schema set; use simple discriminated models/enums supported by that generator.
- [ ] Write contract tests first: NaN/infinity, booleans as IDs, negative/over-limit durations, unknown key with non-null tonic, unknown tempo with fake BPM, unordered/duplicate beats, downbeats outside beats, overlapping sections, >2 channels, oversized arrays, and extra user pathname fields must be rejected.
- [ ] Run `python -m unittest app.music_analysis_contracts_test app.music_dsp_contracts_test -v` in the isolated environment described below. Expected before implementation: failing imports/model-validation assertions, with no file mutation outside the test directory.
- [ ] Define the three `MusicSourceRef` members with strict IDs, source digests, `extra='forbid'`, and a required discriminator. Export request/result/override/job models as `MUSIC_ANALYSIS_CLIENT_MODELS`.
- [ ] Define unknown-aware tempo/meter/key/activity models from the contract table. Keep uncertainty separate from missing installation and from a worker exception.
- [ ] Define `WarpAnchor`, bounded transform/mix/ducking models, exact output metadata, alignment diagnostics, and `MUSIC_DSP_CLIENT_MODELS`; all cross-field invariants use typed validators.
- [ ] Add this concrete monotone-frame validation rule to `music_dsp_contracts.py`, adapting only the module's declared model names. It is deliberately independent of a third-party stretch API:

```python
from collections.abc import Sequence
import math


def checked_anchor_frames(
    anchors: Sequence[tuple[float, float]],
    sample_rate: int,
    *,
    min_ratio: float = 0.75,
    max_ratio: float = 1.5,
) -> list[tuple[int, int]]:
    if not 8000 <= sample_rate <= 192000 or isinstance(sample_rate, bool):
        raise ValueError('invalid_sample_rate')
    if not 2 <= len(anchors) <= 4096:
        raise ValueError('invalid_anchor_count')
    if not 0 < min_ratio <= 1 <= max_ratio or max_ratio > 1.5 or min_ratio < 0.75:
        raise ValueError('invalid_stretch_bounds')
    frames: list[tuple[int, int]] = []
    for source_sec, target_sec in anchors:
        if not math.isfinite(source_sec) or not math.isfinite(target_sec):
            raise ValueError('invalid_anchor')
        if source_sec < 0 or target_sec < 0:
            raise ValueError('invalid_anchor')
        pair = (round(source_sec * sample_rate), round(target_sec * sample_rate))
        if frames:
            source_delta = pair[0] - frames[-1][0]
            target_delta = pair[1] - frames[-1][1]
            if source_delta <= 0 or target_delta <= 0:
                raise ValueError('non_monotone_anchors')
            ratio = target_delta / source_delta
            if not min_ratio <= ratio <= max_ratio:
                raise ValueError('stretch_ratio_exceeded')
        frames.append(pair)
    return frames
```

- [ ] Add exact contract assertions: `[(0,0),(1,1),(2,2)]` at 48 kHz yields `(0,0),(48000,48000),(96000,96000)`; `[(0,0),(1,1.6)]` raises `stretch_ratio_exceeded`; `[(0,0),(0.000001,0.000001)]` raises after sample rounding; negative, NaN, repeated source, and reversed target are rejected. Validate selected source/output range separately using request/source metadata.
- [ ] Add generator registry imports, regenerate with `python backend/scripts/generate_contracts.py`, then `--check`. Do not hand-edit the generated artifact.
- [ ] Run the two contract modules, existing `app.contract_generation_test`, and frontend contract/schema validation tests. Expected after implementation: pass; runtime decoder preserves nulls and rejects wrong discriminators.
- [ ] Review with voice/mashup/beat callers: field names, source→target convention, ratio definition, revision semantics, key unknown behavior, and existing loudness reuse are frozen before implementing clients.

### Task 2: Add additive persistence and atomic cache claims

**Files:** Create `backend/app/music_analysis_store.py`, `music_analysis_store_test.py`; modify `backend/app/db.py` migration registration.

- [ ] Create a temporary pre-feature catalog containing tracks, original/voice versions, old generation history, and retired historical origin records; record row counts and source bytes.
- [ ] Write migration tests: applying `migrate` twice preserves every old row/column value; foreign-key checks remain valid; malformed digest/status/revision constraints fail.
- [ ] Write a concurrent claim test with two independent SQLite connections and identical cache key: exactly one active job exists and both callers receive its ID.
- [ ] Write cancellation-vs-publish CAS regression: after terminal cancellation and revision increment, an older running revision cannot publish/reopen the job.
- [ ] Run only `app.music_analysis_store_test`; expected failure before implementation identifies missing schema/CAS functions.
- [ ] Implement `migrate`, independent connection/transaction helpers following `music_queue_store`/`artist_production.transaction`; validate JSON bodies as typed models on both write and read.
- [ ] Implement proposed store functions `claim`, `get_job`, `set_worker_identity`, `transition`, `publish_result`, `put_override`, `list_recoverable`, and `detach_source`. Each transition checks attempt/revision; no string interpolation of client IDs into SQL.
- [ ] Ensure a cached result reused by another track contains only that request's source ref and shared byte-derived fields. Add a regression asserting the earlier track's ID/title/path is absent.
- [ ] Add cache invalidation tests for changed bytes, model digest, dependency recipe, thresholds/hop, source role, and FFmpeg measurement method/version; separately assert that override changes invalidate dependent plans/previews without rerunning unchanged raw analysis.
- [ ] Add tests for malformed/oversized stored JSON, missing source after queued request, foreign-key transaction failure, and duplicate recovery.
- [ ] Register the migration in `db._open_db` and close/clear partly initialized connection on failure using the existing pattern. No live DB is opened during plan writing or fixture generation.
- [ ] Run store tests again; expected pass. Inspect transaction scopes: no awaits, file copies, hashing, model calls, or nested event-loop locks inside SQL transactions.

### Task 3: Resolve and snapshot source bytes safely

**Files:** Create initial `backend/app/music_analysis.py` owner/source functions and `music_analysis_test.py`; modify deletion guard call sites only as required.

- [ ] Write tests using temporary version/stem/production-asset paths for traversal, absolute-path JSON, symlink escape, missing file, unsupported suffix/probe metadata, mismatched digest, and source changed during snapshot.
- [ ] Write a race test in which track deletion/stem replacement begins during snapshot; it must wait for cancellation and confirmed worker/thread drain before removing its files.
- [ ] Write a byte-preservation regression that hashes the source before/after successful, failed, and canceled analysis.
- [ ] Run the focused test module and capture the expected failing boundary assertions.
- [ ] Implement resolution using the existing version/stem/asset resolvers. Constrain every resolved input/scratch/output to the correct library/asset root and reject symlink escapes. Asset authorization/evidence checks remain in the owning production workflow.
- [ ] Implement a typed snapshot metadata helper using `run_owned_thread`, local copy, digest, and controlled temporary file publication. Do not treat existing `audio_path` text as automatically trusted.
- [ ] Probe exact frames/rate/channels with bounded ffprobe output; validate declared duration against actual decode count later. Reject >2 channels explicitly rather than silently taking the first two.
- [ ] Preflight full-rate decoded byte estimate/disk reserve before decoding. Test malformed duration, huge frame counts, integer overflow assumptions, no audio stream, multiple stream selection, and zero samples.
- [ ] Add active source associations to the existing deletion/stem replacement protection path. A completed analysis cache is not an active source lock; an in-flight scratch reader is.
- [ ] Test production asset mapping without fabricating a track ID or bypassing source lineage. A later render receives the same source digest.
- [ ] Re-run source tests and relevant `app.audio_versions_test`/`app.audio_exports_test`; expected pass with unchanged originals.

### Task 4: Pin an optional offline analysis/runtime environment

**Files:** Create environment/setup modules and proposed asset manifests; modify module catalog/contracts/installer/evidence/license dispatch; tests in environment test module and existing module test suites.

- [ ] Read selected immutable upstream source revisions for `beat_this` and `python-stretch`, package metadata, source/license files, and actual artifact availability for each supported Python/platform combination. Record evidence URLs, observed date, revision, code terms, weight terms, and restrictions separately. Do not insert invented version numbers/checksums into the manifest.
- [ ] Produce an exact environment lock after dependency resolution in an isolated development directory during execution, not in the user's active engine. Pin versions/hashes based on real resolvable artifacts. The supplied plan intentionally does not pretend a successful resolution already happened.
- [ ] Write missing-checkpoint and tampered-checkpoint tests: preflight fails before importing/loading an inference model; no network call is attempted.
- [ ] Write a network-denied runtime test by patching `socket.create_connection`, `urllib.request.urlopen`, `torch.hub.load_state_dict_from_url`, and the relevant package downloader boundary to raise. Runtime with supplied local weights succeeds using a fake model; absent local weights returns `analysis_weights_missing` without invoking any patched function.
- [ ] Inspect checkpoint loading at the pinned revision: the observed upstream loader falls back to HTTP on missing filenames. Require a verified local regular file, verify its digest, stage it under owner-protected runtime storage, and make loader network fallback unreachable. A preflight alone is insufficient if the file can disappear between check/load.
- [ ] Implement the narrow local loading adapter based on the inspected pinned source and safe deserialization support. Do not execute arbitrary user pickles; the initial path accepts only explicitly reviewed pinned checkpoints. Avoid `torch.hub.load` for inference.
- [ ] Declare proposed module ID `music_analysis`; update the existing ModuleId/catalog maps/actions and setup dispatch consistently. Setup without model downloads installs dependencies only; model acquisition remains an explicit installer step with byte count/digest/license evidence. The runtime never installs packages or downloads weights.
- [ ] Keep `dbn=False`; do not pull madmom models into the default environment. Do not install allin1/SongFormer/MuQ by transitive accident. Fail a lock audit when default dependencies include an unreviewed model downloader.
- [ ] Install CPU support first. Expose `device='cpu'`, `precision='float32'` in result identity. CUDA/MPS are unavailable in this initial capability receipt until measured platform evidence exists; a PyTorch device existing is not proof that this model's operations work.
- [ ] Inspect `python-stretch` wheel availability and native-library notices for macOS arm64, Linux x64, Windows x64; record unsupported combinations truthfully. If a platform needs source compilation, use the existing setup toolchain checks and a pinned source recipe; never compile on first render.
- [ ] Implement package/environment fingerprint and reviewed weight digest checks using existing module evidence patterns. Invalidated environment → clear setup action, no silent dependency refresh.
- [ ] Update license records with publisher declarations and source evidence. Keep user-source permission and model-training provenance independent; do not label the output legally cleared merely because beat tracking is MIT.
- [ ] Add module/setup tests for source pin mismatch, offline missing artifact, disk failure, cancel during setup, unverified receipt, unexpected network access, and retired-engine absence.
- [ ] Regenerate module contracts; run focused module/environment tests and strict types for modified dispatch maps. Expected pass with mocked install/download boundaries; native package install proof remains a separate platform task.

Primary-source evidence read for this plan: [beat_this inference source](https://github.com/CPJKU/beat_this/blob/main/beat_this/inference.py) shows the download fallback and inference entry points; [beat_this postprocessor](https://raw.githubusercontent.com/CPJKU/beat_this/main/beat_this/model/postprocessor.py) must be used to confirm tuple ordering. [python-stretch upstream](https://github.com/gregogiudici/python-stretch) documents a native Signalsmith binding. These are moving sources, not the final install lock. Re-read and pin before implementation.

### Task 5: Build bounded decode/features with conservative key and sections

**Files:** Create `backend/app/music_analysis_features.py`, `music_analysis_features_test.py`, initial `music_analysis_worker.py`, `music_analysis_worker_test.py`, fixture README.

- [ ] Add stdlib-generated fixture builders for silence, impulse clicks, amplitude envelopes, steady triads, detuned triads, and independent stereo channels. Never download music or use production libraries in unit tests.
- [ ] Write finite shape/channel tests for zero-length, sub-hop, 100 ms, 2 sec, 3 min, malformed byte boundaries, NaN/inf float PCM, truncated files, odd sample rates, and antiphase stereo.
- [ ] Write known evidence tests: silence returns null key/tempo and no vocal claim; an A-minor triad family is not forced to C major solely because notes overlap; a repeated noise loop cannot receive a high-confidence tonal key.
- [ ] Run the feature/worker test modules; expected failures identify missing helper and protocol implementation.
- [ ] Implement controlled ffmpeg local-input decode via caller-owned supervisor. Use the existing protocol/container allowlist, `-nostdin`, exact selected audio stream, bounded scratch filenames, and no shell command strings. FFmpeg output duration/sample count must agree with validated metadata within a documented codec tolerance; a truncated decode cannot silently pass.
- [ ] Stream full-quality source measurement separately; create 22,050 Hz float32 analysis windows with context. Preserve both channels for power/spectral summaries. Do not average antiphase stereo into silence.
- [ ] For per-channel chroma/onset features, combine magnitudes/power after per-channel transforms. For a mono-only model, choose the higher-energy channel or run both channels when material disagreement is detected; persist the policy and `channel_disagreement` warning. Do not replace waveform samples with absolute values, which changes harmonic content.
- [ ] Extract bounded onset envelope, RMS/energy, spectral centroid/overlap-band summary, chroma, and MFCC blocks with explicit hop/sample-rate/padding choices. Record source frame/time offsets including resampler latency; test exact onset offsets on impulse fixtures.
- [ ] Implement the initial key estimator from documented major/minor profile correlation with mean-centered/normalized chroma and a top-two margin; define versioned profiles and thresholds in code. Reject silence, low tonal energy, tied/unstable candidates, and detuning outside documented 12-tone support. Record candidates/method; do not call the score calibrated confidence until evaluated.
- [ ] Add sliding local key estimates only where windows have sufficient activity; emit modulation diagnostics when stable local regions disagree. A single global tonic must not automatically shift a modulating vocal.
- [ ] Implement weight-free generic section boundaries from bounded beat-synchronous feature novelty and recurrence. Cap the coarse representation at 4,000 frames/bars before any all-pairs matrix. Compute features incrementally; do not allocate whole-track dense DTW/recurrence at 10 ms resolution.
- [ ] Label repeated groups A/B/C with stable deterministic IDs. A repeated high-energy group may be a `chorus_candidate` diagnostic; leave `musical_label=null` until user review or a separately enabled, verified label engine supplies evidence.
- [ ] Compute vocal activity only from a supplied vocal stem/guide source using an explicit energy/onset gating method. Full mix analysis returns unknown activity unless separation has been requested and completed by the owner. A music/noise gate is not a vocal detector.
- [ ] Add synthetic section fixtures with known timbre/energy changes and repeated regions; require boundary tolerance tied to feature hop, deterministic grouping, no claimed verse/chorus semantics, and stable treatment of rubato/odd meters.
- [ ] Add worker result validation, maximum output size, finite checks, and safe error enum. Write the complete validated result to an owner-selected private file by atomic replacement; stdout contains only a bounded completion receipt `{schema_version:1,result_sha256}`. Keep progress/logs in a bounded owner log. Parent independently validates the manifest digest/schema/source identity; neither a success exit code nor receipt alone establishes a valid result.
- [ ] Run feature/worker tests; expected pass. Inspect that frontend/backend application startup does not import librosa/PyTorch/native stretch from optional worker-only modules.

### Task 6: Integrate beat/downbeat inference without hidden network or invented confidence

**Files:** Complete analysis worker/environment/feature adapters; extend worker/features/environment tests.

- [ ] Read the pinned `beat_this` entry point and postprocessor signatures, return ordering, supported audio shape, sample rate, and precision; write a fake adapter test that distinguishes beats from downbeats so a tuple-order mistake fails visibly.
- [ ] Write a missing local checkpoint regression with network disabled. It must report `analysis_weights_missing`, not download `final0` or return guessed regular beats.
- [ ] Implement model loading through the reviewed local adapter, minimal postprocessor, CPU float32, `torch.inference_mode`, and explicit runtime fingerprint.
- [ ] Decode/process at most 120 sec per analysis call with 10 sec context overlap; retain central-window events and merge overlapping detections within one documented hop. Preserve absolute frame offsets.
- [ ] Add a boundary-crossing click-grid fixture with gradual tempo drift and a downbeat near a window boundary. Duplicates are merged, missing/conflicting beats downgrade the segment, and monotonic output is preserved.
- [ ] Test alternating stereo-dominant percussion and antiphase tracks; the channel policy from Task 5 must retain musical evidence without changing audible PCM.
- [ ] Derive tempo map from interbeat intervals using bounded robust smoothing. Identify half/double ambiguity rather than silently selecting the visually convenient value.
- [ ] Infer meter only when consecutive downbeat spans support a stable count with sufficient evidence. Support irregular/mixed meter as unknown or local metadata; do not fill every source with 4/4.
- [ ] Keep uncalibrated per-beat model outputs distinct from confidence. Return score/method/null confidence with boundary diagnostics until held-out calibration exists.
- [ ] Add fallback behavior when dependencies/weights are unavailable: callers can choose manual tempo/grid or explicitly request CPU heuristic estimates; mark those as estimated and prevent automatic bar-locked mashup approval. No silent substitution of video overview markers.
- [ ] Run mocked inference tests; expected pass with zero weight downloads. A later opt-in native proof must run the exact pinned model against a licensed short local fixture and compare annotated beats/downbeats.
- [ ] Record CPU wall time, peak RSS, threads, fixture duration, model fingerprint, and boundary errors on target hardware before claiming realtime, MPS support, or maximum-length support.

### Task 7: Own durable analysis from submission through recovery

**Files:** Complete `music_analysis.py`, `music_analysis_test.py`; modify lifecycle/admission/busy/deletion hooks.

- [ ] Write tests for queued cancellation, cancel during snapshot/decode/model/loudness/publish, repeated cancellation, shutdown during work, frontend disconnect, and restart with a running persisted row.
- [ ] Write a fake worker that spawns a sleeping child. Cancellation must prove both parent and descendant exit before scratch deletion and resource release; if identity cannot be verified, retain quarantine/ownership rather than claiming idle.
- [ ] Write a cache-hit test asserting no new process/model call, and a concurrent client test asserting one job with independent polling.
- [ ] Run the owner test module; expected failure before lifecycle integration.
- [ ] Implement `start`, `get`, `cancel`, `retry`, `work_busy`, `recover`, `shutdown` with a strong task registry and per-job ownership. Publish the registry/claim while holding admission protection; no await gap between passing the idle check and registering work.
- [ ] Reserve native admission before source work can conflict with setup/coordinator transitions; use the existing coordinator capability for inherited children. Do not take `reserve_idle_native()` recursively from inside an already admitted parent. Document lock acquisition order and test it.
- [ ] Heavy CPU-only analysis initially participates in native/busy accounting so it cannot overcommit unified memory beside model generation. It does not acquire `stems.gpu_lock` merely because it is expensive. A later verified GPU path requires both admission and existing GPU lease with the proper owner label.
- [ ] Add a bounded FIFO analysis queue with capacity 32 (proposed), one running worker, and explicit `analysis_queue_full` refusal. Persist queued jobs before return. Avoid a new generic scheduler abstraction; this owner manages only analysis.
- [ ] Run loudness measurement through the same owner using `audio_quality.measure`; reuse measurement for identical snapshot bytes within that run. A short/silent clip retains null loudness and warning, never NaN or fabricated -70 LUFS.
- [ ] Validate entire result before store publication and CAS `running → done`. Check cancellation immediately before atomic result commit. Do not install a result on a canceled attempt.
- [ ] On restart, verify persisted token/receipt identity before terminating a matching worker. Mark old nonterminal work `interrupted`; keep the source snapshot and permit explicit retry. Queued jobs may be requeued only after source/config/runtime identity and resource admission are revalidated.
- [ ] Add startup recovery before accepting API jobs and shutdown drain in `main.lifespan`'s existing unconditional cleanup. A failed later startup still drains analysis.
- [ ] Add analysis to `work_busy` and relevant admission checks using local imports if required to avoid existing import cycles. Verify no deadlock where a job blocks its own inherited coordinator context.
- [ ] Re-run owner, resource admission, music queue ownership, lifecycle, and source-removal test modules. Expected pass; injected failed drains leave the system busy/quarantined until resolved.

### Task 8: Align prepared vocals using phrase evidence and bounded DTW

**Files:** Create `backend/app/music_alignment.py`, `music_alignment_test.py`; extend DSP contracts only if a validated field is missing.

- [ ] Build generated guide/prepared fixtures with known onset offsets, piecewise tempo drift, breaths/silence, clipped phrases, repeated words/notes, and mismatched phrase count. Store expected phrase/frame correspondences in the fixture builder, not by inspecting algorithm output.
- [ ] Write tests for identity mapping, a prepared take 10% slower, leading 1 sec silence, one missing phrase, one swapped repeated phrase, a 20 sec held note, empty activity, >safe stretch, and user-edited anchors collapsing to one frame.
- [ ] Run `python -m unittest app.music_alignment_test -v`; expected failing tests before implementation.
- [ ] Implement activity segmentation with minimum phrase activity/silence durations recorded in algorithm version; create coarse phrase-onset sequences for both sources. Keep unmatched phrases explicit rather than stretching them across unrelated material.
- [ ] Match phrase sequences with a monotone edit-distance/dynamic program allowing unmatched phrases at a stated penalty. Preserve lyric order; use supplied/manual phrase anchors when repeated sequences are ambiguous.
- [ ] For each matched phrase, compute normalized chroma + MFCC + onset features at 20 ms initially. Standardize feature groups so 20 MFCC dimensions do not overwhelm a single onset term; weights/version are fixed and hashed.
- [ ] Apply bounded DTW with global band only inside matched phrases. Preflight `n_frames*m_frames <=4_000_000` and each axis <=2,000; if phrase is longer, split at stable paired silence or manual anchor. If no valid split exists, return `needs_review` instead of allocating an entire song matrix.
- [ ] Use the pinned [librosa DTW API](https://librosa.org/doc/0.11.0/generated/librosa.sequence.dtw.html) with explicit feature cost, `global_constraints=True`, selected band radius, and path backtracking. The band limits valid alignments, not dense allocation. Validate the pinned implementation's actual peak memory and path ordering.
- [ ] Reverse the path if returned in reverse time order, collapse horizontal/vertical repeats into representative strictly increasing pairs, and derive phrase-onset/interior anchors. Convert once to frames and validate source→target monotonicity plus duration ratios.
- [ ] Do not force first/last source frames to song boundaries when the prepared vocal omits an intro/tail. Preserve explicit offsets and silence placement. Only paired active phrases should determine the warp.
- [ ] Reject or request review for impossible slope, large unmatched regions, ambiguous repeated phrases, low tonal/onset evidence, or conflicting manual anchors. Do not repair a bad path by sorting target times independently or clipping ratios while silently changing correspondence.
- [ ] Calculate diagnostics from held-out feature agreement, matched-phrase coverage, slope distribution, and onset differences. Keep `confidence=null` until calibrated; `accepted` requires stated evidence thresholds plus no structural warning.
- [ ] Return a revisioned alignment object containing both source digests and feature/matcher version. Any new source bytes/manual anchors/config change invalidates the prior alignment.
- [ ] Test deterministic output, conservative failure, matrix-budget refusal before allocation, no out-of-range target times, and retained source bytes on exceptions.
- [ ] Run alignment tests and contract tests. Expected pass on synthetic timing fixtures; synthetic success does not establish the report's held-out 90%/±30 ms target.

### Task 9: Render stretch/pitch without stereo drift or sample loss

**Files:** Create `backend/app/music_dsp.py`, `music_dsp_worker.py`, `music_dsp_test.py`; integrate native package through Task 4's isolated runtime.

- [ ] Read the pinned `python-stretch` binding source for channel-major vs frame-major layout, `timeFactor` direction, output length, algorithm latency, padding/flush behavior, reset/reuse behavior, and supported dtype. The upstream README alone is insufficient evidence for boundary-accurate rendering.
- [ ] Write native-adapter tests with an impulse, 440 Hz tone, stereo-identical tone, antiphase stereo, and left-only impulses at known frames. Store expected duration/pitch/channel relationships independently of the adapter implementation.
- [ ] Write identity tests requiring a bypass for zero pitch plus identity warp: exact unchanged PCM when no other DSP requested. A zero-change request should not add a spectral processor's latency or coloration.
- [ ] Write tests for duration ratio 0.75, 1, 1.5 and pitch shifts +1/-1, +6/-6, +12/-12 semitones: expected frame count is derived from target anchors; sinusoid frequency is `440*2**(shift/12)` within a documented spectral-estimation tolerance.
- [ ] Run focused tests with a fake native engine first; expected missing adapter behavior, no implicit download/build.
- [ ] Implement a narrow typed native adapter based on the pinned package. Create an accurate `.pyi` declaration only if inspected shipped typing is incomplete; declare the real signatures, not `Any`, casts, or fake successful stubs.
- [ ] Render each selected segment with bounded context/lookbehind/tail. Compensate measured engine latency once, preserve shared stereo timing, and write incremental staging output. Do not instantiate independent stretch processors per channel.
- [ ] For piecewise anchors, derive exact target intervals in frames and apply local duration ratio. Use short constant-sum crossfades for correlated adjacent copies; equal-power is optional for unrelated material. Crossfades consume overlap, so account for it rather than lengthening/shortening the finished render.
- [ ] Keep anchors/endpoints fixed after crossfade; write exactly the requested output frames, with at most the documented algorithm tail outside the clip stored separately if requested. Do not truncate audible endings to hide unexplained engine latency.
- [ ] Reject output with NaN/inf, unexpected channels/rate/length, missing signal for non-silent input, or a ratio unsupported by the pinned binding. Do not silently fall back to Rubber Band, SoundTouch, or a different pitch algorithm.
- [ ] Store transform parameters, engine/pin/configuration digest, source digest, output digest, exact frames/rate, and warnings in `DspTransformResult`.
- [ ] Run real native adapter tests only in the explicitly prepared temporary runtime. Document any unsupported platform without making the backend-wide suite download/compile native dependencies.
- [ ] Compare waveform joins and stereo correlation on fixture boundaries; measure output frame error and peak discontinuities against dry boundary context. A subjective review remains required for singing/formants/transients.
- [ ] Run cancel-mid-segment and crash-after-temp-write tests through the caller-owned supervisor; only drained workers permit partial-file deletion. Dry sources remain hash-identical.

### Task 10: Share measured level/EQ/compression/ducking/fades and final output checks

**Files:** Complete DSP worker/service and tests; reuse `audio_quality.py`; extend `audio_export_quality_test.py` only for a demonstrated shared behavior change.

- [ ] Write level-match fixtures: reference -18 LUFS vocal, quieter prepared vocal, silent reference, short reference, excessive requested gain, and loud true-peak-limited reference. Expected match is measured ±1 LU where attainable; silence/short indeterminate targets require user review.
- [ ] Implement reference measurement with `audio_quality.measure`; compute bounded vocal gain from integrated LUFS only when finite. Keep the legacy ±12.0412 dB maximum boost/cut unless the workflow explicitly revises its contract. If cap prevents target attainment, return a warning and measured difference.
- [ ] Separate vocal-to-reference gain from final master normalization. Apply the former before mixing; apply existing measured loudnorm to the final master only when requested, then remeasure actual output. A limiter can lower LUFS, so never claim both targets met from input math.
- [ ] Write biquad filter tests for bypass identity, expected center-frequency attenuation/boost, bounded coefficient stability across supported rates/Q, no DC blowup, block-size equivalence, and preserved stereo relations.
- [ ] Implement peaking/shelf filters from standard documented DSP equations in own code; use float64 coefficient/state calculations and preserve filter state across blocks. Automatic spectral matching uses robust 1/3-octave summaries, ±6 dB cap, smoothed low-order fit; no copied Matchering/pedalboard code.
- [ ] Write compressor/duck tests with known bursts: max reduction respected, bounded attack/release transition, no gain in silence, no pumping restart at chunk boundaries, deterministic output, and no clipping caused by makeup gain. Test detector energy across channels rather than cancellation-prone stereo averaging.
- [ ] Implement one linked-channel envelope and standard soft-knee gain computer with bounded attack/release coefficients. Preserve detector/filter state across blocks and reset at explicit transport/session boundaries only.
- [ ] Implement ducking using the designated retained sidechain, aligned in exact timeline frames; silence in the sidechain releases gain smoothly. Reject dependency cycles and sidechain output from a different source revision.
- [ ] Add fade tests for 0, 1-frame, overlapping fades, and correlated/unrelated transitions; ensure endpoint coefficients and exact output lengths. Implement defined curves, not CSS easing approximations.
- [ ] Optional owned/CC0 IR reverb: bound IR duration/channel count/wet gain, convolve in bounded blocks with overlap-add, retain IR source hash/license lineage, and include declared tail length. Add impulse-response and cancel/tail tests. Keep this disabled by default until these checks pass.
- [ ] Implement residual/accompaniment correlation as a **bleed-risk screening diagnostic** only. Test known leaked vocal/percussion fixtures plus a clean but rhythmically correlated negative case; avoid presenting correlation as proof the original singer is absent/present.
- [ ] For final master, use the existing `LoudnessSettings` music default -14 LUFS/-1 dBTP when selected; user-matching mode uses source measurement and bounded policy. Verify true peak after PCM render and again after lossy export, since encoding can create intersample peaks.
- [ ] Add intersample-peak fixture from the existing quality test; sample-peak limiter success alone must not satisfy a true-peak claim. If selected gain/ceiling cannot be met, emit measured target warning rather than increasing loudness indefinitely.
- [ ] Run shared DSP/quality tests; expected pass. Listening tests assess artifacts/pumping/space independently of numeric target success.

### Task 11: Expose analysis clients and integrate workflow previews

**Files:** Create analysis routes/frontend API/tests; modify main and generator registration; workflow-specific screens/endpoints are owned by sibling plans.

- [ ] Write backend route tests for each proposed endpoint and mapping: unknown IDs, extra JSON fields, forbidden paths, malformed source unions, conflicting overrides, canceled job retry, optional runtime missing, cross-origin mutation, chunked oversized request bodies, and stable safe error messages.
- [ ] Implement routes as thin calls into the owner; no filesystem/business logic in route handlers and no inference in the request task. Follow `routes_music_queue.py:28` local-origin validation for mutation routes and stable validation/storage errors; cap analysis request bodies at 128 KiB and override bodies at 2 MiB while reading, even without Content-Length. Preserve loopback binding/CORS defaults.
- [ ] Write frontend tests parsing generated contracts, unknown key/null metrics, estimated meter/grid, malformed nested arrays, worker error states, AbortSignal, stale polling generation, navigation teardown, and retry replacement.
- [ ] Implement `frontend/src/api/musicAnalysis.ts` with the existing fetch/error/decoder conventions. Parse responses into `unknown` then generated decoder; no `as AnalysisResult` assertions.
- [ ] Regenerate contracts/decoders and run frontend API tests before connecting screens.
- [ ] Give voice/mashup/beat workflow owners the shared analysis IDs/revisions and typed DSP requests; they persist their own immutable transform plan and source lineage.
- [ ] Preview and export must reference the same transform revision and backend artifact. Do not preview one algorithm in Web Audio and export another in Signalsmith while displaying the same state. Existing general editor SoundTouch behavior remains intact until an explicit editor integration patch changes it.
- [ ] Manual anchor/key/grid changes create a new revision and invalidate old preview availability. A late response cannot promote an artifact from a prior revision; caller generation tokens/AbortControllers guard UI updates.
- [ ] Use existing controls, i18n, keyboard/labels, progress, error, and A/B playback components. Low-confidence regions are navigable by keyboard and have text reasons; color alone cannot encode acceptance.
- [ ] Opening a transformed source in the editor uses a retained transformed audio file at normal playback rate, with dry original, transform metadata, anchors, and source references retained for later rerender. Avoid applying browser warp a second time.
- [ ] Run routes/frontend client tests, affected screen tests in sibling plans, and manual reload/navigation/cancel checks in a temporary library.

### Task 12: Produce measurements, held-out evaluation, and acceptance evidence

**Files:** Create `docs/music-analysis-dsp.md`, fixture README; extend CI; evaluation manifests/results follow the parent evaluation plan outside committed user media.

- [ ] Add all new app-owned contract/store/owner/feature/alignment/DSP/setup/route modules to the existing strict mypy platform loop; add a truthful native declaration only where inspected source justifies it.
- [ ] Keep default CI tests fake-model/synthetic and offline. Add opt-in native proof commands separately; absence of optional runtime can skip only explicitly tagged native proof, never contract/algorithm/lifecycle tests.
- [ ] Build a rights-reviewed held-out set of at least 10 varied songs/takes for the report's vocal-alignment target: rubato, dense backing, silence, repeated phrases, odd meter, stereo phase, key changes, different sample rates, and differing duration/structure. Keep evaluation audio separate from any training dataset.
- [ ] Have a human annotate phrase onsets/beats/downbeats before evaluation, record annotator agreement and offsets, freeze manifests/hashes, and use a pinned evaluation dependency after license review. Do not tune thresholds on the held-out set.
- [ ] Measure phrase onset precision/recall and matched-phrase onset absolute error. Report denominator, unmatched phrases, failures/manual-review rate, and the fraction within ±30 ms. The proposed ≥90% target applies to all relevant annotated phrases under a declared protocol; dropping difficult failures would be benchmark laundering.
- [ ] Measure voice LUFS error and final true peak on actual rendered/exported artifacts; record silence/short indeterminate cases separately. Numeric success is not listening quality.
- [ ] Record beat/downbeat matching tolerance/metrics, key confusion/unknown rates including detuned/non-Western fixtures, section-boundary tolerance, meter handling, and compatibility with human grid overrides.
- [ ] Collect a blind five-listener A/B review under the parent evaluation plan; retain listener counts, set selection, acceptable/better denominator, and artifact notes. Do not claim the report's 7/10 quality target before data exists.
- [ ] Benchmark exact target hardware/runtime: Mac M4 Max CPU path first; only test MPS if adapter/runtime supports it and it is explicitly selected for verification. Record cold/warm setup/inference, RTF, peak RSS, disk IO/temp usage, cancellation latency, output duration error, and energy where available. Never infer MPS success from PyTorch availability.
- [ ] Run maximum admitted duration, memory/disk refusal, canceled/restarted render, concurrent same-cache clients, setup conflict, coordinator conflict, source removal, and worker quarantine proof using temporary data.
- [ ] Document observed platform support separately: unit/mypy on a platform is not native runtime proof; native stretch/FFmpeg/worker recovery require actual macOS/Linux/Windows evidence.
- [ ] Review final diff for unsafe casts/suppressions, path handling, loose JSON, hidden imports/downloads, extra infrastructure, source overwrites, unbounded matrices, stale revisions, missing translation labels, and accidental executable retired-engine references.
- [ ] Complete the verification commands below and record actual command exit status/results. No commit/push/deployment is part of this plan.

## Regression fixture matrix

Generate fixtures under a TemporaryDirectory. Do not commit copyrighted audio, large model files, private voice takes, or benchmark caches.

| Fixture | Expected behavior | Main regression |
| --- | --- | --- |
| 48 kHz digital silence, mono/stereo | Null LUFS/tempo/key, no confident beats/activity, finite serializable response | Silence is not a musical answer |
| 100 ms and 2 sec tone | Short-programme warning, no confident global key/meter, no divide-by-zero | Short-file stability |
| Pulse train 120 BPM, offset 0.25 sec | Correct offset within declared hop in fake-model boundary test; strict monotone timestamps | Time-base/latency correctness |
| Pulse train with gradual drift | Local tempo map and ordered grid; no fixed-BPM assumption | Live performance drift |
| Alternating 3/4 and 4/4 accents | Unknown/local meter behavior, no automatic all-4/4 bar snapping | Meter honesty |
| Stereo `(x,-x)` plus click accents | Nonzero power/features; original stereo unchanged | Antiphase cancellation |
| Left-only/right-only impulses | Shared stereo transform latency; no channel swap | Layout conventions |
| Detuned/non-12-tone motif | Unknown/unsupported key warning; no destructive auto transpose | Culture/tuning compatibility |
| Modulating tonal progression | Local-key disagreement warning; no blanket global correction | Key changes |
| Prepared take +10% duration | Anchors satisfy requested ratios and known phrase timing | Speed vs duration convention |
| Missing/repeated/swapped phrase | Needs review/unmatched phrase; no lyric reorder | DTW wrong-but-cheap paths |
| 60 sec unbroken held note | Bounded-memory manual-review response when no safe split | Dense DTW allocation |
| Long PCM metadata beyond budget | Refusal before allocation, original retained | Decode/memory bound |
| Cancel worker with child process | Descendants drained before cleanup/admission release | Ownership |
| Crash after audio rename before SQL commit | Verified receipt permits idempotent completion or safe interrupted state | Recoverable publication |
| Changed source/preset/model hash | New analysis/cache identity and invalidated downstream preview | Cache truthfulness |
| 12 kHz intersample peak fixture | True peak can exceed sample peak; final post-codec measurement determines pass | False limiter guarantees |

The synthetic fixtures assert mechanics. They do not validate singing quality, cultural authenticity, source-rights clearance, or the report's proposed held-out targets.

## Future verification commands — not executed for this plan

Use the repository's existing development interpreter that has `backend/requirements-test.txt` installed, or create a dedicated temporary test environment during implementation. No model download is needed for default tests. Do not run these commands against the user's active config/data/engines.

From repository root, initialize isolation once in the test shell:

```bash
MUSIC_TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/openfabric-music-tests.XXXXXX")"
export OPENFABRIC_CONFIG="$MUSIC_TEST_ROOT/config.json"
export OPENFABRIC_DATA_DIR="$MUSIC_TEST_ROOT/library"
export SEED_VC_DIR="$MUSIC_TEST_ROOT/seed-vc"
export OPENFABRIC_MODULE_ROOT="$MUSIC_TEST_ROOT/modules"
export XDG_CACHE_HOME="$MUSIC_TEST_ROOT/cache"
export HF_HOME="$MUSIC_TEST_ROOT/huggingface"
export TORCH_HOME="$MUSIC_TEST_ROOT/torch"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
mkdir -p "$OPENFABRIC_DATA_DIR" "$SEED_VC_DIR" "$OPENFABRIC_MODULE_ROOT"
```

The actual environment override is `OPENFABRIC_MODULE_ROOT`, not an invented `MODULE_ROOT`. These variables also apply to native proof and contract generation because startup/config imports can place model links.

Focused Python suite from `backend/` after the proposed modules exist:

```bash
python -m unittest \
  app.music_analysis_contracts_test app.music_analysis_store_test \
  app.music_analysis_test app.music_analysis_features_test \
  app.music_analysis_environment_test app.music_analysis_worker_test \
  app.music_dsp_contracts_test app.music_alignment_test app.music_dsp_test \
  app.api.routes_music_analysis_test \
  app.audio_export_quality_test app.audio_versions_test app.audio_exports_test \
  app.resource_admission_test app.music_queue_ownership_test app.job_lifecycle_test \
  app.removed_music_engine_test app.contract_generation_test -v
```

Expected after implementation: all behavioral assertions pass; offline/missing-model tests pass without contacting a model host. Native integration proofs run separately in the prepared runtime, not as hidden downloads inside this command.

Generated contracts from repository root:

```bash
python backend/scripts/generate_contracts.py
python backend/scripts/generate_contracts.py --check
```

New strict module scope from `backend/`:

```bash
for target in linux darwin win32; do
  python -m mypy --strict --follow-imports=silent \
    app/music_analysis_contracts.py app/music_analysis_store.py \
    app/music_analysis.py app/music_analysis_features.py \
    app/music_analysis_environment.py app/music_analysis_setup.py \
    app/music_analysis_worker.py app/music_dsp_contracts.py \
    app/music_alignment.py app/music_dsp.py app/music_dsp_worker.py \
    app/api/routes_music_analysis.py --platform "$target"
done
```

Also run the **complete existing** platform-loop command list in `.github/workflows/ci.yml` from `backend/`; the new scope is additional, not a replacement for current checked core/dispatch paths. A platform typing pass does not establish native compatibility.

Required backend regression suite from `backend/`:

```bash
python -m unittest discover -s app -p '*_test.py' -v
```

Required frontend checks from `frontend/`:

```bash
npm test
npm run build
```

Because startup/module/file-layout integration changes, run desktop checks from `desktop/`:

```bash
npm test
```

Run `git diff --check`, inspect `git diff --stat` and final patches; record any test skip and native evidence gap. Delete the temporary test root only after all subprocesses are drained and its path is verified as this task's scratch directory. Do not include an automatic broad `rm -rf` in a copied verification block.

## Completion gate and handoff to other plans

- [ ] All wire model names/units/unknown semantics and source→target mapping agreed with sibling plans.
- [ ] Raw cache identity covers source bytes, role, engine/model/dependency/config; derived plan/render identity additionally covers effective analysis and override revision.
- [ ] No production source is overwritten; original, dry vocal, accompaniment, and prepared-take provenance remain independently retrievable.
- [ ] Analysis jobs survive browser closure, cancel/retry/restart safely, and release admission only after drain proof.
- [ ] Every heavy child is supervised and fingerprinted; runtime has no hidden network/setup step.
- [ ] Silent, short, detuned, irregular-meter, stereo, mismatched-phrase, and excessive-size cases return honest diagnostics.
- [ ] Prepared alignment fails conservatively, preserves phrase order, and does not allocate whole-song dense DTW.
- [ ] Backend preview and export use the same revision/artifact; manual changes invalidate older previews.
- [ ] LUFS/true-peak reports identify actual output measurement and do not replace source-rights review.
- [ ] Default CI, strict platform paths, generated contracts, frontend tests/build, affected backend/desktop checks pass with actual captured outputs.
- [ ] Hardware/listening/held-out targets are labeled unproven until their corresponding evidence exists.

Downstream voice plan consumes `MusicSourceRef`, `AnalysisResult`, `AlignmentResult`, `DspTransformRequest`, `DspMixRequest`, and the existing loudness models. Mashup consumes beat/downbeat/key/section/energy evidence and produces an ordered arrangement; unknown fields require manual planning. Beat/song mixing consumes the same grid/DSP; culture presets retain their explicit symbolic tuning rather than coercing it through the 12-tone key estimator. Generation engines and training remain outside this shared foundation.

Remaining uncertainties are concrete: selected package pins/wheels/checkpoint digests have not been resolved in an install, native binding latency has not been measured, MPS behavior is unverified, MIR confidence thresholds need calibration, and perceptual/held-out targets need real evaluation. The plan provides explicit tasks and stop conditions for each; it does not manufacture benchmark results to make the architecture look finished.
