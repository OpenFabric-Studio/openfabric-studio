# Quality and reliability implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development and verification-before-completion. Implementation was authorized by Seth's “yes to all”; the subsequent request authorizes committing, merging into main, pushing and starting the local studio for live testing.

**Goal:** Make setup recoverable, add optional local speaker screening, measure export audio, preserve export provenance and investigate the new LTX source without silently changing the production engine.

**Architecture:** Extend existing module and audio-export job registries. Speaker review runs in an owned optional CPU subprocess with immutable accepted-reference snapshots. LTX experiments are separate, offline and source-pinned. Backend schemas generate frontend contracts; local libraries and original media remain untouched by verification.

**Tech stack:** Python 3.12, FastAPI/Pydantic, SQLite/atomic JSON storage, Vue/TypeScript, owned FFmpeg, optional SpeechBrain CPU environment, MLX research harness.

## Approved boundaries

- No automatic paid retries, GPU weight downloads, native secret-store writes, production migrations or running service restarts.
- Speaker cosine similarity is a screening hint, not identity probability. Human reference approval and an explicit calibrated threshold are required; no automatic acceptance or repair.
- GPT-SoVITS does not attest the currently loaded checkpoint. Local saved-audio comparisons remain usable with an explicit unverified-renderer warning; only known model/profile/cast/take changes can invalidate their provenance. No model-switch detection is claimed for external unverified servers.
- Loudness profiles are optional house targets. Measured true peak is distinct from sample peak. Silence/unmeasurable data and failed targets are reported honestly.
- Provenance distinguishes generated, mixed, recorded and unknown content. Source/provider marks survive exports. Metadata and disclosure options are not legal certification.
- License cards show declarations with primary source links, component scope and unknowns; they do not claim blanket commercial permission.
- Keep LTX 0.15.12 production pin until compatibility and measured inference justify an upgrade. Candidate source includes merged preprocessing/unfused changes; low-RAM unfused is explicitly unsupported.

## Task 1 — Download recovery and module license cards

**Owner:** audit_backend_media. Files: module_install.py, module_jobs.py, module_contracts.py, module_catalog.py, module_runtime.py, new module_licenses.py and focused tests; Settings ModuleSetupPanel and locales/tests; new external/patches/yue-model-download-reliability.patch, desktop prepare-resources.js and native patch tests.

- [x] Add regression tests for interrupted body, retry exhaustion, malformed Content-Range, changed object identity, resumable partial data and cancellation.
- [x] Run focused tests with isolated configuration and confirm the intended failures.
- [x] Add bounded backoff/stall recovery only to owned artifact download functions. Persist throttled typed byte/attempt progress; verify final size/hash before atomic promotion.
- [x] Layer the native reliability patch over the existing resume patch; preserve already-managed engines and immutable fixtures.
- [x] Add scoped license declarations with reviewed source URLs and explicit unknowns, and present them before installation.
- [x] Surface optional speaker-review capability/setup instructions in Settings without loading the model or downloading weights.
- [x] Run focused backend/frontend/native desktop fixtures; report commands and actual results.

## Task 2 — Optional local speaker-match review

**Owner:** audit_frontend. Files: new speaker_review modules/contracts/routes/worker/tests; scripts/speaker_review.py, scripts/setup_speaker_review.py, requirements-speaker-review.txt; SpeakerPassageReview.vue, API and locale/tests; narrow read-only snapshot resolvers in audiobook_workflows.py.

- [x] Test encoder fingerprint mismatch despite equal dimensions, accepted-take/reference replacement, cast/profile reassignment, short/silent audio, malformed worker output and cancellation/recovery.
- [x] Run focused failing tests before implementation.
- [x] Implement fixed inspected ECAPA architecture/checkpoint loading with weights_only, no remote YAML or implicit downloads; isolate optional dependencies from the base runtime.
- [x] Bind results to audio hashes, render snapshot, current cast assignment, encoder/checkpoint/preprocessing/package fingerprints. Revalidate before publication and when reading historical results.
- [x] Add explicit listened/approved reference selection and user-calibrated threshold to passage review. Reuse human repair workflows; show stale/suspect/unmeasurable states.
- [x] Supply dependency-only setup instructions and a lightweight capability helper for Settings.
- [x] Run focused backend/frontend behavior tests. Real encoder evidence is separate from mocked lifecycle verification.

## Task 3 — Measured export audio and provenance

