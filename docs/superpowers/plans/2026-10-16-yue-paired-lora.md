# YuE paired LoRA implementation plan

> Historical record — HeartMuLa retired on 16 October 2026. Its generation, training and installation instructions below no longer describe supported functionality. See [current music documentation](../../local-music.md).

> **For agentic workers:** Use superpowers:subagent-driven-development and tests
> that fail before implementation. Preserve the existing dirty working tree;
> this task does not authorize a commit, push or long user training run.

**Goal:** One training action creates compatible AR and NAR adapters while
preserving independent generation selectors and older single-adapter jobs.

**Architecture:** One existing durable job owns sequential score extraction,
planner training and acoustic training. Both artifacts are validated before
publication; recovery completes interrupted publication without restarting
training. Use the installed native score model and owned CLI subprocesses.

**Tech stack:** Existing Pydantic/generated TypeScript contracts, SQLite job JSON,
SafeTensors, pinned PyTorch/Comfy trainer, native audio.cpp and Vue components.

## Shared contract

```python
class YueLoraPairSettings(Contract):
    planner_steps: int = Field(default=100, strict=True, ge=1, le=100000)
    planner_learning_rate: float = Field(default=0.00005, strict=True, ge=1e-7, le=0.01)

# MusicLoraTrainingRequest; None preserves historical fingerprints with exclude_none.
yue_pair: YueLoraPairSettings | None = None
# MusicLoraTrainingProgress; include transcribing in its existing stage enum.
target: Literal['ar', 'nar'] | None = None
# MusicLoraTrainingJob; adapter remains the NAR/Heart compatibility alias.
adapters: list[MusicAdapter] = Field(default_factory=list, max_length=2)
```

Paired completion uses the common existing job_id, engine, request_sha256,
dataset_sha256, base_sha256 and max_steps fields, adds planner_steps, and replaces
checkpoint/sha256 with exactly two artifacts containing target, checkpoint and
sha256. Require the targets to be exactly ar and nar. Keep the legacy receipt.

## Task 1: Contracts and durable publication

Files: `backend/app/music_training_contracts.py`, `music_training.py`,
`music_training_runtime.py`, `music_lora.py` and their existing tests.

- [x] Reproduce paired receipt rejection, job-target deduplication, publication
  interruption, cancellation during publication, and historical fingerprint cases.
- [x] Implement the shared contract; allow optional clip lyrics for paired YuE
  while retaining the legacy acoustic-only restriction.
- [x] Validate both files before registration. Deduplicate by job and target,
  require both for done, and recover missing registration idempotently.
- [x] Accept AR-to-NAR step reset, reject old-target/regressive telemetry, and
  preserve singular jobs. Run focused temporary-library unittest modules.

## Task 2: Owned worker, conversion and score extraction

Files: `backend/app/yue_lora_runtime.py`, `yue_lora_convert.py`,
`backend/scripts/yue_lora_worker.py` and corresponding tests.

- [x] Reproduce AR conversion rejection and pair sequencing/receipt failures.
- [x] Add strict planner metadata and native unfused AR tensor conversion using
  the verified projection dimensions and alpha/rank scaling.
- [x] Prepare immutable private excerpts once; preserve supplied matching lyrics.
  For a paired request invoke installed CLI score extraction, require bounded
  usable ABC for every excerpt, then run planner followed by acoustic training.
- [x] Report target-specific progress; verify real completed updates and finite
  weights for both; write a pair receipt only after both conversions succeed.
- [x] Test score failure, trainer failure, cancellation/no second-stage launch,
  subprocess drainage, unexpected tensors and artifact tampering.

## Task 3: Training UI and generator handoff

Files: `ModelLoraTraining.vue`, `TrainingJobProgress.vue`, `TrainingOptionsHelp.vue`,
`musicTrainingSamples.ts`, `generationDrafts.ts`, YuE `GenerateForm.vue`, relevant
locales and tests.

- [x] Reproduce one-click pair request, paired history/export, stage ETA/reset,
  matching clip lyrics, preview-both and comparison-clears-both cases.
- [x] New YuE submissions always include yue_pair. Display conservative separate
  advanced AR defaults beside existing NAR controls, and explain excerpt limits.
- [x] Preserve old history and HeartMuLa behavior. Use generated contracts.
- [x] Run focused frontend tests and strict build after contract generation.

## Task 4: Setup, portable packages and hardware proof

Root owns `music_training_setup.py`, `module_install.py`, portable package worker
and bootstrap, affected tests, contract generation and live verification.

- [x] Reproduce CLI executable-mode loss; preserve permissions for native tools
  and provide a validated repair path for existing owned installation.
- [x] Paired readiness checks score model, spec and CLI without extra model
  downloads. Keep acoustic-only compatibility and expose actionable setup errors.
- [x] Portable packages carry prepared ABC and produce/check both artifacts;
  retain legacy package compatibility and document device evidence honestly.
- [x] Run isolated synthetic native transcription, one-step AR and NAR training
  on MPS, finite/nonzero weight checks, conversion checks and native pair loading.
  Preserve all user data; recheck active jobs before any app restart.
- [x] Run frontend tests/build, full temporary backend suite, generated contracts,
  21 strict mypy platform groups, desktop tests if startup/file layout is affected.
- [x] Review spec compliance, then code quality, inspect final diff and browser
  behavior, update evidence and leave the app available. Do not commit or push.

## Completion evidence — 2026-10-16

- Apple Silicon/MPS: an isolated six-second synthetic recording passed native
  score extraction, one real planner update and one real acoustic update. Both
  checkpoints had finite, changed factors and registered under one job. A fresh
  process recovered the same two adapter IDs without restarting an optimizer.
- Native Metal generation loaded 196 AR and 196 NAR projections together and
  produced a finite, non-silent eight-second stereo 48 kHz WAV. This checks
  format compatibility and execution, not musical quality or convergence.
- Browser: paired settings, the single Start action, help card, legacy NAR
  history, and the two independent generation selectors were inspected. No
  user training or generation was started for the browser check.
- Full backend suite: 2,381 tests, 16 platform/optional skips. After the final
  prerequisite and portable-package cleanup fixes, all affected module/setup,
  paired/HeartMuLa/Stable training and Windows ownership suites were rerun.
- Frontend: 1,719 tests passed; strict TypeScript/Vue checks and production build
  passed. Generated contracts matched. All 21 strict mypy groups passed across
  Linux, macOS and Windows typing paths. Desktop: 87 passed, one platform skip.
- Setup regressions cover actual verification (including a ready runtime),
  missing CLI/spec/model files, fresh installation, preserved existing content,
  concurrent creation, symlink escapes and incomplete staged releases. Temporary
  ASGI checks also exercised plan → job → verifier → model publication.
- Portable package cancellation drains children and descendants on macOS.
  Windows Job ownership has mocked boundary coverage and a Windows-only runtime
  test; actual Windows and CUDA execution remain unverified.
- The original live library's 42 database tables were compared against its
  consistent pre-test backup; only the queue ownership/control row changed on
  app restart. All five dataset records (three in trash), both managed adapters,
  and the existing training job remained. Synthetic proof artifacts stayed in a
  separate recovery library. No commit or push was performed.

Limits: automatic transcription can make musical errors. Long-run training
quality, CUDA and Windows runtime behavior were not evaluated. Exact training
state resume after a stopped or crashed optimizer is unchanged and unsupported.
