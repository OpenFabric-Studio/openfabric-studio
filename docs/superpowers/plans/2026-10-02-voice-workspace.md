# Voice workspace implementation plan

**Goal:** Implement the approved A workspace using the existing voice workflows and API contracts.

**Architecture:** VoiceClonePage owns validated route-backed modes and keeps visited workspaces mounted. SingingVoiceWorkspace owns singing selection, preparation, model overview and job controls. Speech and Audiobooks each own their library, editor, requests and draft state. No backend contracts or dependencies change.

**Tech stack:** Vue 3, strict TypeScript, vue-router, vue-i18n, existing Tailwind tokens, Vitest/happy-dom.

User authorization covers implementation. Do not commit, publish or touch user media. Existing uncommitted files stay intact. Independent file owners may work in parallel; root reviews the integrated result.

## 1. Singing and mode shell (root)
Files: VoiceClonePage.vue, SingingVoiceWorkspace.vue, singingNavigation.ts, VoiceJobSummary.vue, VoiceOverview.vue, singingWorkspace.ts locale, en.ts integration, VoiceWorkflow.test.ts, VoiceWorkspace.test.ts, VoiceStudio.test.ts.
- [x] Add regression checks for mode draft retention, route selection and Back, ready/new/active entry, published model selection, confirmed deletion, and excluded source provenance. Run focused tests and confirm the intended failures.
- [x] Split the existing singing implementation into SingingVoiceWorkspace. Keep review/comparison instances mounted as steps change. Shell mounts visited modes once and uses v-show subsequently.
- [x] Route modes accept only singing/speech/audiobooks; stage accepts overview/files/samples/coverage/build/compare. Unknown or missing voice falls back to a valid library item; route changes select without triggering requests from old sessions.
- [x] Sidebar has a labelled search, selectable item buttons and new action; compact keyboard stage tabs. Ready voice opens Overview; active preparation opens its current stage, active training opens Train, and new voice opens Sources. Poll updates never reset stage.
- [x] Overview uses generated model metadata and published dry reference URL, existing setActiveModel API and useForSongs. It never labels a dry reference as generated audio or comparison complete.
- [x] Completed job details collapse. Active/failed/cancelled jobs remain visible with existing cancel/retry. Activity emits a translated string to the shell so hidden-mode work remains discoverable.
- [x] Compare enabled saved source filenames to built_from as sets for the source-change warning; no guesses about unknown old provenance.
- [x] Confirmation uses existing useDialogA11y focus ownership, identifies selected item, and guards duplicate/stale delete completion.
- [x] Run singing regression tests and inspect any changed expectations for actual UX requirements.

## 2. Speech workspace (speech owner)
Files: VoiceProfilesPanel.vue, VoiceProfilesPanel.test.ts, locales/speechWorkspace.ts.
- [x] Test create on demand, consent, selection, named delete confirmation, mock/installed/api_reachable statuses independently, duplicate trial and late completion after teardown/selection.
- [x] Implement searchable sidebar and selected profile synthesis editor. Keep draft values across shell mode switches. Expose activity event [message: string] while a request is pending or errored.
- [x] Use AbortSignal and generation checks; never apply a prior profile trial to another profile. Preserve successful work at backend. No invented playback route or raw internal error/path display.
- [x] Run focused tests. Send root locale object speechWorkspaceEn for en.ts integration; do not edit shared files.

## 3. Audiobook workspace (books owner)
Files: AudiobooksPanel.vue, AudiobooksPanel.test.ts, locales/audiobookWorkspace.ts.
- [x] Test saved books separate from creation, chapter/narrator draft persistence, no duplicate create/retry, no stale jobs after selection/unmount, polling teardown.
- [x] Implement searchable sidebar, on-demand creation, chapter creation fields/narrator group, selected book jobs, actual chapter playback and existing export/retry.
- [x] Replace overlapping setInterval with owned createPollingLoop or sequential timer, abort/generation guards. Activity event [message: string] announces pending/background/error state to shell.
- [x] Run focused tests. Send root locale object audiobookWorkspaceEn for en.ts integration; do not edit shared files.

## 4. Integration and delivery (root + independent review)
- [x] Read all changed files and inspect final diff for type, security, data and scope effects.
- [x] Run npm test and npm run build from frontend. Fix all newly introduced and affected baseline failures. Check generated contracts without modifying schemas.
- [x] Independent reviewer checks approved spec, async ownership, accessibility and regressions; resolve important findings.
- [x] Integrate only owned files into the original workspace with preimage checks; preserve .gitignore and speech fixture.
- [x] Inspect real app desktop and narrow layouts, keyboard navigation, mode draft retention, singing model and reference display. Never delete or train user voices during verification.
- [x] Prepare the handoff with implemented changes, actual checks and remaining engine/platform limitations. Leave changes uncommitted.


## Verification record — 2026-10-02

- Final frontend suite: 91 test files and 710 tests passed.
- Final frontend build: strict type policy, vue-tsc and Vite production build passed.
- Generated API contracts: `backend/.venv/bin/python backend/scripts/generate_contracts.py --check` passed without schema changes.
- Independent review resolved navigation, stale focus and lifecycle findings; no critical or important findings remained. Final diff whitespace check passed.
- Live browser: desktop and 375px layouts, keyboard mode navigation, named deletion confirmation and Escape focus return, voice/stage reload persistence, Speech/Audiobook draft retention and published dry reference playback verified. Hidden-mode playback pauses. Coverage no longer widens the document on a narrow screen. Browser error log was empty.
- Original workspace integration used preimage checks. Existing `.gitignore` edits and the speech fixture were preserved. Changes remain uncommitted on `feat/voice-clone-ui`.

No training, speech synthesis or saved-book generation/export was invoked in the user's library. Backend, desktop and GPU flows were unchanged and were not tested end to end for this UI task.

Existing API limitations remain: Speech trial audio has no browser-serving endpoint, aborting a client request cannot guarantee server-side synthesis cancellation, saved audiobook chapters have no edit endpoint, and export-only failures cannot be retried through the chapter retry endpoint when every chapter is already complete. The interface does not invent unsupported actions or playback URLs.
