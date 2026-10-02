# Voice Clone UI/UX proposal — 2026-10-02

Status: direction A approved by Seth ("a"). Implement in this session.

## Findings from the current app

- Speech profile creation and audiobook creation render before the singing voice library. At the normal desktop viewport, the singing library is below the first screen.
- Every selected singing voice opens on Files, including a ready 1,000-step voice. Model selection and dry vocal playback sit inside Build.
- Five large step cards, a historical job panel, repeated step guidance and setup controls compete for space. A completed older job prominently shows unavailable phase/time values.
- Deletion is a direct action beside each profile/voice. The examined handlers call deletion without a user confirmation.
- The ShirleyB change warning compares all 32 recordings with 31 built-from filenames. The extra recording is explicitly excluded, and the saved enabled source set matches the trained input set. This warning therefore does not establish a relevant source change.
- Speech readiness copy says synthesis is coming next, despite the existing engine API reporting a connected server and the existing trial worker supporting synthesis.
- Speech trials return a local output path but there is no examined GET route for playing that trial output. Adding browser speech playback would require a separate backend serving boundary; the mockup does not invent a usable URL for it.

## Recommended direction A: mode-based workspace

Keep the existing Voice Clone entry and product design tokens. Provide Singing, Speech and Audiobooks modes, with Singing as the initial default. Desktop uses a searchable library alongside the selected item; narrower screens stack the library and content. New item creation opens on demand.

Ready singing voices open on Overview. New voices start at Sources. Active jobs initially open on the appropriate stage, but polling must not interrupt the user's deliberate navigation. Persist mode/voice/stage in validated route query values so refresh and Back recover navigation.

Overview shows published model identity, readiness, active model selection, a short dry reference recording, and Test & compare / Use for new songs actions. Explain the role of the reference recording accurately. Preserve separate published-model and preparation-draft state; an unfinished draft must not make an already published model appear unusable or evaluated.

Keep the existing preparation sequence and backend requirements: Sources, Review samples, Coverage, Train, Compare. Use compact navigation and give the current step one clear primary action. Keep source provenance, singer confirmation, saved selection, reference selection, cleanup alternatives, screening limits, held-out comparison, full-state resume validation and model isolation.

Active/failed/cancelled jobs keep visible progress, error, cancellation and retry controls. Completed historical job details become a compact expandable summary. Do not manufacture time estimates or completed quality evaluations.

Reveal filters, bulk actions and advanced experiment settings when requested. Fix the excluded-source warning using saved selection/build provenance, rather than guessing relevance from a count difference. Keep the distinction between changed data, changed options and legacy metadata with unknown provenance.

Speech mode separates the profile library from synthesis. Profile creation retains explicit consent. Describe engine state using installed, mock and api_reachable independently. Keep setup hints within a troubleshooting disclosure. No fake success, progress or audio URL.

Audiobooks mode separates saved books/chapter jobs from creation. Keep narrator and chapter editing together, with retry/export alongside the selected book. Switching modes must preserve drafts and pending action ownership, avoid duplicate submissions, and ignore stale responses after teardown or item changes.

Move deletion into item actions and require a confirmation identifying the selected item. Preserve keyboard navigation, visible focus, labels and explicit disabled-state reasons.

## Alternative B: library-first layout

The three modes are the same, but singing opens with voice cards above a full-width editor. This is useful for browsing a larger collection. It consumes more vertical space and provides less continuity when frequently switching between preparation and comparison, so A fits the current workflow better.

## Implementation and verification scope

Reuse the existing Vue panels and domain composables, generated API contracts, local font, components and theme. Separate the shell from singing/profile/book workspaces where responsibility justifies it; do not add dependencies or new training APIs. Published artifacts remain backend owned. Preserve all original library data.

Meaningful frontend regression checks should cover ready/new/running/failed voice entry, mode switching with drafts and pending responses, keyboard/focus behavior, route refresh/Back, excluded-source warnings, model selection, deletion confirmation, readable error states, and existing preparation/build cancellation and polling cleanup. Run npm test and npm run build, then inspect desktop and narrow layouts in the browser. If implementation needs an app-owned backend contract or serving change, generate contracts and run the applicable isolated backend/type checks.

## Prototype verification

The local mockup server returned HTTP 200. Desktop layout, both directions, and Singing/Speech/Audiobooks mode switching were inspected in Chrome. Prototype actions do not start synthesis, preparation or training. Production accessibility, responsiveness and regression checks remain implementation work.

UI pattern searches did not return a relevant progressive-disclosure pattern. The proposed structure is an inference from the live app, repository workflows and general interaction principles, rather than a claimed database pattern match.

Preview: http://localhost:49276

Files: content/voice-workspace-directions.html and voice-workspace-preview.png.
