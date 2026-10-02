# App navigation implementation plan

**Goal:** Implement the requested compact status header and collapsible left navigation.

**Architecture:** `AppShell.vue` owns layout, desktop preference and mobile drawer focus. `AppSidebar.vue` owns existing destinations, explicit model switching and Help. `AppHeader.vue` shows read-only runtime status icons and maintains sticky offsets. A small typed icon component and shared tooltip reuse theme tokens without adding dependencies. Tooltips share the existing top-dialog focus owner; engine switching checks pending navigation ownership before starting.

**Tech stack:** Vue 3, strict TypeScript, Vue Router, Pinia, vue-i18n, Tailwind and Vitest/happy-dom.

The user authorized this reversible redesign. Work in the existing `feat/voice-clone-ui` topic branch, preserve the previous redesign and unrelated changes, and leave all changes uncommitted. No backend or media writes.

## 1. Behavioral regressions

- [x] Add `AppNavigation.test.ts` integration regressions for menu groups, route/history selection, collapse persistence, invalid/unavailable storage, mobile focus/route close and resize cleanup, explicit model selection and duplicate guarding.
- [x] Cover the real header in the same integration suite for compact status-only semantics, unknown state, each runtime state, tooltip interactions and public switch failure copy.
- [x] Adapt `App.test.ts` to isolate its existing document-title test at the new shell boundary. Run focused tests and confirm expected missing-feature failures.

## 2. Implement the shell

- [x] Add typed `AppIcon.vue`, `AppTooltip.vue` and `enginePresentation.ts`. Tooltips support hover/focus/tap, Escape, viewport constraints and lifecycle cleanup.
- [x] Add `AppSidebar.vue`: labelled route groups, current-page markers, engine actions with duplicate guards, discoverable LoRA prerequisite, Settings and existing Help modal.
- [x] Add `AppShell.vue`: validated preference, 224/64px desktop widths, narrow-screen drawer, existing focus ownership, skip link and footer.
- [x] Replace the header's navigation with 44px status controls; preserve header measurement and safe public error feedback.
- [x] Integrate shell in `App.vue` and add `appNavigation.ts` locale strings through the existing English locale.

## 3. Review and verify

- [x] Run focused regressions, then `npm test` and `npm run build` in `frontend/`.
- [x] Independently review navigation, accessibility and async/lifecycle correctness; resolve important findings.
- [x] Inspect live expanded, collapsed and 375px layouts, tooltip focus/tap, keyboard drawer behavior and existing Voice Clone layout. Do not start engines or change user data.
- [x] Review the final diff and record actual checks and limitations. Leave changes uncommitted and provide a screenshot of the running app.


## Verification record — 2026-10-02

- Full frontend suite: 92 test files and 741 tests passed, including 31 new navigation regressions.
- Strict type policy, vue-tsc and Vite production build passed.
- Generated contracts check passed; no API schema or backend changes.
- Independent review: 104 focused tests passed across 7 files, covering shell/navigation, Help/Settings, editor controls and voice-profile dialogs. No remaining critical or important findings.
- Live Chrome: header measured 44px; sidebar measured 224px expanded and 64px collapsed. Collapse survived reload. Keyboard icon labels, status tap/Escape, mobile drawer focus, first-Escape tooltip dismissal, second-Escape drawer close, focus return and scroll-lock release verified. Mobile navigation closes the drawer. Settings, Editor and Voice Clone render with correct active destinations.
- Responsive checks: 375px viewport had a 365px document with no horizontal overflow; expanded sidebar at 1024px had a 1014px document. Default desktop viewport restored. Browser error log was empty.
- Screenshot artifacts under ignored `.superpowers/app-navigation-2026-10-02/`: `expanded.png`, `collapsed.png`, `mobile-drawer.png`.
- Previous Voice Clone implementation and unrelated uncommitted files preserved. Final diff whitespace check passed. Changes remain uncommitted on `feat/voice-clone-ui`.

Actual engine start transitions were tested against mocked boundaries. No real engines were started and no voices, checkpoints or user media were changed during browser verification. Desktop packaging and GPU flows were unchanged and were not run.
