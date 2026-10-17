# Deterministic Beat Maker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This planning artifact does not authorize implementation, installation, downloads, live jobs, migrations, commits, pushes or deployment.

**Goal:** Build a recoverable, editable beat sequencer with deterministic musical controls, licensed sample instruments, exact rendered previews, WAV/stem/MIDI/JSON exports and an existing-editor handoff.

**Architecture:** Keep patterns and instrument identities in bounded backend contracts and SQLite revisions. Compile patterns once into sample-frame events, render CPU audio in an owned worker, and play that immutable WAV in the browser; export reads the same artifact. Regional presets use these contracts in plan 04; model-conditioned songs and beat/song mixing consume immutable render snapshots in plan 05.

**Tech Stack:** Existing FastAPI/Pydantic/SQLite, NumPy/soundfile, Vue 3/Pinia/vue-i18n/Vitest, owned worker supervision, existing editor/audio-version/export-provenance modules; `mido` only if the MIDI task establishes that no existing suitable writer is available.

---

## Scope, evidence and decisions

This is an implementation plan; the proposed files, contracts and endpoints below do not exist yet. It covers report §§5.1–5.4, 5.7–5.9 and phase 2. Plan 04 owns named regional scales, rhythms, instruments and review; this plan provides their common engine. The report's calendar estimates are hypotheses, not delivery commitments.

Verified in the inspected repository:

- `frontend/src/audio/timelineTypes.ts` has version-1 lanes and audio/MIDI clips, a single project BPM and uniform per-clip stretch. It has no beat-pattern representation or microtuning.
- `frontend/src/api/editorValidation.ts` validates projects and rebuilds accepted fields, so an unregistered `beat` property would disappear on save, undo or load.
- `backend/app/api/routes_projects.py` and `client_contracts.py` deliberately store opaque project JSON. Use a separate typed beat document; do not convert every historical editor project as collateral work.
- `frontend/src/audio/mixerEngine.ts` already supplies EQ, compressor, filter, pan, effects and shared live/offline graph construction. The report's statement that no EQ exists is inaccurate. Reuse its controls for existing audio lanes; the deterministic beat renderer initially has gain/pan and a master safety stage only.
- `frontend/src/audio/miniMidiPlayer.ts` supports semitone notes, velocities and tempo maps, but it ignores pitch bends and cannot provide accurate microtonal playback.
- Existing runtime ownership is in `job_lifecycle.py` and `video_process.py`; source audio can be retained with `audio_versions.retain_original`. `AudioVersion.kind` currently allows `original|voice`, so publish beat audio as a normal new track plus retained original, not a fabricated `beat` version kind.

Reject the report's browser-scheduler-plus-NumPy “bit-for-bit” claim: different renderers cannot promise identical audio. First release previews the exact completed server artifact. Interactive changes debounce a short render; UI says “Rendering preview” and retains the previous preview until the new revision is ready. A later low-latency audition engine can be labelled approximate only after measurement; it is outside the initial build.

Do not add Tone.js, a service broker, Redis, a model-backed generator, a new DAW, or unrestricted SFZ execution. Rule generation and a bounded sample manifest satisfy the first release. Optional symbolic models and audio-loop generators have a specific evidence gate in Task 12.

## Interfaces shared with plans 04 and 05

All names here are proposed. Generate their client types from Pydantic; do not hand-write parallel TypeScript models.

| Contract | Required content and invariant |
|---|---|
| `BeatDocument` | `schema_version=1`, 32-hex `id`, integer `revision>=0`, title ≤120 chars, quarter-note `bpm` 20–300, seed 0–2³¹−1, `kit_id`, `kit_manifest_sha256`, 1–4 patterns, chain, `global_swing` 0.50–0.75, nullable scale, renderer version |
| `BeatPattern` | `id=A|B|C|D`, 1–4 bars, `meter: BeatMeter`, `steps_per_quarter=2|3|4|6|8`, 8–16 lanes; derive integral `steps_per_bar = numerator*4/denominator*steps_per_quarter`, reject nonintegral or >256 |
| `BeatMeter` | Numerator 1–32, denominator `2|4|8|16`; quarter-note BPM is always the stored clock. UI can display the dotted-beat equivalent without changing that clock |
| `BeatLane` | Stable `lane_id`, label ≤80, `instrument_id`, `role=drum|bass|pitched`, mute, gain −60…+12 dB, pan −1…1, nullable swing override, at most `bars*steps_per_bar` sparse steps with unique indexes |
| `BeatStep` | Index, velocity 1–127, probability 0–1, offset ±0.5 step, ratchet 1–4, nullable note 0–127, residual cents −100…100, gate 0.05–8 steps; optional manifest articulation and bounded pitch curve |
| `BeatScale` | Stable scale ID, root MIDI note, ascending unique `degrees_cents` 0≤x<1200 (1–24 degrees), reference Hz 400–480; preserve cultural/tuning provenance in preset data |
| `BeatPitchPoint` | Normalized note position 0–1, cents −2400…2400, at most 16 ordered points; explicit endpoint handling and manifest capability check |
| `PatternPlacement` | Pattern ID, repeats 1–64 and section label ≤80; ≤128 placements and ≤600 s/4096 bars after expansion |
| `BeatRenderRequest` | Beat ID, expected revision, `preview|full`, preview pattern ID when previewing, client idempotency key; no path or arbitrary sample URL |
| `BeatRenderManifest` | Beat ID/revision/hash, render ID, seed/renderer version, sample rate/frame count, mix artifact ID/hash, per-lane artifacts/hash, pattern JSON artifact, MIDI artifact, source/sample/license manifest, canonical resolved event list/hash |

