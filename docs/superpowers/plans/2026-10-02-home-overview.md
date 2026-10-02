# Home overview implementation plan

**Goal:** Deliver a visually stronger, strictly informative Home page.

**Architecture:** `HomeView.vue` becomes a presentational view using the existing i18n and shared AppIcon. A `homeOverview.ts` locale namespace supplies the copy. Home no longer imports stores, model switching, library APIs, voice selection or playback components. Existing app-shell behavior and other workspaces stay intact.

**Tech stack:** Vue 3, strict TypeScript, vue-i18n, existing CSS theme tokens, Vitest/happy-dom.

User authorization covers this reversible redesign. Continue on `feat/voice-clone-ui`, preserve all previous/unrelated changes, do not commit or touch user media.

## Tasks

- [x] Replace the two previous Home dashboard tests with focused regressions for informational content, no controls/players, no API/model actions and backend-independent rendering. Confirm expected failures against the current Home page.
- [x] Add `frontend/src/locales/homeOverview.ts` and integrate it in `frontend/src/locales/en.ts`, retaining existing locale keys for compatibility.
- [x] Replace `frontend/src/views/HomeView.vue` with the hero, static illustration, four workspace descriptions and useful local-library/version/Settings guidance. Reuse `AppIcon.vue`, make all user-facing copy translatable, and keep decorative SVG inaccessible to screen readers.
- [x] Run focused Home/Settings regressions, then `npm test` and `npm run build` from `frontend/`.
- [x] Independently review the scoped diff for informative-only behavior, honest feature claims, accessibility, scope and regressions.
- [x] Verify the real app at desktop and 375px with both sidebar states; save a final screenshot and leave the app open.
- [x] Inspect the final diff and record actual verification. Leave changes uncommitted.

## Completed verification — 2026-10-02

- Observed all five new Home regressions fail against the old interactive page, then pass against the informative view.
- Focused Home/Settings run: 20 tests passed.
- Final frontend run after the description-list markup correction: 744 tests passed across 92 files.
- Final production build passed the strict type policy, vue-tsc and Vite build.
- Generated contract check and final git diff whitespace check passed. No API schemas changed.
- Independent read-only review found no important findings; the minor description-list grouping issue was corrected and reviewed again.
- Live browser checked desktop expanded (224px) and collapsed (64px) navigation, 1024px layouts and 375px phone layout. Document widths matched scroll widths at all checked sizes; Home had zero controls. The mobile Home menu selection closed the drawer. Browser error logs were empty.
- Saved desktop and phone previews in `.superpowers/home-overview-2026-10-02/`; reset the temporary viewport override and left the live Home tab open.
- The 24-file prior voice-workspace preservation manifest still matched after removing only the separate navigation/Home locale additions for the locale comparison.
- This change is frontend-only. Backend, desktop and GPU workflows were not changed or exercised. No engine launch, generation or user-media mutation was initiated during Home verification.
- Changes remain uncommitted.
