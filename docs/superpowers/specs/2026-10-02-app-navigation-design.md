# Compact app navigation

The user requested a compact header containing status icons with tooltips and a redesigned left menu that folds into icons. This continues the approved Voice Clone redesign using the same dark theme and existing routes.

## Layout and hierarchy

- Desktop: a 224px left sidebar, folding to a 64px icon rail. Remember expanded/collapsed preference locally; invalid or inaccessible storage falls back to expanded.
- Workspace: Home and Editor. Create: ACE-Step 1.5, YuE2-3B, Voice Clone and Video. Tools: LoRA training. Settings and Help remain at the bottom.
- A 44px top bar contains engine status icons. Hover, keyboard focus and tapping reveal the engine name and runtime state. Missing initial status is unknown, never falsely stopped. Reading a status does not start an engine.
- Explicit music-engine sidebar actions retain the existing model-switch behavior and guard duplicate starts. LoRA remains discoverable with a prerequisite explanation when ACE-Step is stopped.
- Narrow screens use a navigation drawer to preserve content width. Trap drawer focus, support Escape, return focus, and close on navigation or a desktop breakpoint transition.

## Accessibility and compatibility

Use labelled controls, semantic navigation groups, current-page markers, visible focus, 44px hit areas and tooltips that dismiss on Escape. Tooltips render outside the sidebar's scroll clipping and stay within the viewport. The main-content skip link remains keyboard accessible. Existing Help/Notices dialogs and attribution are preserved.

The header maintains `--app-header-height` so sticky job summaries and generator controls remain below it. Existing route components and their state ownership remain unchanged. No API, backend, model data or dependencies change. Failed engine switching displays a translated public message rather than the existing raw exception string.

## Alternatives considered

A permanently collapsed rail maximizes space but makes discovery harder. An expanded menu without collapse is easier to learn but consumes space while editing. The user's requested expandable rail supports both. A persistent rail on phones would consume too much of the working area, so a drawer is used there.

## Verification

Behavioral tests cover collapse persistence and invalid storage, active routes and history, explicit engine start and duplicate guards, unknown/status tooltips, mobile drawer focus and breakpoint changes, and late actions after teardown. Run all frontend tests and strict production build; inspect desktop, collapsed and narrow layouts in the live app. Do not start engines, delete voices or generate user media during browser checks.