Every pattern in one document uses the same ordered lane IDs (8–16 total), so a four-pattern chain cannot accidentally produce 64 output lanes. Pattern-specific instrument/step changes remain explicit. Deleting or reordering a lane updates every pattern as one revision; it does not recycle another lane's randomness identity.

4/4 menu labels “16”, “32”, “12 triplet” and “24 triplet” mean 4, 8, 3 and 6 steps per quarter respectively. Do not conflate “12 steps per bar” with 12/8 meter. Compound and asymmetric meters must work in the compiler, duration labels, keyboard grid and MIDI conductor track before plan 04 advertises them. Mixed-meter chains are supported through per-pattern meters; store all positions in quarter-note ticks with PPQ 960, not accumulated floating point seconds.

Scale degrees resolve to a total pitch before render, then canonical MIDI note plus residual cents. Keep `MusicKey` from `music_score_contracts.py` unchanged: it represents a Western ABC selection, not every world tuning. Non-Western keys can remain unknown in analysis; never invent a major/minor label.

Random outcomes use a named, versioned hash of `(seed, pattern_id, lane_id, chain_occurrence, step_index, purpose)`; probability, humanise and region selection get distinct purposes. Probability is evaluated once per source step, ratchets inherit that result, and each chain occurrence gets reproducible fresh outcomes. Preview of an occurrence uses that same occurrence coordinate. This avoids changing every downstream note when a lane is inserted.

## File map

Create backend domain files `backend/app/beat_contracts.py`, `beat_store.py`, `beat_instruments.py`, `beat_compile.py`, `beat_render.py`, `beat_jobs.py`, `beat_midi.py`, `beat_projects.py`, `backend/app/api/routes_beats.py`, `backend/scripts/beat_render_worker.py`, and `backend/app/assets/beat-kits.json`. Small pure compile/render helpers stay in these files; do not create a package per function.

Create tests `backend/app/beat_contracts_test.py`, `beat_store_test.py`, `beat_instruments_test.py`, `beat_compile_test.py`, `beat_render_test.py`, `beat_jobs_test.py`, `beat_midi_test.py`, `beat_projects_test.py`, `backend/app/api/routes_beats_test.py`. Generate tiny impulse/sine WAVs in temporary test directories rather than checking large samples into Git.

Create frontend `frontend/src/api/beats.ts`, `beats.test.ts`, `frontend/src/stores/beats.ts`, `beats.test.ts`, `frontend/src/views/beats/BeatMakerPage.vue`, `BeatMakerPage.test.ts`, `frontend/src/components/beats/BeatGrid.vue`, `BeatGrid.test.ts`, `BeatStepInspector.vue`, `BeatPatternChain.vue`, `BeatExportPanel.vue`, and `frontend/src/locales/beatWorkspace.ts`.

Modify `backend/app/db.py`, `main.py`, `work_busy.py`, `data_root.py`/existing migration tests when retention paths require it, `backend/scripts/generate_contracts.py`, `.github/workflows/ci.yml`, `frontend/src/api/contracts.ts`, `frontend/src/api/generated.ts` (generator only), `frontend/src/router.ts`, `frontend/src/components/shared/AppSidebar.vue`, `frontend/src/i18n/index.ts`, `frontend/src/audio/timelineTypes.ts`, `frontend/src/api/editorValidation.ts`, `frontend/src/stores/editor.ts`, `frontend/src/views/editor/EditorPage.vue`, `frontend/src/components/editor/EditorHelpModal.vue`, and affected editor/API tests. Update `backend/requirements.txt` and `requirements-test.txt` only for a justified MIDI dependency, with actual license/evidence in plan 00. Update `docs/music-beat-maker.md` and `ROADMAP.md` with measured support.

## Task 1: Freeze bounds and contract validation

**Files:** Create `beat_contracts.py`, `beat_contracts_test.py`; modify contract generator/export entrypoints.

- [ ] Add failing tests for nonfinite BPM/probability, strings masquerading as numbers, duplicate lane/step IDs, unknown pattern references, 7-lane/17-lane rejection, >4 bars, nonintegral grids, out-of-range note/cents/curve, contradictory unpitched notes and oversized expanded chain.
- [ ] Start with these actual proposed test assertions; imports fail until the module exists:

