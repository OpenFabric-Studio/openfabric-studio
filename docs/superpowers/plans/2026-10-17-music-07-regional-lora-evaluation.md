# Regional LoRA Data, Training and Evaluation Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans. Keep data curation, implementation and independent review ownership separate. No training, download, publication or commit is authorized merely by the existence of this plan.

**Goal:** Produce optional, evidence-backed regional adapters that improve over the base model on held-out recordings, without contaminating training or claiming unsupported rights.

**Architecture:** Reuse the existing ACE-Step and Stable Audio training workspaces, dataset library, caption jobs, adapter registry, preview/comparison links and safe export. Add a typed dataset/provenance and evaluation layer around them. Train separate traditions and preserve base-model compatibility rather than creating one vaguely 'Asian' adapter.

**Tech Stack:** Existing Pydantic training contracts, persisted Python jobs, native ACE/Stable trainers, source manifests and content hashes, Vue training pages, optional separately installed evaluation tools.

---

## Dependencies and evidence boundary

Report sections 6.7, 6.9, 7.5, 8 and phase 5. Depends on plans 00/03/04 for rights, rendering and preset versions. Data sourcing and musician review can begin earlier; no adapter quality claim is allowed until evaluation is complete.

Existing modules to inspect before changing behavior:

- `backend/app/music_training_source_contracts.py`, `music_training_sources.py`: selected recordings and saved dataset sources.
- `backend/app/training_caption_contracts.py`, `training_captions.py`, `training_caption_runtime.py`: durable analysis/caption jobs.
- `backend/app/music_training_contracts.py`, `music_training.py`, `music_training_runtime.py`: owned training jobs and paired YuE behavior; preserve it without using YuE for the report's default permissive regional-adapter route.
- `backend/app/stable_lora_contracts.py`, `stable_lora.py`, `stable_lora_runtime.py`, `stable_lora_setup.py`: variant-specific Stable training and publication.
- `backend/app/ace_lora.py`, `ace_lora_contracts.py`: existing ACE native workflow.
- `backend/app/music_lora.py`, `music_lora_names.py`: managed adapters and duplicate-name validation.
- `backend/app/lora_datasets.py`, `lora_dataset_contracts.py`, `lora_export.py`: saved datasets, records-only trash handling and destination safety.
- `frontend/src/views/music/ModelLoraTraining.vue`, `TrainingJobProgress.vue`, `TrainingCaptionAnalyzer.vue`, `MusicLoraPage.vue`, and `frontend/src/views/ace-step/LoraTrainingPage.vue`: existing UI.

Proposed additions: `backend/app/music_dataset_evidence_contracts.py`, `music_dataset_evidence.py`, `music_evaluation_contracts.py`, `music_evaluation.py`, their route/test modules, `frontend/src/api/musicEvaluation.ts`, `frontend/src/views/music/MusicEvaluationPanel.vue` and dataset-review cards in existing training pages. Introduce these only for responsibilities the existing modules cannot own cleanly.

## T01 — Establish source rights and inventory

- [ ] Create a dataset inventory for each tradition/instrument/articulation with source ID, original file hash, author/performer, acquisition date, original filename, recording provenance, license/permission evidence and permitted uses.
- [ ] Distinguish listening/reference permission, training permission, redistribution of source samples and redistribution of derived adapters. One does not imply the others.
- [ ] Recheck exact VCSL, commissioned recordings, per-file Freesound CC0, CdM gamelan and Jingju collection sources. For Jingju, identify and exclude or separately clear the report's stated third-party SVAD subset; do not assign one license to an entire mixed-origin collection.
- [ ] Keep CCMusic NC/ND, Saraga NC and other restricted/unlicensed sets outside this default commercially oriented workflow. Explicit research experiments remain separately labeled under plan 08.
- [ ] For commissioned sessions specify recording/sample distribution, adaptation, model training and adapter distribution scope; retain signed/private evidence outside Git and share only references/hashes.
- [ ] Record practical asset gaps by instrument/technique. Treat the report's 2–4 hours per culture as a proposed starting budget to validate, not an established minimum or guarantee.
- [ ] Add validation tests for missing rights, conflicting permitted/blocked uses, changed source bytes, copied evidence with a different scope and accidental inclusion of evaluation-only audio.

**Exit:** Each training recording has scoped evidence; unknown/restricted records are excluded from this dataset build with an actionable reason.

