# Generator panel controls

The user requests hiding the full generator card and opening it as a floating panel over the track list. Use the existing generator without changing generation behavior.

## Design

- **Docked:** existing side-by-side desktop layout and stacked mobile layout.
- **Hidden:** generator stays mounted but is visually hidden; results use the full available width. Visible controls reopen it docked or floating.
- **Floating:** non-modal overlay over the results, with a bounded width and height, a scrolling body and visible Dock/Hide controls. The underlying track list remains interactive.
- The floating header/title is a drag handle using native Pointer Events and pointer capture for mouse, touch and pen. Interactive header controls do not initiate movement. A named, focusable title handle also supports arrow keys (10 px; Shift: 50 px).
- Cap floating height at the smaller of 680 px and available viewport height so normal desktop screens leave room for vertical movement. Clamp movement within the viewport and below the fixed application header. Recheck bounds after viewport or panel size changes, end active drags on cancellation/teardown/mode changes, and retain the last position through mode switches during the session.
- Change CSS position/visibility at the same component tree position; do not remount or Teleport the form. Preserve prompt, lyrics, file input, voice/model selection and ongoing upload state.
- Use named buttons, keyboard operation and Escape handling with sensible focus return. Do not intercept Escape intended for an open child help dialog.
- Persist only validated panel modes in local storage. Invalid/unavailable storage falls back to docked. Coordinates remain in memory; no dependencies or resizing controls are needed.

## Implementation and verification

- [x] Add focused panel component and ACE page integration, retaining the initial unknown engine-status guard.
- [x] Translate controls in English and Russian.
- [x] Reproduce missing behavior, then verify docking, hiding, floating, preserved draft, keyboard/focus and storage validation.
- [x] Run the full frontend suite and strict build; review scoped changes.
- [x] Verify desktop/mobile controls in the browser without submitting generation jobs; record actual results.
- [x] Add bounded pointer/keyboard dragging with capture cleanup and session-only position retention.
- [x] Reproduce and verify movement, bounds, cancellation, interactive-control guards, mode teardown and resize/content-size changes.
- [x] Run focused drag regressions and the strict build, then verify dragging in the browser without generation jobs.

## Verification evidence

- Twenty-seven panel/page regressions pass, including the real form's prompt, lyrics, native file input, voice selection and pending submission surviving mode changes. The page regression also covers unknown initial engine status and real Help dialog Escape handling. Four additional Help dialog regressions cover initial focus, Tab wrapping, opener restoration and teardown; all five missing focus behaviors failed before the fix.
- Final full frontend run: **260 tests across 37 files passed**. `npm run build` passed the strict type policy, Vue/TypeScript checks and production build.
- Read-only browser checks against the existing development app: at 1280 × 900, hiding changes the results layout from 480/728 px columns to one 1232 px column. Floating is fixed at 480 px wide, has a scrollable body, and fits within the viewport. The same prompt/file DOM nodes and selected synthetic file remain through Hide → Float → Dock.
- At 390 × 844, the page remains 390 px wide; the floating panel is 358 px wide and fits vertically. Dock/Hide remain visible while its form body scrolls. Desktop and mobile screenshots were inspected.
- Actual desktop mouse dragging moved the 480 × 680 px panel from (784, 77.5) to (434, 197.5); arrow keys then moved it by 10 px and Shift-arrow by 50 px. Extreme drags stopped at (12, 77.5) and (788, 208), below the 65.5 px application header and within the viewport. Pointer capture ended on release. Dock/Float retained position, prompt and the same native upload node/file.
- Resizing the browser to 390 × 844 reclamped the panel to (20, 152) with no horizontal overflow. Mouse movement at that mobile viewport moved it to (20, 32); its scrolling body did not move the Dock/Hide controls. Touch/pen pointer ownership is covered by regressions; physical touch/pen devices were not tested.
- Escape first closes the child Help dialog and leaves the panel floating; Escape inside the form then hides it and focuses Show. Floating and hidden modes survive reload. The browser session reported no JavaScript errors. No generation job was submitted, stopped or deleted for these checks.
- Actual browser Help opening focused Close automatically; Tab stayed within Help and Escape returned focus to its opener while keeping the panel floating. Scoped changes received an independent review.
- Reviewed the panel, page integration, Help dialog change and regression tests. No new dependencies or generation API/store changes were required for panel controls. Drafts/uploads are retained across mode switches, not page reloads.