```python
from unittest import TestCase
from pydantic import ValidationError
from app.beat_contracts import BeatMeter, BeatStep, BeatPattern

class BeatBoundsTests(TestCase):
    def test_step_bounds_and_strict_numbers(self) -> None:
        for patch in ({'probability': float('nan')}, {'ratchet': 5},
                      {'index': '0'}, {'offset_steps': 0.51}):
            with self.subTest(patch=patch), self.assertRaises(ValidationError):
                BeatStep.model_validate({'index': 0, **patch})

    def test_compound_meter_clock(self) -> None:
        meter = BeatMeter(numerator=12, denominator=8)
        self.assertEqual(meter.quarters_per_bar, 6)
        self.assertEqual(BeatPattern.grid_size(meter, 4), 24)
```

- [ ] Run `python -m unittest app.beat_contracts_test -v` from the isolated backend environment described below. Expect a missing-module failure initially; after adding contracts expect green bounds tests.
- [ ] Implement strict `BeatContract` with `ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)`, bounded aliases and named models above. A proposed core (remaining fields follow the interface table):

```python
class BeatStep(BeatContract):
    index: int = Field(ge=0, le=1023)
    velocity: int = Field(default=100, ge=1, le=127)
    probability: float = Field(default=1.0, ge=0, le=1)
    offset_steps: float = Field(default=0.0, ge=-0.5, le=0.5)
    ratchet: int = Field(default=1, ge=1, le=4)
    note_midi: int | None = Field(default=None, ge=0, le=127)
    cents: float = Field(default=0.0, ge=-100, le=100)
    gate_steps: float = Field(default=1.0, ge=0.05, le=8)
    articulation_id: str | None = Field(default=None, max_length=80)
    pitch_curve: list[BeatPitchPoint] = Field(default_factory=list, max_length=16)
```

- [ ] Add cross-field validators for every table invariant; `BeatPattern.grid_size(meter, steps_per_quarter)` returns the validated integer and `quarters_per_bar` uses an exact rational internally. Validate maximum compiled event count 262,144 after ratchets and repeats, not just JSON size.
- [ ] Register `BEAT_CLIENT_MODELS` in `generate_contracts.py`; re-export generated parsers through `frontend/src/api/contracts.ts`; run generation and drift check. New literals/arrays must pass the generator without loosening its accepted schema vocabulary.

## Task 2: Revisioned document and recoverable migration

**Files:** Create `beat_store.py`, `beat_store_test.py`; modify `db.py`.

- [ ] Test a fresh DB, a DB created before the feature, duplicate submission, conflicting same-key submissions, concurrent saves with one expected revision, deletion with retained renders and interrupted migrations. Use two SQLite connections for the race test.
- [ ] Add additive migration tables `beat_documents(id,revision,body_json,created_at,updated_at)`, immutable `beat_revisions(beat_id,revision,body_json,body_sha256)`, `beat_render_jobs(id,beat_id,beat_revision,request_hash,status,worker_receipt_json,manifest_json,error_code,created_at,updated_at)` and `beat_submissions(submission_key,request_hash,job_id)`. Add foreign keys, JSON checks, unique revision/key constraints and a status index. Store migration version explicitly; old editor `projects` rows are untouched.
- [ ] Use this update shape inside a transaction; rollback all companion writes if no row matched:

```sql
UPDATE beat_documents SET revision=revision+1, body_json=?, updated_at=?
WHERE id=? AND revision=?;
```

- [ ] Add `create(document)`, `get(beat_id)`, `update(beat_id, expected_revision, document)`, and `get_revision(beat_id, revision)` with typed return values. They are proposed functions in `beat_store.py`. Rename IDs/revision from server-owned values after parsing; never trust a client to change the document identity.
- [ ] Test the same document revision hash is used by the render job even if a newer edit is saved during rendering. Source revisions remain until no retained render/project references them; deletion marks the document archived and preserves required snapshots.
- [ ] Put owned assets beneath `db.FILES_DIR / '_beats'` with root-relative stored asset names and IDs, so normal library copying includes them. Worker receipts contain runtime absolute paths: either resolve them from relative IDs at recovery or register the new JSON columns/metadata subtree in `data_root.py`'s existing relocation machinery. Test an idle library move and recovery before claiming relocation support; never move a library during active work.
- [ ] Run `python -m unittest app.beat_store_test -v`; expect both concurrent writers to finish with one success and one `beat_revision_conflict`, no lost update.

## Task 3: Licensed, bounded instrument manifest

**Files:** Create `beat_instruments.py`, `assets/beat-kits.json`, `beat_instruments_test.py`; modify plan-00 license/export registration.

