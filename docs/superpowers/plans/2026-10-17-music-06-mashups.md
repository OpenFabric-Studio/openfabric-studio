# Musical Mashup Planning and Rendering Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans with focused file ownership when delegating. Every checkbox is future work; do not commit or run source recordings through new models without the normal authorization and isolation rules.

**Goal:** Turn two selected songs into an explained, editable mashup plan and render it without losing either source or rearranging lyrics accidentally.

**Architecture:** Reuse the analysis cache and typed DSP service from plan 01, the source/artifact ownership rules from plans 00/05, and the existing editor. A bounded mashup job owns separation, planning and rendering. Revisioned musical placements remain separate from editor JSON; one validated adapter creates the project.

**Tech Stack:** FastAPI, Pydantic, SQLite, NumPy, existing Demucs/RoFormer workers, proposed analysis/DSP services, Vue 3 and existing timeline audio lanes.

---

## Dependencies and current gaps

Report sections 4.1–4.9 and 8–11. Start after plan 01 and the editor/render seam in plans 03/05. Existing separation, imported tracks, audio lanes, static BPM warping, effects and project persistence are reusable. Existing code does not provide reliable downbeats, automatic ordered section mapping or compatibility scoring.

The report suggests Hungarian assignment while also requiring vocal order preservation. Unconstrained assignment does not satisfy that requirement. Use a monotone dynamic programme or explicitly constrained order-preserving search. No learned mashup model is required for the initial workflow.

### Proposed file ownership

| Responsibility | Files |
| --- | --- |
| Public schemas | New `backend/app/mashup_contracts.py` |
| Persisted plans/revisions | New `backend/app/mashup_store.py` |
| Compatibility and ordered mapping | New `backend/app/mashup_plan.py` |
| Owned jobs/rendering | New `backend/app/mashups.py`, `backend/app/mashup_render.py` |
| Routes/client | New `backend/app/api/routes_mashups.py`, `frontend/src/api/mashups.ts`; register schemas in `backend/scripts/generate_contracts.py` |
| Editor adapter | Reuse the proposed `backend/app/beat_projects.py` audio-project builder from plan 03 rather than create another project serializer |
| UI | New `frontend/src/views/music/MashupPage.vue`, `MashupPlanEditor.vue`, `MashupCompatibility.vue`, `frontend/src/locales/mashups.ts`; extend `frontend/src/router.ts`, music navigation and existing library picker |
| Lifecycle | `backend/app/main.py`, existing source retention/resource admission/provenance modules, `.github/workflows/ci.yml` |
| Tests | New matching `mashup_contracts_test.py`, `mashup_store_test.py`, `mashup_plan_test.py`, `mashup_render_test.py`, `mashups_test.py`, route tests and Vue/API tests |

Names above identify new planned units, not verified existing functions. Use a single placement-to-editor adapter rather than separate guessed project schemas in each workflow.

## Proposed domain contract

| Entity | Required fields and invariants |
| --- | --- |
| `MashupSource` | Existing track/version ID, captured SHA-256, analysis ID/config hash, source origin/restriction evidence; no caller filesystem path |
| `MashupCandidate` | Orientation, per-component measurements, score `0..100` or null, analysis coverage, proposed shifts/ratios, warnings; no fabricated certainty when key/sections are unknown |
| `MashupPlacement` | Stable ID, source A/B, allowlisted stem ID, source frame interval, target tick interval, gain, fades, transform/artifact ID; positive durations, frame bounds and contiguous lyric order |
| `MashupPlan` | 32-hex ID, revision, source snapshots, quarter-note BPM `20..300`, target key or unknown, meter, ordered placements, transition requests, mix settings, analysis/manual overrides and evidence |
| `MashupRender` | Immutable plan revision/hash, owned job ID/status/progress, render artifacts, project ID, published track ID, warnings/error code |
| Manual overrides | Value, scope and superseded analysis revision; modifying target tempo/key invalidates preview and requires regeneration of transforms |

Initial automatic-analysis limit: ten minutes per input, configurable only after memory/disk measurements. Existing longer source tracks remain stored and can supply a selected bounded excerpt. Source handles select an immutable version and excerpt; IDs are not permission to read arbitrary files.

### Proposed route set

- `POST /api/mashups`: select sources and create one analysis/separation preparation job with idempotency key.
- `GET /api/mashups/{id}`: bounded persisted state, candidate explanations and current plan revision.
- `POST /api/mashups/{id}/plan`: generate the chosen orientation/target settings using an expected revision.
- `PATCH /api/mashups/{id}/plan`: validated placement/lock edits with optimistic revision checking.
- `POST /api/mashups/{id}/preview`: render a selected transition/section against an immutable plan revision.
- `POST /api/mashups/{id}/render`: render/publish the reviewed revision and create its editor project.
- `POST /api/mashups/{id}/cancel`: cancel the owned active operation; preserve all inputs and completed outputs.

