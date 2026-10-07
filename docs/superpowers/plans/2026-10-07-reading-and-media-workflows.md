# Reading and media workflows implementation plan

> For agentic workers: use subagent-driven development for independent ownership, then review the integrated changes. The user approved the complete proposed feature set. Commits and pushes are not part of this request.

**Goal:** Improve reuse of completed narration, timing control, inspectable training, support diagnostics and portrait direction without compromising source media or overstating model quality.

**Architecture:** Retain immutable dry recordings and backend-owned source identities. New composition/export settings affect assembled artifacts, not synthesis cache identity. Use generated Pydantic contracts, existing owned subprocesses, persisted jobs and current Vue components. Keep the production LTX pin unless the maintained wrapper and real acceptance cases pass.

**Stack:** Python/FastAPI/Pydantic/SQLite, Vue/TypeScript/Vitest, FFmpeg, optional Apple MLX.

## 1. Audiobook pacing and duration guidance

- [x] Add bounded speaker-change and passage-gap settings with legacy zero defaults, persisted through migrations and revision checks.
- [x] Retain passage identity and recompute sample-derived offsets when assembling gaps; dry recordings/cache remain unchanged.
- [x] Add exact occupied duration and bounded forecasts keyed to voice/model/language/settings, showing unavailable/approximate states honestly.
- [x] Add accessible translated controls and regression tests for gap placement, repairs, cache reuse, stale changes and estimates.

Ownership: audiobook contracts, persistence, narration, workflow/cue helpers and their existing views/API/locales/tests. Coordinate schema changes with the export worker; no edits to generated.ts, main.py or CI.

## 2. Read-along exports and retained-audio handoff

- [x] Add a durable chapter-first read-along export: MP4, passage-timed SRT/WebVTT and bounded preview, retaining exact selected narration and display/spoken text mapping.
- [x] Use existing bundled fonts, FFmpeg supervision and secure artifact publication; handle cancellation, shutdown, recovery, missing glyphs and stale source revisions.
- [x] Export original display wording only where recorded mapping is trustworthy; never fabricate word timings or regenerate speech for styling.
- [x] Add backend-resolved retained source handoff from speech trials/chapters to Video; preserve consent/provenance, require explicit clipping for timeline limits and do not label soundtrack as lip-sync.
- [x] Add focused components, generated-contract clients and CPU/model-mocked behavioral tests.

Ownership: new audiobook export/handoff contracts, modules/routes/clients/components/locales/tests; video_projects.py only for handoff integration. Request root integration into existing audiobook and speech views.

## 3. Engine controls and private support report

- [x] Add idle-only stop controls for owned engines with active-job exclusion, process identity validation and drained exit.
- [x] Add previewable allowlisted diagnostics JSON, including fallback when backend is unreachable; exclude raw logs, private paths, names, prompts, transcripts, audio and secrets.
- [x] Add translated Settings UI and privacy/liveness/cancellation tests. No automatic upload, telemetry or stop of external engines.

Ownership: settings UI, new support contracts/modules/routes/client/locales/tests, orchestrator stop admission and relevant regressions. Root owns main.py, generated contracts and shared CI edits.

## 4. Portrait direction, training transparency and photo normalization

- [x] Add conservative portrait framing/direction presets, warning that face size guidance is a heuristic and soundtrack alone does not drive lips.
- [x] Show effective trainer recipe, pinned base/engine compatibility, held-out comparison and review status. Never use parameter magnitude as a likeness score.
- [x] Preserve originals and normalize EXIF orientation and ICC colour profiles consistently across accepted still/training/reference paths; test malformed profiles, mirrored rotations and resource bounds.

Root ownership: video direction/training components and contracts/backend review helpers, image normalization and integration, tests/locales/docs.

## 5. LTX compatibility investigation

- [x] Inspect exact 0.16.1 source and pending HQ/control changes; port/test maintained A2V wrapper against exact hashes in an isolated candidate checkout.
- [x] Extend research harness for candidate A2V where supported; no implicit model downloads or production replacement.
- [x] Record actual CPU/GPU evidence and unresolved face/LoRA/portrait-memory limitations. A source port alone does not justify changing the production engine pin.

## Integration and acceptance

- [x] Register routes and owned recovery/shutdown tasks; regenerate frontend contracts after all schema changes.
- [x] Review every source→job→artifact→UI path, old data defaults, consent, stale revisions, cancellation and privacy.
- [x] Run focused behavioral tests during implementation, then frontend `npm test`/`npm run build`, isolated backend full unittest discovery, contract drift check, CI strict mypy on linux/darwin/win32 and desktop tests if startup/package changes affect it.
- [x] Run independent spec/code review, fix material findings and review final diff. Record evidence and limitations in feature docs; leave changes on the topic branch without committing or pushing.

## Verified outcome

All implementation tasks are complete. Production LTX remains 0.15.12; its replacement is conditional on the documented quality/compatibility matrix, rather than inferred from one candidate A2V smoke case. Final evidence: 1,256 backend tests (10 skipped), 1,066 frontend tests, strict frontend build, contract drift check, strict CI mypy scope on Linux/macOS/Windows (162 files each), and 77 desktop tests (one skipped). Independent backend and UI/runtime reviews identified eight issues, all fixed with regression coverage. Candidate 0.16.1 real tiled A2V completed with validated output; no face/lip-sync quality claim. App remains stopped; nothing was committed or pushed.