## T02 — Dataset manifests and leakage-resistant splits

- [ ] Define immutable dataset revisions containing source hashes, sample/excerpt offsets, preprocessing parameters, captions, tradition/instrument/articulation tags, preset/render versions for synthetic data, and evidence references.
- [ ] Keep full original recordings and dry sources; derived crops have parent IDs and exact source frame spans. Never overwrite originals while normalizing or trimming.
- [ ] Deduplicate by recording identity/content hash before splitting. Keep all excerpts and render variants of one source in the same split.
- [ ] Allocate the report's proposed 15% holdout by independent recordings, not randomly selected crops. Stratify by instrument/technique and separate performers/sessions where sufficient data exists.
- [ ] If there are too few independent examples, mark evaluation insufficient; do not create a nominal holdout that leaks the same recording into training.
- [ ] Save a deterministic split seed, manifest hash and exclusion reasons. Repeated construction with unchanged sources yields the same split.
- [ ] Add tests for crop siblings, near-duplicate declared takes, synthetic seed variants, renamed files, missing source files, interrupted manifest writes and concurrent edits.
- [ ] Extend the saved-dataset picker/review view instead of adding a second dataset manager. Keep rename/trash/restore and records-only clear-trash behavior intact.
- [ ] Show counts, independent-source counts, duration, disk forecast, train/held-out separation and rights blockers before preparation/training.

**Exit:** A dataset revision can be reconstructed and its evaluation audio cannot enter training through an alternate filename or crop.

## T03 — Structured captions and review

- [ ] Define bounded tags for instrument, technique, scale/tuning, measured or declared tempo, tradition, arrangement category and mood. Unknown values stay unknown.
- [ ] Reuse automatic caption jobs for initial suggestions. Source filenames and artist names are not evidence of musical content.
- [ ] Merge only blank/unreviewed fields by default; preserve manual captions and review revisions. Show which values came from audio analysis versus user declaration.
- [ ] Add source-aware caption templates, for example: `instrument: guzheng; technique: glissando; tuning: named source table; tempo: measured/declared; arrangement: fusion`. Do not imply a verified traditional classification from an AI caption.
- [ ] Require human review of cultural/theory labels before the dataset is marked reviewed for a release evaluation.
- [ ] Validate that synthetic renders list their sample manifests and generator/preset versions; generated audio does not create new real-instrument timbral evidence.
- [ ] Cover batched captions, interrupted worker reuse, edited captions during an active request, missing engines, loading failures and partial item completion.

**Exit:** The dataset carries usable descriptions without erasing edits or laundering uncertain labels into facts.

## T04 — Base-model selection and reproducible run design

- [ ] Begin with a short Stable Audio Small Music integration check if its installed training/runtime is ready; compare against ACE-Step as a separate base. Re-read exact current code/weight/encoder terms and platform capability.
- [ ] Use model-specific preprocessing and adapter metadata. Never share a Stable adapter with ACE, YuE or another Stable variant.
- [ ] Create one named dataset/adapter family per tradition and base. Use existing `music_lora_names.py` validation; a new run must not silently reuse a previous adapter name or output directory.
- [ ] Define a staged rank 8 → 16 → 32 experiment, not a three-job automatic full training sweep. First run a minimal pipeline smoke, then one measured baseline budget, then justify larger ranks from evaluation.
- [ ] Fix source revision, captions, split, optimizer settings, training seed, base/encoder/decoder hashes and preprocessing version for each run. Save exact supported parameters rather than copying fields from another engine.
- [ ] Display expected duration and disk ranges only after a measured representative run. Include loading, encoding and save phases separately from step ETA.
- [ ] Include dataset duration, excerpt policy, effective steps/epochs, trainable parameter count, device, saved checkpoints and last-progress timestamp in the run details.
- [ ] Cover invalid base/adapters, absent or corrupted training weights, output-folder conflicts, low disk, preprocessing cache invalidation and model-admission conflicts before long training begins.

**Exit:** A user can explain which recordings, base and settings produced an adapter and why a larger run is justified.

## T05 — Training lifecycle, resume and publication