- [ ] Write failure tests for traversal, absolute paths, backslashes, archive links, symlink escapes, bad sample hash, huge duration, missing source license, overlapping ambiguous regions and unsupported articulations.
- [ ] Define `BeatInstrumentManifest`/`BeatInstrument`/`BeatSampleRegion` in `beat_contracts.py`. Manifest fields include schema version, kit/instrument IDs, SHA-256/bytes, root-relative sample asset ID, sample rate/channels/frames, root note/cents, note and velocity ranges, articulation ID, one-shot versus gated envelope, choke group, source URL/revision, license ID, attribution text and redistribution evidence ID.
- [ ] Resolve user `instrument_id` only against the server-approved immutable manifest; no filesystem path, command, SoundFont path or URL is accepted from beat JSON. Confine assets with resolved root containment and no symlink traversal; reject unsupported sample metadata before allocating arrays. Bound each sample to ≤30 s, mono/stereo and 8–192 kHz, and kit total decoded bytes ≤512 MiB.
- [ ] Bundle a tiny permissively recorded/CC0 starter kit only after plan-00 evidence checks. Keep real sample assets outside application source where packaging expects managed content; package the manifest and required license/attribution. Verify cache and destination free space before download/copy. Existing source files are preserved on a bad download.
- [ ] Convert reviewed SFZ instruments to explicit manifest regions using an allowlisted import subset in a build/import step: sample, root pitch, note/velocity range, gain, envelope and choke only. Reject include directives, absolute samples, unsupported opcodes and conflicting regions. Do not claim general SFZ support.
- [ ] Treat GeneralUser GS as a whole licensed bank if selected; never extract its samples into the starter kit. SF2/FluidSynth is a later separate process adapter with its own measured parity and license task, not a hidden renderer substitution. VCSL/VSCO/Freesound candidates must have item-level evidence before inclusion; the report alone is not permission.
- [ ] Run `python -m unittest app.beat_instruments_test -v` with temporary fixture roots and synthetic WAVs; no network or model download.

## Task 4: Pure event compiler, timing, probability and ratchets

**Files:** Create `beat_compile.py`, `beat_compile_test.py`.

- [ ] Test 4/4 grids 16/32/12/24, 12/8, 9/8, 10/8, 18/8, mixed-meter chains, tempo extremes, ratchet boundaries, negative offset at frame zero, note tails and block boundaries. An impulse test must measure onset frames, not merely check list lengths.
- [ ] Add proposed pure helpers `event_fraction(seed, coordinate, purpose) -> float`, `compile_events(document, sample_rate) -> CompiledBeat`, and typed frozen dataclasses `CompiledEvent`, `CompiledBeat`. Each event carries lane/instrument/sample identity, integer onset/gate frames, velocity, pitch/cents/curve, occurrence, note/choke identity. No filesystem or random global state in compilation.
- [ ] Build a canonical SHA-256 input with fixed separators and sorted keys and map its first 64 bits into `[0,1)`. Version the algorithm. Humanise uses separate hash purposes for timing and velocity so toggling one does not perturb the other.
- [ ] Use quarter-note PPQ 960 and exact rational positions until final frame rounding. Define onset as round-half-up of absolute rational frames. Clamp a negative first-event offset to frame 0 and record `beat_event_clamped`; never shift the complete song or drop an event silently.
- [ ] Swing changes the midpoint of each pair from 50% to the requested share, preserving pair length. Apply it to supported binary subdivisions only; for triplet/odd subdivisions disable the control with `beat_swing_grid_unsupported` instead of creating an undocumented groove. Per-lane override replaces global swing. Ratchets divide the post-swing step span evenly; micro-offset moves the group without changing repeat spacing.
- [ ] Write and run a direct determinism test with an explicit coordinate:

```python
from unittest import TestCase
from app.beat_compile import event_fraction

class BeatRandomTests(TestCase):
    def test_coordinate_randomness_is_stable(self) -> None:
        key = ('A', 'kick', 3, 0)
        value = event_fraction(4471, key, 'probability')
        self.assertEqual(value, event_fraction(4471, key, 'probability'))
        self.assertTrue(0 <= value < 1)
        self.assertNotEqual(value, event_fraction(4471, key, 'velocity'))
```

- [ ] Bound total frames at 600 s plus ≤30 s tail, event count, polyphony ≤128, and maximum events in a block; fail before render with `beat_render_limit`. Run `python -m unittest app.beat_compile_test -v`.

## Task 5: CPU sampler and exact preview/export artifact

**Files:** Create `beat_render.py`, `beat_render_test.py`, `scripts/beat_render_worker.py`.

