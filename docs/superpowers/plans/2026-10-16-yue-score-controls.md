# YuE2 score controls implementation plan

**Goal:** Add BPM, key/scale and meter controls for both automatic musical
guidance and safe, explicit editing of existing ABC.

**Architecture:** Extend existing typed request/snapshot contracts and the
bounded backend ABC transformer. Preserve original style/settings in history;
only the native request receives synthesized musical guidance. The Vue form
uses its existing components and an explicit Apply action for supplied ABC.

**Tech stack:** Pydantic/generated TypeScript, existing FastAPI routes,
Vue/Pinia/Vitest, standard-library ABC transformations. No new dependency.

The user approved both paths. Preserve the dirty workspace. Work here because
prior uncommitted integrations are required; no commits, resets or pushes.

## Shared contracts, automatic guidance and persistence (root)

Files: new backend/app/music_score_contracts.py; generation_contracts.py,
yue_contracts.py, yue_jobs.py, generation_library.py and their tests;
frontend/src/api/generated.ts (generator only), generationSnapshots.ts/tests.

- [x] Write/run failing tests for validated optional musical fields, legacy
  defaults, external-ABC conflicts, native prompt mapping and preset/history
  round trips. New fields are bpm, key_scale, time_signature on YueOptions;
  bpm, keyScale, timeSignature on YueGenerationSettings.
- [x] Share MusicKey and MusicMeter literal aliases and a bounded strict
  MusicTempo type. Accept the major/minor spellings supported by the current
  backend parser. Tempo is 20–300; meter is 2/4, 3/4, 4/4, 6/8, 9/8 or 12/8.
- [x] Filter app-only fields before native dispatch. For no external score,
  append readable Tempo/Key/Time signature guidance to the style. Keep the
  submitted original style and explicit musical settings in stored metadata.
  Reject external ABC combined with unapplied guidance fields.
- [x] Update snapshots and legacy preset identity without importing unchanged
  presets twice. Regenerate contracts; verify affected callers and fixtures.

## Existing-score transformation (exclusive backend agent)

Files: backend/app/reference_contracts.py, reference_tools.py,
reference_tools_test.py and reference_routes_test.py if needed.

- [x] Reproduce missing target_key and time_signature support. Extend
  ReferenceAbcRequest with those optional fields using root's aliases.
- [x] Resolve target tonic to the nearest signed transposition, preserving
  major/minor mode; transpose notes and chord symbols. Reject mode conversion
  with a stable typed code. Keep existing semitone/range behavior unchanged
  when no target key is supplied; reject ambiguous combinations as needed.
- [x] Update or insert a meter before K. Do not rebar or alter note lengths.
  Preserve tempo-only keys and key spelling where possible; validate pitches
  and all output bounds. Unsupported notation must not be guessed.
- [x] Test sharp/flat/minor keys, chords, naturals, enharmonics, limits,
  unsupported notation and backward compatibility in temporary roots.

## Interface (exclusive frontend agent)

Files: frontend/src/views/yue2/GenerateForm.vue/test.ts, optional focused
YueScoreControls.vue/test.ts, new locale module if needed and locales/en.ts.

- [x] Reproduce absence of BPM/key/meter controls and complete preset reuse.
- [x] Add the approved compact card with labeled optional controls, honest
  guidance/editing hints and the shared info dialog. Preserve raw ABC editing.
- [x] Reuse api/references.transformAbc for Apply to ABC. Preserve current text
  on errors, stale responses, unmount or changed input. Clear applied edit
  intents after successful Apply; raw ABC now contains the chosen settings.
- [x] Block Create while supplied ABC has unapplied edits. No header-only ABC
  may be sent for automatic planning. Restore optional controls from copied
  params and full presets, resetting omissions on sparse copies.
- [x] Test both generation routes, failure/cancellation, accessible help and
  field labels, preset restoration and raw ABC preservation.

## Verification and review

- [x] Review each domain for spec compliance and code quality; fix findings.
- [x] Run frontend tests/build, backend suite in isolated config/data/module/
  Seed-VC roots, contracts check and strict CI mypy platform loop. Desktop
  checks only if startup, packaging or file layout is affected.
- [x] Verify the final UI in the live app without starting a user job. Check
  active jobs before any backend restart; preserve them if busy. Save browser
  evidence outside Git and report actual checks and hardware limits.


## Verification evidence (2026-10-16)

- Full backend suite: **2,444 tests, 17 skipped**, passed with temporary config,
  library, module and Seed-VC roots. Latest affected regressions after review:
  **82 tests passed** across musical controls, owned YuE jobs, generation
  persistence, ABC transforms and reference routes.
- Final frontend suite: **1,761 tests in 170 files passed**. Strict TypeScript,
  type-policy check and Vite build passed. Desktop: **87 passed, 1 skipped**.
- Generated contracts check passed. All seven CI strict mypy groups passed on
  Linux, macOS and Windows typing paths. A concurrent shared-cache mypy internal
  error was resolved by rerunning the full loop with isolated per-platform
  caches; no typing check or code rule was suppressed. All seven changed
  backend production files also passed the final three-platform focused loop.
- Independent spec and quality reviews found and fixed implicit note-length
  changes during meter edits, unsupported chord spellings remaining in their
  old key, blank ABC handling and partial invalid browser number input. The
  final review found no remaining blockers.
- The live YuE generation was allowed to finish. Immediately verified training,
  generation, queue, setup and video idle before gracefully restarting the
  owned source launcher. Restored the previously active YuE engine afterward.
- Browser verification used only a synthetic ABC draft: C major to D major,
  100 BPM, 4/4 to 2/4, with Cmaj7/G7 becoming Dmaj7/A7 and L:1/8 inserted to
  preserve note lengths. The real backend updated the draft, cleared applied
  edit intents and displayed the success notice. No music generation, model
  download or training job was started during these checks. Reloaded the test
  tab to leave a clean form afterward.
- Browser evidence is outside Git:
  `/Users/seth/Documents/OpenFabricStudioData/live/recovery/yue-score-controls-2026-10-16/applied-score.jpg`.
- Scoped final review and `git diff --check` passed. Existing unrelated work was
  preserved. No commit or push was performed.

**Limits:** Automatic musical choices are prompt guidance; adherence and audio
quality were not measured. Existing-score editing retains the deliberately
bounded single-voice major/minor ABC subset. Mode conversion and rearranging
bars are outside this change; unsupported transformations refuse safely.
