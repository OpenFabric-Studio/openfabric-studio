# Upstream Integration Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` with focused independent work following the repository's collaboration rules. Preserve the approved design and existing fork behavior.

**Goal:** Adopt all reviewed upstream improvements without losing the fork's voice, format, favorites, progress, settings and video functionality.

**Architecture:** Preserve the fork baseline during merge conflict resolution; adapt reviewed upstream behavior with regression tests. Separate audio/editor, metadata/download and library presentation ownership, then review their integration. Backend schemas generate client types; the current selected version/export remains the audio source of truth.

**Tech Stack:** Vue 3, Pinia, strict TypeScript, Web Audio, FastAPI/Pydantic, SQLite, FFmpeg, Vitest and unittest.

## 1. Baseline and history

- [x] Confirm clean fork HEAD `9391186`, remotes and ignored `.worktrees`.
- [x] Create a sync worktree from fork master and fast-forward the already reviewed voice-progress branch.
- [x] Run frontend baseline: `npm test --prefix frontend` (398 tests).
- [x] Begin `git merge --no-commit --no-ff upstream/master`; preserve the verified baseline while resolving conflicting trees.

## 2. Audio transport

Owner: audio implementation agent. Files: `frontend/src/audio/mixerEngine.ts`, `timelineEngine.ts`, `composables/useTimelineEngine.ts`, `views/editor/EditorPage.vue`, `stores/editor.ts` and corresponding audio/editor tests.

- [x] Add failing behavioral regressions for actual-start timing, muted/empty project completion, pre-scheduled bounded audio/MIDI loops, lane-channel identity and stale playback completion.
- [x] Run focused tests and retain evidence of the failures.
- [x] Port timing, loop and channel-ID behavior while preserving all current cleanup, typing and session ownership.
- [x] Verify focused audio/editor tests and strict TypeScript; hand off the editor page/store before accessibility changes.

## 3. Tagged downloads and artist setting

Owner: backend implementation agent. Files: new typed tagging/settings modules and tests, `api/routes_tracks.py`, `routes_audio_exports.py`, `routes_audio_versions.py`, `routes_settings.py`, `db.py`/settings storage as appropriate, `main.py`, `scripts/generate_contracts.py` and generated contract artifacts. Parent owns frontend API parsers and Settings integration after contract handoff.

- [x] Add failing regressions for artist validation/persistence, the exact selected version/export, Unicode metadata, stream-copy equivalence, constrained paths, failure/cancellation/timeouts and temporary-file cleanup.
- [x] Run isolated tests with temporary `OPENFABRIC_CONFIG`, `OPENFABRIC_DATA_DIR` and `SEED_VC_DIR`.
- [x] Implement typed metadata and version/export-specific tagged responses without changing stored audio or existing endpoints.
- [x] Regenerate contracts; verify tests and strict Python typing. Extend CI's checked scope for new typed modules.

## 4. Track feeds and cards

Owner: UI implementation agent. Files: `stores/yue2.ts`, ACE/YuE cards/feeds, new pagination and track-details components/composables, card-specific tests. Parent coordinates WaveformPlayer playback notifications/download API handoff and shared locale composition.

- [x] Add failing tests for rename/reload/reuse preserving style, favorites/sort/date pagination, page deletion/clamping, active/playing card retention and voice/version/progress controls surviving redesign.
- [x] Port title/style separation, validated persisted pagination, structured lyrics/style details, readable card hierarchy and confirmed deletion while retaining batch, compact, version and progress behavior.
- [x] Verify focused feed/card/store tests and strict typing; document which components/events the parent must integrate.

## 5. Accessibility, Home and settings integration

Owner: parent and a focused accessibility agent after the audio handoff. Files: shared dialog composable, help/library dialogs, editor handles, channel strip, Home, header/footer/help, Settings, locale modules and tests.

- [x] Add regression tests before behavior changes: asynchronous dialog close/reopen, nested focus, keyboard bounds, header route preservation, recent-track loading/failure/version controls, selected-download API URLs and artist form request races.
- [x] Implement focus lifecycle and keyboard controls, readable focus styling, Home recent tracks, global help/attribution and artist settings/download integration.
- [x] Keep existing header measurement for draggable panel positioning and all fork routes.
- [x] Use checked-in notices as the license source; link authoritative upstream/vendor notices without inventing uniform binary/model licensing claims.

## 6. Integrated verification and delivery

- [x] Run `npm test` and `npm run build` in frontend.
- [x] Run `python -m unittest discover -s app -p '*_test.py' -v` in backend using three temporary test paths.
- [x] Run contract drift and the exact CI strict-mypy loop for Linux/macOS/Windows, including added typed modules.
- [x] Run desktop tests because backend startup/router registration changed.
- [x] Request independent spec and quality review; fix material findings and rerun affected checks.
- [x] Inspect final merge diff, Git status and preservation checklist. Document actual verification and upstream adaptations.
- [x] Deliver on the fork sync branch under the existing fork publishing authorization, preserving both Git histories. Update the normal checkout only after checks; avoid restarting active model jobs.

## Review corrections

- Preserve blank artist as an explicit override of inherited source metadata.
- Put full escaped metadata in a bounded FFmetadata file instead of argv; reject oversized metadata explicitly.
- Keep active conversion/export details expanded in compact feeds.
- Use real card/player regression tests for voice/format transitions across pages; mocked event-only tests were insufficient.
- Retry the latest cancelled editor activation once after undo changes lane identities; stale requests cannot affect newer playback.
- Clamp the reported trim-slider value to shortened source bounds without changing saved trims.
- Retain existing visibly indicated playback-refresh fallback; download requests capture exact IDs and never substitute files.
- Reconcile successful YuE rename/delete edits with pending history, retaining other tracks and latest-request ownership.

## Delivery evidence

Merge commit `c09faa77648c74248aa82fe0ed6af145665c55d4` retains fork `9391186`
and upstream `5a2f4a4` as parents. It was pushed to
`mchosc/remiqora:sync/upstream-2026-10-01`; the normal checkout uses that branch.
The delivered checkout also passed a fresh strict production build.
Shared `master`, the real library and model processes were not modified.
Local verification counts and limitations are recorded in
[the integration notes](../../upstream-integration-2026-10-01.md).