- [ ] Create temporary PCM impulses, two-note sine samples and a known choke-group fixture. Test tuning ratio, velocity, pan, coincident events, stereo downmix policy, exact onset frames, sample tails, clipping, silence and NaN rejection. Compare repeated artifact hashes on the same renderer/runtime; do not assert cross-platform floating-point bit identity without evidence.
- [ ] Implement typed `render(compiled, instruments, output_dir, cancelled) -> BeatRenderManifest`. Use bounded float32 blocks (4096 frames) and a fixed processing order by lane ID/event index, not one 600 s × lane matrix. Read regions by resolved asset IDs, reuse decoded samples inside the bounded cache, integrate playback phase for pitch curves and linear interpolation for an initial measured sampler. Apply deterministic short envelopes to gated/choked samples and constant-power pan.
- [ ] Export aligned float32 lane stems and a PCM24 mix; store a separate master safety gain in the manifest. Define `mix = sum(stems) * master_gain`, avoiding independently normalised stems. If an actual limiter is requested, apply the shared plan-01 mastering implementation and record its nonlinearity; do not promise stems sum to a limited master exactly.
- [ ] Render into a unique job staging directory, fsync output files, verify format/frame count/hash, atomically publish the manifest last. Zero-input/silent output returns a valid explicitly silent preview with `beat_silent`, never a misleading failure percentage.
- [ ] `preview` renders one chosen occurrence/pattern with at most 4 bars; full render uses the chain. Play and download the same stored WAV; its displayed hash/revision is the preview identity. Download never rerenders or advances the seed.
- [ ] Test last-sample correctness through a block boundary, deterministic rerender, cancellation mid-block, disk full during stem write and manifest failure after completed WAVs. Completed unpublished outputs remain recoverable for retry-save; user assets remain untouched. Run `python -m unittest app.beat_render_test -v`.

## Task 6: Owned durable CPU jobs, API and limits

**Files:** Create `beat_jobs.py`, `api/routes_beats.py`, both tests; modify `main.py`, `work_busy.py`, shared plan-01 job integration if available.

- [ ] First test enqueue→disconnect→done, duplicate submission, cancelling queued/running jobs, shutdown during publish, parent exit, hung codec descendant, worker receipt mismatch and restart between file publication/DB link. A job may publish exactly one track for its identity.
- [ ] Follow existing `local_music.py`/`video_process.py` lifecycle within the specific `beat_jobs.py` owner. Plan 01 adds analysis ownership and caller-owned DSP, not a generic workflow scheduler. One active beat CPU render, at most 8 waiting jobs and bounded history 1000; CPU work does not acquire the GPU lease but must not mutate a shared engine.
- [ ] Start the worker through existing owned supervisor, retain its token/receipt, await complete descendant exit on cancellation, and drain registry in `main.py` lifespan. A thread alone is unsuitable for interruptible long rendering; use block cancellation plus process termination fallback.
- [ ] Add proposed endpoints `POST/GET /api/beats`, `GET/PATCH /api/beats/{id}`, `POST /api/beats/{id}/renders`, `GET /api/beats/renders/{render_id}`, `POST .../{render_id}/cancel`, `POST .../{render_id}/retry-save`, and artifact-ID-based downloads. Ensure static `renders` routes precede `{id}`. All mutations carry expected revision/idempotency key where applicable; serve artifacts only through confined IDs.
- [ ] Before admission, calculate worst-case output size from frames × channels × sample bytes × (lane count+mix), MIDI/event/pattern allowance and staging overhead. Require free bytes ≥estimate×1.25+256 MiB on every actual destination filesystem; recheck at publish and convert ENOSPC to `beat_disk_full`.
- [ ] Stable public codes: `beat_not_found`404; `beat_revision_conflict`, `beat_submission_conflict`, `beat_sample_changed`, `beat_instrument_missing`409; `beat_pattern_invalid`, `beat_render_limit`, `beat_swing_grid_unsupported`422; `beat_queue_full`429; `beat_disk_full`, `beat_worker_failed`, `beat_cleanup_failed`, `beat_save_failed`503. Recovery uses `interrupted` for lost CPU work, `blocked` for unproven cleanup and `saving` for completed retained artifacts. Keep technical exception details in backend logs.
- [ ] Run `python -m unittest app.beat_jobs_test app.api.routes_beats_test -v` and verify a stale API response cannot mutate another job/document. Test fake owned children and temporary roots only.

## Task 7: Rule generation, humanise, fills and chaining

**Files:** Modify `beat_compile.py`, `beat_contracts.py`; create `backend/app/beat_rules.py`, `beat_rules_test.py`; extend routes/store/UI contracts.

