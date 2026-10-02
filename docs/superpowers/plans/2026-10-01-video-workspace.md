# Video Workspace Implementation Plan

> For agentic workers: use the subagent-driven-development workflow for the independent backend, engine, and frontend domains. Preserve all existing uncommitted work; no commits or model downloads are requested.

**Goal:** Implement the approved video review: reliable saved projects, direction/reference controls, compact editable storyboard, musical markers, short fixed-seed previews, reusable shot variants, recovery, and useful exports.

**Architecture:** Keep local FastAPI job ownership and the existing video engine. Store typed project documents and assets atomically under the video library, using revision checks and validated source/model/settings fingerprints. Existing video jobs remain readable. Generate frontend types from backend Pydantic contracts; use existing Vue components and style tokens.

**Tech Stack:** Python/Pydantic/FastAPI, existing ffmpeg/ffprobe and MLX engine, Vue/TypeScript/Vitest.

## Ownership

- Backend video worker: `backend/app/video_jobs.py`, `backend/app/api/routes_videos.py`, new `backend/app/video_contracts.py`, `backend/app/video_projects.py` and behavioral tests. Define/export the public contracts early for frontend integration.
- Engine worker: `setup_video.sh`, new setup/preflight helpers, compatibility wrapper, musical-analysis helpers and tests. Do not edit the backend worker's files; provide callable typed helper boundaries.
- Root: frontend video workspace/API/tests, generated-contract integration, video locale keys, CI, documentation, integration and independent verification.
- Voice worker owns separate voice files; do not overwrite its locale section or contracts.

## Task 1: Recovery and artifact correctness

- [x] Add failing CPU fixtures for stale polling, competing writes, hard-parent crash, invalid media publication, publication interruption, timing overlaps, and single-shot tails.
- [x] Run the fixtures with temporary config/data paths and confirm the reviewed failures.
- [x] Make GET polling read-only; reconcile at lifecycle startup under serialized ownership. Use unique atomic metadata writes and preserve/adopt validated completed output.
- [x] Track owned process identity safely and enforce backend resource admission, including externally running music/training work. Drain cancellation/shutdown and hard-parent-loss children.
- [x] Validate clip/final streams, dimensions, fps, decodability and expected duration; trim on frame-aligned timeline boundaries using one assembly policy.
- [x] Run video and lifecycle regression suites against CPU fixtures.

## Task 2: Engine settings and setup

- [x] Add compatibility tests for effective refinement settings, A2V tiling and the pinned engine signature.
- [x] Implement effective 1–3 refinement limits, honest compute preset labels, checked memory controls, pinned engine/model/encoder provenance and a required-file manifest.
- [x] Add setup readiness/preflight showing cached/remaining bytes, free disk, hardware/engine compatibility, actionable setup instructions and warnings.
- [x] Preserve modified checkouts and unrelated environment configuration. Do not download replacement models during automated tests or this implementation.
- [x] Add bounded CPU musical analysis returning waveform peaks, energy/onset markers and confidence with manual correction available; avoid describing heuristic sections as verified lyric alignment.
- [x] Verify helper tests, syntax, strict Python scope and installed parser compatibility.

## Task 3: Typed saved projects and shot variants

- [x] Add failing tests for revision conflicts, source changes, reference upload bounds/path escapes, reusable previews, retry/resume and deterministic seeds.
- [x] Implement typed project create/list/get/update/delete/duplicate, reference asset upload/serving, preview/render/adopt/approve operations and explicit export settings. Keep the API shapes in `video_contracts.py`.
- [x] Persist reviewed shot settings, direction, references, markers, seeds, variants and approvals independently of browser state. Validate source/settings/model fingerprints before reusing clips.
- [x] Add a deterministic cover-art motion/visualizer path with original-song audio and post-render title/lyric overlays using explicit editable timing. Avoid claiming automatic lyric alignment.
- [x] Expose an opt-in model comparison selector only for compatible installed packs, with unavailable choices explained; never silently download tens of GB when selecting it.
- [x] Run API/storage/job regressions and generated-contract checks.

## Task 4: Vue workspace

- [x] Add failing mounted tests for persistent drafts, delayed analysis after changing songs, step progression, preview requests, variant approval, ripple editing and field-local errors.
- [x] Replace the expanded shot form list with Song → Direction → Storyboard → Preview → Render & Export navigation and a persistent project/job summary.
- [x] Add searchable song selection and audio audition, reference controls, compact shot cards/timeline, selected-shot editor, duration ripple mode, duplicate/split/reorder/undo, selective re-analysis preserving locked shots and manual musical markers.
- [x] Add one-shot/selected-shot preview, fixed-seed comparisons, approval, per-shot progress/retry/resume, result posters, filtered/paginated project results and explicit export/download/duplicate actions.
- [x] Guard awaited requests with project/song/session identities; serialize saves and handle revision conflicts visibly. Preserve server-owned jobs through navigation.
- [x] Provide named sliders/progress, selected-control semantics, keyboard navigation, live status/error messages and comfortable mobile targets. Translate both languages.
- [x] Run frontend tests, strict type/build and real desktop/mobile browser interactions against temporary synthetic fixtures.

## Task 5: Integrated verification

- [x] Review all approved requirements against the implemented flow; remove stale/misleading copy.
- [x] Run complete backend/frontend suites, generated drift check, expanded scoped strict mypy, shell syntax and affected desktop tests.
- [x] Run real CPU media export/recovery fixtures; verify browser routes and mock-boundary preview/render operations.
- [x] Check whether bounded GPU smoke is possible. **Skipped:** readiness reported `model_not_installed` for both packs; no model downloads were requested. Perceptual quality, GPU time and peak memory remain unverified.
- [x] Record supported/experimental model behavior, measured verification, remaining empirical quality limits and launch instructions. Review the final diff and report no commits.

## Verification recorded on 2026-10-01

- Full backend suite: 337 tests passed in a temporary library/configuration.
- Full frontend suite: 179 tests passed; strict TypeScript policy, Vue type-check and Vite production build passed.
- Desktop suite: 27 tests passed.
- Scoped strict mypy: 33 files passed; generated-contract drift check, shell syntax and diff whitespace check passed.
- Real FFmpeg CPU cover preview, approval, portrait export with timed text and five-second audio were exercised through the browser. Playback decoded at 252 × 448 with no media error and stopped after navigation.
- Desktop/mobile navigation and persistent voice status were checked; 390-pixel layouts had no page overflow. The cancelled voice fixture retained its recorded elapsed time.
- Independent video review checked lifecycle/security boundaries and found no remaining concrete P0/P1 blockers in its reviewed scope. GPU generation and real voice training were not run.
- Existing uncommitted work was preserved; no commits, model downloads or user-library mutations.