**Owner:** fix_midi_timing. Files: audio_encoding.py, audio_exports.py, audio_version_contracts.py, routes_audio_exports.py, new audio_quality and export_provenance modules/contracts/tests; narrow tagging.py changes; audio settings/editor/version UI/API/locales; audiobook_publish.py and video_render.py/video export forms/contracts.

- [x] Add synthetic intersample-overload, clipped/silent signal and actual FFmpeg loudness-target tests, plus immutable-source/cancel/recovery cases.
- [x] Confirm failures, then extend the existing durable registry with analysis and optional two-pass normalization plus post-encode remeasurement.
- [x] Route final editor downloads through captured backend exports and use the same processed file for preview/download.
- [x] Build immutable, hash-bound JSON and readable provenance manifests from server-known sources/renderer receipts; unknown imported content remains unknown.
- [x] Preserve provider/source metadata and watermarks; embed supported short origin metadata and offer an explicit visible label for known generated video.
- [x] Test all origin categories, metadata preservation, manifest hashes, immutable publication, source replacement and cancelled work.
- [x] Run focused backend/frontend and real CPU FFmpeg tests; distinguish target failures from successful encoding.

## Task 4 — Separate LTX compatibility experiment

**Owner:** root. Files: video_benchmark.py, scripts/benchmark_video.py, new candidate runner/source manifest and focused tests, benchmark evidence/documentation. Existing production video_engine.py pin remains unchanged unless evidence meets its full acceptance matrix.

- [x] Inspect exact merged candidate source, dependency lock and CLI/LoRA/memory contracts from the upstream repository.
- [x] Add regression tests for unknown source, unsupported low-RAM/unfused, offline isolation, identical fixed settings and owned cancellation.
- [x] Implement candidate-only source verification and explicit memory/adapter experiment settings. Record exact source, model revision, input/output hashes, elapsed time and genuine memory telemetry when available.
- [x] Run a short exclusive offline case using existing cached weights only if no studio/model process is running. Keep candidate source/environment/output outside the configured engine and cache.
- [x] Record incompatibilities and missing evidence. Do not infer face identity or platform support from a CPU fixture or one T2V render.

## Task 5 — Integration and review

**Owner:** root. Files: app/main.py lifecycle/routes, scripts/generate_contracts.py registry, generated.ts (generated only), CI checked-file scope, ROADMAP and user documentation.

- [x] Integrate shared contracts once worker schemas settle; regenerate and check contract drift.
- [x] Integrate speaker lifecycle with startup/shutdown/resource admission and test shutdown ownership.
- [x] Review the combined diff for strict typing, safe paths, source preservation, translated accessible UI, cancellation races and accidental scope.
- [x] Run frontend tests/build, full backend unittest with temporary config/data/modules/SEED_VC_DIR, contract check, strict mypy linux/darwin/win32, and desktop tests.
- [x] Verify UI against synthetic fixture data in a temporary local server; keep the real service stopped.
- [x] Report actual verification and remaining native GPU/model/legal uncertainties. Leave changes on feat/quality-reliability for review.

## Baseline evidence

Before implementation: backend 1,109 tests passed (10 environment/platform skips), frontend 1,020 tests passed in 126 files. Logs are outside Git under /tmp/openfabric-oct6-baseline-*.log. Main's existing UI worktree is preserved separately.

## Final verification — 6 October 2026

- Full backend unittest: 1,190 tests run, 10 environment/platform skips, no failures (154.445 seconds). Temporary config, data, module and Seed-VC roots were used.
- Frontend: 1,037 tests passed in 128 files. A final test-only fixture shape correction also passed its 35-test group; strict TypeScript policy, vue-tsc and Vite build passed afterward.
- Strict CI Python scope: 151 files passed for Linux, macOS and Windows typing paths. Generated contracts matched the backend schemas.
- Desktop: 77 tests passed, one environment/platform skip. Native downloader layering and packaged resources were covered.
- Independent review reproduced and closed stem manifest routing, mixed-content disclosure, selected-file measurement isolation and missing video target notices. Actual CPU media/recovery fixtures verified the fixes.
- Temporary browser verification exercised module license cards, audio target saving, speaker comparison and result persistence after reload. Fixture servers and the owned browser were stopped.
- Three short LTX renders validated decode, geometry and duration; 38 selected upstream preprocessing/adapter tests passed. See the retained benchmark reports for measured limits.

At this pre-integration verification, the real studio service was stopped and main was unchanged; implementation was uncommitted on `feat/quality-reliability`. Optional speaker dependencies and weights were not installed, so real voice-match accuracy and native clean installation remain unverified. The LTX candidate does not replace or certify the maintained A2V wrapper, trained character likeness or lip synchronization.
