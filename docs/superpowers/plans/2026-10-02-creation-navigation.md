# Creation navigation implementation plan

**Goal:** Implement the requested Create/Tools grouping and Music model selection.

**Architecture:** Preserve current model forms behind a shared nested Music route. Use the same voice root on Voice Clone and Audiobook routes to preserve its visited child ownership. Keep startup as an explicit guarded UI action, reuse existing stores/types/theme and update destination copy.

**Tech stack:** Vue 3, Vue Router, Pinia, vue-i18n, strict TypeScript, Vitest/happy-dom.

Continue on the existing topic branch with prior work intact. User authorization covers implementation. No commits, media changes, backend processes or live model switches.

## Tasks and ownership

- [x] Root: update `AppNavigation.test.ts` to assert Create = Music/Audiobook/Video, Tools = LoRA Training/Voice Clone, passive Music links and correct active state. Observe failures against the prior sidebar.
- [x] Root: add a minimal `views/music/MusicPage.vue` nested-view shell, then add failing selector regressions for explicit startup, duplicate/busy prevention, running model reuse, failed/cancelled navigation, teardown and superseded navigation.
- [x] Root: implement Music's typed model selection and nested routes in `router.ts`. Preserve existing route names and legacy URLs, and test actual route records with mocked engine/model boundaries.
- [x] Root: simplify `AppSidebar.vue` to route navigation; move Voice Clone to Tools, add Music/Audiobook labels and a book icon, retain LoRA gating and foldable/mobile behavior. Update Home's menu guidance and tests.
- [x] Focused voice agent: adapt `VoiceClonePage.vue` and its tests for audiobook-only presentation and two voice modes, legacy bookmark migration, cross-route drafts and background ownership. Update voice/audiobook copy; change `SingingVoiceWorkspace.vue` only where route/activity ownership needs it.
- [x] Root: inspect the existing offline-start action for duplicate/teardown ownership and safe errors; reproduce any affected startup regressions before making a narrow correction.
- [x] Root: integrate and run focused navigation/Music/voice/Home tests. Run `npm test`, `npm run build`, generated contracts check and `git diff --check`.
- [x] Independent reviewer: review scoped integrated source and evidence for routing, ownership, correct user-visible labels, type safety, accessibility and preservation.
- [x] Root: verify real Home/menu/Music/Audiobook/Voice Clone navigation at desktop and phone widths without switching a live engine; save a screenshot, reset viewport and leave the app open.
- [x] Root: review the final diff, compare prior-file preservation outside the authorized edit list, record actual checks and leave changes uncommitted.


## Implementation and verification evidence

- Sidebar now matches the requested Create/Tools order. Music owns ACE-Step 1.5/YuE2-3B selection with translated status, explicit engine-switch guidance and guarded starts. Audiobook has its own destination; Voice Clone retains Singing/Speech. Existing legacy URLs and route names remain supported.
- Observed failing regressions before implementation: sidebar/model-selector behavior, duplicate and busy offline starts, startup after teardown, public error sanitization, delayed singing library responses while Audiobook is visible, and subsequent voice-mode restoration after a bare route round trip. Corrections preserve explicit bookmark/history behavior, ongoing actions, draft ownership and hidden-audio pause behavior.
- Final `npm test`: **781 tests passed in 94 files**. Final `npm run build`: strict type policy, `vue-tsc -b` and Vite production build passed (305 modules).
- `backend/.venv/bin/python backend/scripts/generate_contracts.py --check` and `git diff --check` passed. No API schemas changed.
- Live browser verification: exact sidebar groups/order, 64px folded navigation with accessible labels, passive legacy YuE redirect, Music model selection display, separate Audiobook heading and two Voice Clone modes. At a 375px viewport Music, Audiobook and Voice Clone had no horizontal overflow; mobile drawer navigation closed correctly. No browser error logs.
- Live verification used navigation only: ACE-Step remained running and YuE remained stopped. No live engine switches or generation were performed. Backend, GPU and desktop packaging flows were not affected or exercised.
- Compared the 46 pre-existing changed files with the pre-task SHA-256 manifest: 35 unchanged (after removing only the two new Music namespace additions from `en.ts`); the other 11 are authorized scoped edits. No unexpected prior-file changes.
- Screenshots: `.superpowers/creation-navigation-2026-10-02/music-final.png`, `mobile-music.png` and `mobile-menu.png`. Viewport override reset; final Music page left open. Changes remain uncommitted.
- Independent integrated review concluded no remaining important findings after correcting the two singer-state regressions; reviewed routing, async guards, component ownership, accessibility, copy and meaningful tests.