- [ ] Add a failing deterministic-generation test before each generator: basic backbeat, offbeat hats, seeded variation, one-bar fill, scale-degree melody and mixed-meter section chain. Assert exact note/event fingerprints for a small pinned fixture, not screenshots of randomness.
- [ ] Implement `generate_pattern(template_id, seed, density, variation, scale, meter)` in `beat_rules.py` over immutable, reviewed templates. Define `density`/`variation` as bounded 0–1 transformations with named purposes; all outputs pass `BeatPattern` validation. Plan 04 supplies regional templates through the same typed data seam.
- [ ] Humanise command proposes timing ≤0.10 step and velocity ±12 units by default, writes actual step offsets/velocities into a new beat revision, preserves seed, and supports undo. No perpetual invisible jitter at playback. Fill command modifies only the selected last bar and selected lanes, is previewed before applying and does not mutate earlier sections.
- [ ] Chain A/B/C/D by repeats and named intro/verse/chorus sections. “Render two minutes” derives an explicit finite chain, trims only at a legal bar unless user chooses an explicit fade, and displays the exact resulting duration before enqueue. Never secretly loop to an arbitrary hard-coded 120 s.
- [ ] Run `python -m unittest app.beat_rules_test app.beat_compile_test -v`; rerunning with the same manifest/seed produces identical authoring JSON and resolved events.

## Task 8: MIDI with honest cents and export provenance

**Files:** Create `beat_midi.py`, `beat_midi_test.py`; modify `export_provenance.py`, `export_provenance_contracts.py` only as required by plan-00 manifests; optional requirements update.

- [ ] Test tempo/meter changes, program/channel mapping, percussion channel 10 (zero-based 9), note-off-before-reused-note-on, overlaps with different cents, pitch curves and channel exhaustion. Decode emitted SMF independently, not with only the writer under test. Verify initial pitch state and reset after each released channel.
- [ ] Check existing dependencies/writers first. If none is adequate, pin `mido` with upstream license evidence; a maintained small MIDI writer is cheaper to maintain than a homemade binary parser. Keep its third-party types accurate; do not add `Any` or casts to silence it.
- [ ] Export SMF type 1, PPQ 960, conductor tempo/time-signature events per chain placement. Map cents and curves through pitch bend with RPN bend range explicitly set to the smallest supported whole-semitone range containing the maximum absolute **total** residual plus curve (up to 25 semitones with these bounds). Allocate a separate melodic channel for simultaneously sounding distinct bend trajectories; never bend all notes on a channel to one note's tuning. Pitched percussion uses a melodic channel rather than sending tuning bends to shared percussion channel 10. Chokes emit note-offs.
- [ ] With >15 simultaneous melodic bend states (percussion channel reserved), fail lossless MIDI export with `beat_midi_channels_exhausted`. Offer an explicit “12-TET MIDI approximation” export with warnings/rounding manifest; never silently quantize the default. For controller timing use ≤5 ms intervals plus exact curve knots and bounded event total.
- [ ] Always export `pattern.json` plus `tuning.json` with canonical cents/curves, sample/program mappings and a warning that generic GM players will use different sounds. Exact audio is WAV/stems; exact authoring is pattern JSON. Do not use the existing browser mini player for a “microtonal proof.”
- [ ] Exports include license/attribution/provenance JSON and text, immutable sample and preset IDs/hashes, render/event algorithm version, seed/revision, mixer/master settings, original/edited origin and any quantization. User recordings retain user-supplied rights status; never turn unknown into “commercial eligible.”
- [ ] Run `python -m unittest app.beat_midi_test -v` plus plan-00 export-provenance tests. Test interrupted ZIP creation and retry without deleting completed WAVs.

## Task 9: Existing editor handoff, re-edit and revisions

**Files:** Create `beat_projects.py`, `beat_projects_test.py`; modify timeline types/parser/store/editor page and relevant tests.

