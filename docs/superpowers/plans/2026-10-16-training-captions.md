# Automatic training captions implementation plan

> Historical record — HeartMuLa retired on 16 October 2026. Its generation, training and installation instructions below no longer describe supported functionality. See [current music documentation](../../local-music.md).

**Goal:** Describe added YuE/HeartMuLa training audio automatically, without
overwriting manual captions or changing lyrics/instrumental selections.

**Architecture:** Use the installed local ACE-Step encoder and language model's
`full_analysis_only` operation against the same bounded first excerpt used for
training. A backend-owned persisted batch job retains each completed caption
across browser disconnection. The shared training form observes jobs and applies
results only to matching, still-empty samples and unchanged excerpt settings.
No cloud provider, model downloads, dataset-builder mutations or new dependencies.

## Backend

- [x] Add bounded Pydantic source/request/item/job contracts and safe same-origin
  submit/list/get/cancel routes. Generate client types; never hand-edit them.
- [x] Add test-first durable batch ownership, cancellation/shutdown, partial
  results, unavailable/busy analyzer, invalid sources, privacy-safe errors and
  interrupted process recovery. Reuse existing filesystem/source validation.
- [x] Prepare bounded temporary WAV excerpts with owned FFmpeg. Start/init local
  ACE only after verified idle admission, parse actual native analysis results,
  and drain or stop the exact owned instance before releasing resources.
- [x] Integrate job admission, shutdown and strict platform typing in CI.

## Frontend

- [x] Test automatic analysis after file import, library selection, existing
  captions, manual edits while analysis awaits, removed sources, changed excerpt
  settings, cancellation, network reconnection and KeepAlive deactivation.
- [x] Add a local caption card: default-on auto-analysis checkbox, Analyze missing
  captions, progress/current recording, Cancel, safe errors and retry controls.
  Never infer lyrics or instrumental status. Empty captions remain editable.
- [x] Block training/NVIDIA submission during caption work, then use generated
  captions through the unchanged training request. Preserve unrelated drafts.

## Verification

- [x] Focused tests first, then frontend suite/build, backend suite with temporary
  configuration/data/engine paths, contracts and strict mypy platform loop.
- [x] Independent final review, task-only diff inspection, documentation, browser
  verification and screenshot. No training run, commit or push is authorized.

Known tradeoff: the local analyzer needs installed ACE-Step model files and uses
the accelerator. Busy/missing setups must give an actionable state instead of
silently guessing a caption from the file name. Suggested captions need listening
review; they are not ground truth or a claim of musical quality.

## Evidence

- Frontend: 1,829 tests and strict production build passed; contracts check passed.
- Backend: full regression command passed 2,500 tests (19 skipped). After the
  final journal/residency fixes, all 36 caption regressions passed again. The
  complete CI mypy loop passed eight groups on Linux, macOS and Windows paths.
- Desktop: 87 tests passed, one platform test skipped.
- Independent reviews reproduced and then verified fixes for lost POST responses,
  KeepAlive deactivation, completed-result reuse, competing jobs, imports during
  recovery, and maximum serialized caption sizes. Final reviews found no blockers.
- Native Mac batch `472fa651b40d419da6e1cf1b7ae8ded2` analyzed five existing library
  recordings locally (first 12 seconds each), including model load, in about
  64 seconds. All five captions appeared in editable training fields. Model-tab
  navigation retained them. After a full reload, reselecting the same recordings
  restored all five saved captions without another analysis.
- Final native cleanup check `dfd202c2970c42ba9f0ee4676c64e146` started the analyzer,
  captioned one recording and automatically stopped its owned ACE process. The
  completed job was saved and engine support reported stopped, unowned and idle.
- No LoRA training, model downloads, commits or pushes were started by this task.
  The existing native launcher is reused after installed-file preflight; it does
  not enforce process-level offline flags. Caption quality still needs listening
  review; native output is a suggestion about the selected excerpt.