Use structured allowlisted errors; `mashup_revision_conflict`, `mashup_source_changed`, `mashup_analysis_incomplete`, `mashup_alignment_unsafe`, `mashup_pitch_override_required`, `mashup_source_missing` and `insufficient_disk` describe distinct recoverable actions.

## M01 — Contracts, storage and recoverable preparation

- [ ] Write request-validation tests for duplicate/invalid sources, malformed versions, large JSON, unknown stem IDs, NaN values, overlong excerpts and traversal/symlink attempts.
- [ ] Implement bounded discriminated contracts and register generated schemas/client decoders. Body size must be capped before expensive parsing.
- [ ] Add a versioned additive SQLite migration for documents, immutable revisions, source pins, jobs and publication receipts. Index job state and document/revision; constrain IDs, status and uniqueness.
- [ ] Cover revision races, duplicate submission with same/different content, copied-library upgrade, interrupted schema creation and unsupported future schema.
- [ ] Snapshot/retain both source versions before acceptance. Detect source mutation while hashing/copying. No shared-directory clearing and no implicit overwrite.
- [ ] Start preparation as an owned job. CPU analysis may run concurrently only within a measured cap; separation respects the existing GPU lease/admission.
- [ ] Persist analysis results, separation artifact hashes and stage completion. On reconnect return the same live job; after process death reuse only verified completed artifacts and mark interrupted stages accurately.
- [ ] Register startup recovery and shutdown draining in `main.py`; cover unverified process exit/quarantine before allowing a conflicting new job.

**Exit:** Two inputs survive failed preparation, reload, cancellation, duplicate requests and a backend restart.

## M02 — Compatibility explanations and both orientations

- [ ] Create synthetic beat/key/section fixtures for same tempo/key, relative key, half/double tempo, drifting tempo, unknown key, incompatible meters and large pitch shifts.
- [ ] Implement key-distance candidates with bounded transposition and explicit relative-mode handling. A chroma similarity is not proof that melodies will harmonize.
- [ ] Implement tempo ratio and half/double alternatives. Show the alias selected; do not silently reinterpret bars and accents based on a convenient BPM multiple.
- [ ] Calculate section-duration fit and energy/spectral overlap using source analyses. Use vocal-stem activity where available, not a voice-detection claim from whole-mix periodicity.
- [ ] Start with the report's proposed weights: key 35%, tempo 30%, section fit 20%, energy/clash 15%. Store scoring version and raw measurements for reproducibility.
- [ ] If required measurements are unknown, show incomplete analysis and component availability. Do not silently replace missing evidence with a confident 0 or rescaled 100.
- [ ] Score A-vocals/B-backing and the reverse. Add custom stem selection after both standard orientations render correctly.
- [ ] Show why a candidate is difficult: required pitch shift, stretch percentage, sections without matching space, uncertain key and mismatched meter.
- [ ] Test tie-breaking, repeated runs, rounding near thresholds and score-version cache invalidation.

**Exit:** Every score has visible inputs and does not imply a measured quality probability.

## M03 — Order-preserving arrangement planning

- [ ] Make failing tests where naive assignment would put chorus 2 before verse 1; preserve source vocal order in the expected result.
- [ ] Quantize source sections to reviewed downbeats or user-confirmed anchors. Keep generic A/B section labels distinct from manually confirmed verse/chorus labels.
- [ ] Implement monotone dynamic programming over ordered vocal sections and available backing spans. Cost terms cover label, bar length, energy and excessive repetition; enforce locks as hard constraints.
- [ ] Loop or trim accompaniment at valid bar boundaries. Do not cut/repeat lyrics to satisfy a backing duration unless the user explicitly edits that placement.
- [ ] Fill uncovered target bars with instrumental sections, then mark unresolved transitions for review rather than inventing a musically seamless bridge.
- [ ] Store a source-frame and target-tick mapping for every placement; derive seconds from tempo/meter in one place.
- [ ] Validate edited plans for source bounds, crossing anchors, forbidden overlaps, excessive total duration, orphan locks and invalid section references.
- [ ] If manual locks conflict, return the conflicting placement IDs. Never silently unlock or move them.
- [ ] Test repeated choruses, unequal section lengths, intro/outro omission, empty vocal spans, mixed meter and an impossible lock set.

**Exit:** Vocal order is preserved, backing edits are inspectable, and no source time is lost through rounding drift.

## M04 — Tempo, key and transform safety

- [ ] Implement fixed-grid stretch first, followed by variable-tempo piecewise mapping through plan 01's monotonic anchors and cell/time budgets.
- [ ] Prefer vocal shifts within two semitones. Flag larger shifts; require an explicit plan-bound override above three semitones. Never exceed the DSP service's validated hard range.
- [ ] Prefer one-source stretch within the report's proposed ±8%; otherwise propose a midpoint tempo and show both ratios. Ratios outside the DSP safety band block automatic rendering.
- [ ] Validate source/target duration-ratio direction consistently; unit tests must catch reciprocal-ratio mistakes.
- [ ] Preserve stereo relationships and exact target frame counts; compensate known transform latency before placement.
- [ ] Bake variable warps into new immutable assets for the current editor. Its existing `originalBpm` handles only uniform warp and cannot represent a tempo map.
- [ ] Keep transform recipes and original source references so users can re-render after edits; disable duplicate editor warp on already transformed audio.
- [ ] Test impulse alignment, sustained tones, drifting click tracks, silence, phase-inverted stereo, clipping and extreme user overrides.

