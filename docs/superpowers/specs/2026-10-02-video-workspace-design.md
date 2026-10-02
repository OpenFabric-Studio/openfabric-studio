# Video workspace clarity

The user requests a clearer Video experience after the Home, voice and navigation redesigns. Use the established dark theme, panel tokens, icons and keyboard patterns. Implement within the authorized UI scope. The user explicitly authorized committing and pushing after implementation and review.

## Information hierarchy

Prefer a project workspace with guided stages over a strict wizard: revisiting scenes and approvals is routine. Keep saved projects in a compact searchable rail, with status, selected state and named open/delete/download actions. Project management no longer occupies the main header. On small screens put the library behind a labelled disclosure and keep the stage navigation usable without squeezing five labels into tiny text.

Separate creating a project from editing the selected project's source. New project opens a song chooser without modifying or discarding the current draft. Cancelling returns to the current project and prior stage. Successful creation uses the existing API and enters Direction. A project's saved source/title/duration remain clear even when its song is no longer in the current catalog. Source-change warnings offer a concrete new-project action while retaining all existing restrictions.

The workspace shows the current project, saving/job status and a compact five-stage navigation. Song, Direction, Storyboard, Preview and Export remain revisitable for an existing project. Stages that require a project are unavailable during creation. Existing save, action, cancellation, polling and revision ownership remain in `useVideoWorkspace`.

## Direction, scenes and output

Direction presents three labelled visual approach buttons with concise descriptions: Generated scenes, Animated cover and Audio visualizer. Generated scenes expose visual direction and model selection; image modes emphasize the required reference and omit controls that have no effect. Keep resolution and seed where relevant, while denoise/refinement/guidance/negative prompts use an Advanced disclosure. Setup details, disk requirements and measured warnings use a separate disclosure. Preserve model availability, reference/file limits and analysis gating.

Storyboard gives the sequence and selected shot distinct visual regions. Timing, duration and shot description are primary; seed, influence, protection and editing tools are secondary. Image modes omit ignored prompt/influence controls while retaining the stored values. An invalid stored description exposes a temporary shot-label repair editor, which stays mounted while typing; backend validation remains authoritative. Musical markers and re-analysis stay available through progressive disclosure. Preview clearly identifies the selected shot and variant approval state, with an empty state explaining the next action. Export separates rendering missing clips from assembling already approved clips, displays the approval count and explains disabled actions. Framing and encoding remain primary; optional timed-text editing is secondary. Finished output and download have a clear result region.

Use visible labels, 44px actions, keyboard tab navigation, focus restoration after in-panel stage changes and responsive containment. Keep global job progress/cancel and saving/error states visible across stages. Errors and warnings must use public messages; unknown internal text must not leak.

## Scope and verification

No new engines, dependencies, API contracts, storage schemas or model downloads. Preserve all saved media, references, previews, drafts and backend processes. Live verification uses navigation/disclosures only, without creating, saving, generating, approving or deleting user projects. Add regressions for creation/cancellation isolation, unavailable stages, model-specific controls, advanced disclosures, source change recovery, focus, actionable blocked output and error sanitization. Retain existing lifecycle/save/preview/export/pagination/deletion regressions. Run frontend tests/build, contracts and diff checks, review independently and inspect desktop/phone layouts.