- [ ] Add frontend/backend tests proving an ordinary legacy project round-trips unchanged, a beat link survives parser/save/undo/reload, the imported stems start at sample zero and an old render remains playable after a beat edit.
- [ ] Publish mix as a normal library track with `audio_versions.retain_original`; preserve each per-lane stem in the render manifest. Create a conventional version-1 timeline project with one audio lane per stem, BPM and default house mixer settings. Avoid inventing a new clip type; use audio clips plus a typed optional `BeatClipLink`.
- [ ] Define the single reusable project seam here for plans 05/06: `RenderedProjectLane` carries stable lane ID, label, retained artifact ID/hash, start/trim/fade seconds, validated house mixer settings, muted state and optional `BeatClipLink`; `MusicProjectProvenance` carries workflow ID/revision, source manifest hash and at most 32 retained source/version IDs. `EditorProjectDocument` is the bounded backend model for newly built version-1 documents with ≤32 lanes/4096 clips and existing `version/lanes/master/pxPerSecond/bpm/snapEnabled/loopRegion` fields. `BuiltMusicProject` carries `{name, data: EditorProjectDocument, provenance}` so title is retained outside the editor JSON as the current API expects. Derive its mixer ranges from inspected `editorValidation.ts`, generate types, and test against that parser. Keep historical project JSON opaque on existing routes.
- [ ] Add proposed `build_audio_project(title, bpm, lanes: Sequence[RenderedProjectLane], provenance: MusicProjectProvenance) -> BuiltMusicProject` and `publish_audio_project(built: BuiltMusicProject, idempotency_key: str) -> int` to `beat_projects.py`. Publication inserts the project and a unique workflow-publication mapping in the same SQLite transaction, rather than calling `db.insert_project` and hoping a crash between commits cannot duplicate it. Task 2 adds this small mapping table. Plan 05/06 import this helper instead of creating another exporter.
- [ ] Add proposed `BeatClipLink { beat_id, beat_revision, render_id, lane_id, occurrence_index }` to `Clip`, accept it in `parseClip`, and serialize through editor history. Backend validates IDs/revision when building the project. Add an optional project-level provenance link only if required for a source-free empty clip; otherwise the per-clip link suffices.
- [ ] Add “Edit beat” action to `EditorPage.vue`; navigate to beat ID/revision, show current versus pinned revision, and save edits as a new revision. “Update selected beat clips” is explicit, preserves arrangement/mixer values and checks the loaded editor session and server project identity before applying. Re-render never silently replaces an old artifact used by other projects.
- [ ] Microtonal MIDI may be a secondary reference lane only after extending its playback/parser for pitch bends. Initial exact project playback uses rendered audio stems; editable Beat Maker data remains attached and can be regenerated. Document this clearly in UI/help/export, without calling oscillator preview an identical instrument.
- [ ] From `backend/`, run `python -m unittest app.beat_projects_test -v`.
- [ ] From `frontend/`, run `npm test -- src/stores/editor.test.ts src/views/editor/EditorPage.test.ts src/audio/timelineEngine.test.ts`.

## Task 10: Beat Maker controls and accessibility

**Files:** Create beat API/store/page/components/tests/locales; modify router/sidebar/i18n/help.

- [ ] Write component tests for keyboard-only pattern editing, 16↔triplet grid changes preserving valid positions or presenting explicit conversion, selected-step values, save conflict, failed preview retaining old audio and focus after lane deletion. Test actual emitted actions and form labels.
- [ ] API layer uses generated response parsers through existing `apiFetch`/`apiJson`. Store keeps document revision, unsaved edits, expected render identity, `AbortController` and generation token; abort polling on teardown and ignore completions for an older document/revision/session. Edits during save remain dirty.
- [ ] Build 8 default lanes with add/remove to 16, A/B/C/D selector, meter/bars/grid, kit/instrument picker, BPM, root/scale/tuning, global/per-lane swing, velocity/probability/offset/ratchet/gate inspector, mute/solo/gain/pan, undo/redo, chain ordering and Generate/Humanise/Fill.
- [ ] Use roving tabindex in the grid; arrow keys move, Home/End navigate row, Space/Enter toggle, Shift+arrows selects a region, Delete clears selected steps, Escape returns focus. Each cell is a button with `aria-pressed`, lane/bar/beat/subdivision/velocity text; status and play position are not conveyed by color alone. Avoid thousands of simultaneous tab stops.
- [ ] Add explicit save/unsaved/conflict/error states, progress phases (`queued`, `compiling`, `rendering`, `publishing`, `done`, `cancelling`, `interrupted`, `blocked`), measured frames/total only while available and indeterminate setup otherwise. Show whether preview matches the current revision; disable downloads for a nonexistent artifact.
- [ ] Include translated help describing quarter-note BPM versus dotted beats, probability seed, swing/triplet restriction, sample tail, cents, MIDI limitations, exact preview artifact, rights and disk usage. Register `beatWorkspace` in every supported locale through the existing i18n loading convention; tests reject missing keys rather than displaying raw key strings.
- [ ] Reuse house buttons/dialogs/library picker/audio transport. Stop/disconnect any owned preview node on source change/unmount; obey shared transport exclusivity. Test audio dispose and repeated mount/unmount without duplicate listeners.
- [ ] Run `npm test -- src/api/beats.test.ts src/stores/beats.test.ts src/views/beats/BeatMakerPage.test.ts src/components/beats/BeatGrid.test.ts` then `npm run build` from `frontend/`.

## Task 11: Integrate, verify and demonstrate recovery

**Files:** `.github/workflows/ci.yml`, `docs/music-beat-maker.md`, `ROADMAP.md`, affected tests.

- [ ] Add new backend domain/routes/worker files to strict mypy's three-platform scope. Include behavioral unit tests under existing discover patterns; CI must not install real kits or GPU weights.
- [ ] Use the isolated commands below; do not present them as results from this planning session. Run targeted tests after each task, full checks once integrated, and rerun only affected checks after final corrections.
- [ ] With a temporary library, manually create a 2-bar beat, adjust a probability step, save, render, close the tab, reopen and recover the same artifact. Export stems/MIDI/JSON, open the editor, edit a pinned beat clip and verify the earlier project still plays its prior render. Test keyboard-only and narrow viewport flows.
- [ ] Measure render wall time, peak memory, preview edit latency and artifact hashes on CPU/Apple Silicon. Record hardware/software/sample kit; there is no report-backed real-time budget. A slow full render is a performance issue, not evidence for a new queue service.
- [ ] Review final diff for generated-contract drift, new unchecked JSON, filesystem exposure, mutable sample identities, accidental user-library tests and dropped old project fields. Report actual commands/results and unresolved platform measurements. Do not commit, publish or deploy without a request naming the branch.

