# Video workspace implementation plan

**Goal:** Make Video project setup, shot review and output understandable without changing engine/storage behavior.

**Architecture:** `VideoPage.vue` keeps the existing `useVideoWorkspace` ownership and action gates. A focused Direction component edits the same typed `VideoDraft`, the existing project library becomes a compact rail, and the page organizes the five-stage workflow and creation form. Use generated API types and current theme tokens.

**Tech stack:** Vue 3, Vue Router, Pinia, vue-i18n, strict TypeScript, Vitest/happy-dom.

Implementation is authorized by the user request. Continue on the current topic branch without backend mutations or live generation. Independent owners use exclusive files, and root integrates/reviews. The user explicitly authorized committing and pushing after the completed redesign was reviewed.

- [x] Root: add failing page regressions to `VideoPageWorkspace.test.ts` for creation/cancel preserving a selected project, gated stages, source recovery and focus. Adapt old project-manager expectations to the always-visible desktop library/mobile disclosure requirement.
- [x] Direction owner: create `VideoDirectionPanel.vue`, its focused tests and `locales/videoDirection.ts`. Use typed draft v-model plus typed readiness/references props and analyze/continue/upload/duplicate events. Add approach cards, conditional controls, advanced/setup disclosures and preserve action limits/gates. Root owns integration/imports.
- [x] Library owner: update only `VideoProjectLibrary.vue`/test and `locales/videoLibrary.ts` for compact rows, clear source/status, primary open action, secondary actions/disclosure, translated labels and preserved filters/pagination/deletion focus/confirm behavior.
- [x] Root: update `VideoPage.vue`/`locales/videoExperience.ts`/`locales/en.ts` for project rail, new-project form, saved-source context, guided stage navigation and panel focus. Keep draft/save/job ownership in the existing composable.
- [x] Root: organize storyboard editing and previews, hide ignored image-mode controls, disclose markers/secondary controls, and separate render/approved export/result regions with honest disabled reasons. Extend meaningful page regressions first.
- [x] Root: reproduce raw error display in a focused `api/videoErrors.test.ts`, then sanitize unknown errors in `api/videos.ts`, retaining known public codes. Translate the known metadata warning rather than exposing its internal code.
- [x] Root: run focused Video checks, full `npm test`, `npm run build`, generated-contract and diff checks. Address actual failures without type escapes.
- [x] Independent reviewer: inspect integrated routing/focus, state/action gates, conditional controls, type safety, accessibility and existing data preservation.
- [x] Root: verify real Video Song/Direction/Storyboard/Preview/Export and library at desktop and 375px widths using read-only navigation/disclosures. Save screenshots, reset viewport and leave final app open.
- [x] Root: record verification, review final scoped diff and confirm unrelated work remains intact. Leave work uncommitted.


## Verification — 2026-10-02

- Behavioral regressions were observed failing before implementation, including new-project isolation, source playback cleanup, selected-project deletion cleanup, delayed project-load focus, async creation focus, image-shot description repair and public error handling.
- Final `npm test` from `frontend/`: 96 files and 820 tests passed.
- Final `npm run build` from `frontend/`: strict type policy, vue-tsc and Vite production build passed.
- `backend/.venv/bin/python backend/scripts/generate_contracts.py --check` and `git diff --check` passed.
- Independent final review reported no remaining important findings after audio ownership and focus fixes. Backend schemas, composable job ownership, model engines and storage were unchanged.
- Live browser checked Song, Direction, Storyboard, Preview, Export, library disclosure and opening/cancelling the new-project form at desktop and 375px width. Inspected phone pages had matching document/client widths, with stage overflow contained locally. Temporary viewport override was reset. A clean reload and Direction navigation produced no new captured console errors or warnings.
- Screenshots: `.superpowers/video-workspace-2026-10-02/desktop-direction.png` and `mobile-new-project.png` (local ignored artifacts).
- No user projects were created, edited, generated, approved or deleted during live verification. Existing projects report changed/missing source songs; the UI preserves the restriction and exposes recovery. GPU generation, backend regression and desktop packaging checks were not run for this frontend-only change.
- Final scope consists of Video components/tests/translations, Video error sanitization and these design/plan documents. The user subsequently authorized committing and pushing this scope on the existing topic branch; unrelated baseline work was preserved.