**Exit:** The displayed plan and rendered audio use the same bounded time/pitch transformations.

## M05 — Transitions, mixing and preview

- [ ] Render crossfades and bounded gain changes first. Add filter sweeps with an explicit envelope rather than nondeterministic live controls.
- [ ] Implement optional vocal-triggered ducking and conservative EQ carve through shared DSP. Reuse existing EQ concepts; do not add a separate effects framework.
- [ ] Retain dry stems and render processed variants. Mark baked effects and keep neutral editor effects to prevent double ducking/EQ/limiting.
- [ ] Match measured loudness using existing `audio_quality`; handle silence/short clips explicitly. Measure final encoded true peak and report the result.
- [ ] Provide A/B preview for each join, original stems and the complete proposal. Play the same owned render used for export of that revision.
- [ ] Add optional ACE Repaint/Stable inpaint bridges only through the verified source-conditioned integrations from plan 05. A bridge is a separately recorded derived source with model evidence.
- [ ] Re-analyse generated bridges for tempo/key consistency; low-confidence results require audition, not an automatic 'seamless' badge.
- [ ] Test discontinuities, sidechain lookahead/latency, fades at file boundaries, missing stems, cancelled bridge generation and a failed remix that must keep the previous preview.

**Exit:** Users can inspect difficult joins and revert processing without rerunning separation.

## M06 — Editable project and library publication

- [ ] Reuse plan 03's `beat_projects.build_audio_project` helper with typed rendered lanes and provenance, returning `BuiltMusicProject {name,data,provenance}`. Publish through the same idempotent project boundary; do not drop the title or serialize a second incompatible editor schema.
- [ ] Create one lane per selected stem and editable placements; retain original source and transform metadata with the mashup revision.
- [ ] Avoid saving the full processed mix as the only editable lane. A mix-only project does not satisfy the report's requirement.
- [ ] Make final publication idempotent by job/revision identity. Stage files, verify hashes/manifests, register track/project once, then mark done.
- [ ] If publication fails, preserve staged verified outputs and show retry-publication. Do not duplicate library rows on retry or lose an already registered track.
- [ ] Add tests for editor parser round-trip, source deletion during render, duplicate API submission, interrupted publication and changing the plan while an older render finishes.
- [ ] Include both source recordings and model/sample restrictions in the export manifest. Preserve unknown rights; the app license does not grant mashup release rights.

**Exit:** The saved mix plays, its stems open in the editor, and retry/reload cannot publish duplicates.

## M07 — Interface, help and accessibility

- [ ] Add two source decks using existing upload/library selection. Show the selected version, duration and analysis status immediately.
- [ ] Provide orientation controls, per-stem audition, compatibility explanations and target BPM/key overrides.
- [ ] Add a bar/section arrangement view with accessible keyboard operations for move, lock, trim, repeat backing and undo. Do not require drag alone.
- [ ] Put current job activity above history with elapsed time, phase, completed units and measured ETA when meaningful. Suppress stale ETA while loading/separating.
- [ ] Add info cards explaining transposition, stretching, section confidence, bleed, ducking and licensing in plain language.
- [ ] Preserve the saved document across refresh/navigation; responses from old sources/revisions must not replace the active plan.
- [ ] Cover narrow layouts, long titles, empty sources, missing engines, failed analysis, restart and screen-reader status announcements.

**Exit:** A user can inspect, edit, preview and render the arrangement without understanding DSP terminology.

## M08 — Evaluation and integration gate

- [ ] Run synthetic objective checks before held-out music: source order, sample/frame placement, drift, pitch ratio, limiter and deterministic plan generation.
- [ ] Assemble the report's proposed 20-pair listening set from rights-reviewed sources, spanning tempo ratios, key distances, live timing, silence and dense mixes. Keep references outside training.
- [ ] Reanalyse output downbeats; report the fraction within the proposed ±20 ms criterion, including failures and unknown analyses. Do not use the same estimator as unquestioned ground truth; compare manually annotated anchors as well.
- [ ] Run blinded preference against a same-start/no-stretch overlay and collect musical coherence/artifact ratings. The report's 80% preference target remains a study gate, not a feature claim.
- [ ] Record M4 Max wall time, peak memory/disk, source/model revisions, seeds and exact processing settings.
- [ ] Run all targeted tests, generated-contract drift check, full frontend test/build, full backend regressions and strict platform checks from plan 09.
- [ ] Verify legacy queue, voice replacement, library and editor behavior remain intact.

**Verification status:** Plan only. No mashup algorithm, quality score, render benchmark or listening result has been implemented or measured.