## Task 12: Optional generator/instrument experiments, isolated from baseline

**Files if an experiment is selected:** `backend/app/beat_generator_contracts.py`, `beat_generators.py`, `beat_generators_test.py`, `module_catalog.py`, `module_install.py`, `module_licenses.py`, corresponding generated contracts/UI/help; record experiment evidence in `docs/music-beat-maker-experiments.md`.

- [ ] For GrooVAE/Anticipatory Music Transformer, verify exact code and checkpoint license, supported runtime and bounded inference API from pinned primary source. Add an opt-in generator producing validated `BeatPattern`, retain source/generated revisions and test cancellation/seed behavior. If evidence is missing, show “Not available: checkpoint/runtime not verified”; do not install or claim support.
- [ ] For Stable Audio loop generation, reuse the existing durable local engine and label its result an audio loop, with measured beat grid and an audio lane; it is not an editable step pattern. For Magenta RealTime 2, first reproduce its claimed runtime on the actual target before adding another engine. No browser background inference hidden behind Generate.
- [ ] MusicGen weights stay in the explicit noncommercial path from plan 00; do not bundle them as a commercially clean default. HeartMuLa is removed from the current application; do not resurrect an unlicensed port because the earlier report lists it.
- [ ] FluidSynth/SF2 support must render preview and export through that same pinned adapter, preserve whole-bank distribution terms, own its process and measure tuning. Implement only the chosen, justified adapter in a later authorized execution; commits still require a maintainer request naming the branch. The base sampler remains usable when none is installed.

## Exact future verification environment and commands

Run from repository root in a disposable shell, with the repository test Python environment active (`source backend/.venv/bin/activate` in this inspected macOS checkout; bare host `python` is unavailable):

```bash
BEAT_TEST_ROOT="$(mktemp -d -t openfabric-beat-tests)"
export OPENFABRIC_CONFIG="$BEAT_TEST_ROOT/config.json"
export OPENFABRIC_DATA_DIR="$BEAT_TEST_ROOT/library"
export SEED_VC_DIR="$BEAT_TEST_ROOT/seed-vc"
export OPENFABRIC_MODULE_ROOT="$BEAT_TEST_ROOT/modules"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
python -m pip install -r backend/requirements-test.txt
python backend/scripts/generate_contracts.py
python backend/scripts/generate_contracts.py --check
```

From `backend/`:

```bash
python -m unittest app.beat_contracts_test app.beat_store_test app.beat_instruments_test app.beat_compile_test app.beat_render_test app.beat_jobs_test app.beat_rules_test app.beat_midi_test app.beat_projects_test app.api.routes_beats_test -v
python -m unittest discover -s app -p '*_test.py' -v
for target in linux darwin win32; do
  python -m mypy --strict --follow-imports=silent app/beat_contracts.py app/beat_store.py app/beat_instruments.py app/beat_compile.py app/beat_render.py app/beat_jobs.py app/beat_rules.py app/beat_midi.py app/beat_projects.py app/api/routes_beats.py scripts/beat_render_worker.py --platform "$target"
done
```

Also run the full existing CI mypy platform loop, not just the new files. Extract its actual commands without hand-maintaining a divergent copy (from repository root):

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

From `frontend/`: `npm test` then `npm run build`. From `desktop/`: `npm test` because shared startup, packaged backend file inclusion and managed asset layout change. Expected: every command exits 0; initially new targeted tests fail until the corresponding task is implemented. No command above was executed as part of writing this plan.

## Acceptance evidence

- Same document/manifest/seed/renderer version yields the same resolved event list, frame count and repeated-render artifact hash in a pinned runtime. Preview and download refer to the same immutable audio bytes.
- All four default grids, 1–4 bars, 8–16 lanes, velocity/probability/offset/ratchets, supported swing, chaining, humanise/fill, scales/cents and regional meters have direct behavioral tests.
- WAV/stems/JSON remain recoverable after browser exit, worker cancellation, ENOSPC and interruption between publication stages. Same-key retry creates no duplicate track.
- MIDI carries tempo/meter and explicit bend policy; channel exhaustion or unsupported tuning is visible. Exact waveform/timbre remains WAV, with authoring precision in JSON.
- Beat/audio/project identities survive save, reload, undo, edits during pending requests and explicit rerender without mutating earlier arrangements.
- No claim of culturally credible presets, GPU performance, live MIDI parity or optional engine support is made until the separate evidence tasks establish it.