- [ ] Reuse current job polling/reconnect and per-item preparation recovery. Browser/network loss must not stop a running native training job.
- [ ] Maintain distinct actions for reconnecting to a live job, reusing completed preparation, fresh training and verified full-state training resume.
- [ ] A fresh run must use a new workspace and cannot discover a prior optimizer/checkpoint implicitly. Preserve old runs and adapters.
- [ ] Enable crash-resume only for a trainer path proven to restore optimizer/scheduler/RNG/step and validate identical model/data/settings. Until then, show the explicit unsupported state; checkpoint files alone are not proof of resumability.
- [ ] If full-state resume support is implemented, add forced-crash tests comparing continuous versus interrupted training at the supported tolerance, wrong-manifest rejection and no cross-run checkpoint discovery.
- [ ] Cancellation must terminate descendants, await completion and preserve valid checkpoints/labels/tensors. Unverified worker exit quarantines the device/workspace instead of freeing it optimistically.
- [ ] Register a completed adapter only after structural and matching-base validation. Publication is idempotent; interrupted registration can retry without duplicate names/rows.
- [ ] Safe export creates a new named subfolder, shows the complete path and refuses existing destinations. Include dataset/evaluation summaries and required attributions without private source recordings/contracts.
- [ ] Test mid-save cancellation, disk-full checkpoints, registry write failure, duplicate publication, restarting the backend during training and an output file changed after validation.

**Exit:** Long runs remain recoverable to the extent actually supported, and publication never overwrites a previous adapter or user folder.

## T06 — Objective evaluation jobs

- [ ] Define an evaluation specification with adapter/base identity, held-out dataset revision, prompts, seeds, duration, device and evaluation-tool/model hashes.
- [ ] Generate matched base-versus-adapter excerpts using the same prompt, seed and settings. Keep generated comparisons out of all training splits.
- [ ] Run instrument/timbre checks only with separately reviewed evaluation model terms. Treat CLAP similarity and instrument classification as diagnostics, not cultural-authenticity or legal-clearance scores.
- [ ] Implement FAD only after checking the embedding model's terms and adequate corpus size. Report sample count, confidence/limitations and embedding identity; no meaningful universal threshold is assumed.
- [ ] Evaluate scale/tuning/timing on known symbolic synthetic fixtures separately from complex music where estimators may fail.
- [ ] Save generation time, training time, peak memory/disk, source revisions and all comparison audio. Failed or unavailable metrics are visible, not omitted from a green summary.
- [ ] Add mocked model-boundary tests, deterministic synthetic metric fixtures, process cancellation, restart recovery and export privacy tests. Unit tests never download embeddings or GPU weights.

**Exit:** Comparisons are reproducible and separate numerical screening from human musical judgment.

## T07 — Listening panels and preset review

- [ ] Recruit practising musicians for each represented tradition. Record their review scope, consent to attribution if any and which exact versions they heard; never invent this evidence.
- [ ] Use randomized blinded base/adapter comparisons. Ask separately about instrument timbre, phrasing, rhythm, coherence, artifacts, cultural appropriateness and preference.
- [ ] Run the report's proposed 5-second culture identification separately for traditional and fusion presets, with listeners from and outside each culture. Record counts and uncertainty around the 80%/60% proposed targets.
- [ ] Keep identity, performance authenticity and stylistic recognizability distinct; none can be established by a filename, CLAP score or a single reviewer.
- [ ] Record rejection reasons and revisions. A negative result is a valid result; do not release an adapter solely because training loss decreased.
- [ ] Retest affected presets/adapters after material sample, tuning, generation or training changes.

**Exit:** A documented held-out preference supports each recommended adapter; missing review is visibly missing.

## T08 — Distribution and maintenance

- [ ] Publish adapters only after scoped distribution permissions, base terms, source evidence and evaluation review permit the proposed distribution. Preserve unknown cases as unapproved.
- [ ] Produce a model card with intended use, limits, exact base/adapter format, training-data summary, exclusions, held-out protocol, measured hardware/run facts and known failure modes.
- [ ] Include hash-bound notices and public attributions; keep raw commissioned recordings and private documents outside public exports unless explicitly licensed for them.
- [ ] Package through existing adapter registration/import/export; test clean import, matching generator selection, strength controls and base comparison on the target Mac.
- [ ] For optional NVIDIA fallback use the existing reviewed training-package workflow and validate source/settings manifests. Do not claim CUDA support from Mac tests.
- [ ] Run targeted training/dataset/caption/registry/evaluation tests and plan 09's required suite before integration.

**Verification status:** Planning only. No new data has been acquired, no regional adapter has been trained and no improvement, license clearance or listening result is claimed.
