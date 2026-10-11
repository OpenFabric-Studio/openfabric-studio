# App audit repairs and Video workflow design

User approved fixing all thirteen findings from the whole-app audit and separating Video into three workflow tabs on 2026-10-11.

## Scope

Repair library relocation, unsafe video storage roots, inconsistent local API access checks, startup process ownership, engine readiness ownership, damaged configuration handling, five frontend state/lifecycle defects, overlapping desktop launches, and disclosed dependency issues. Preserve existing uncommitted features and user media. No commits, publishing, production migrations, paid requests or model downloads.

## Video organization

Use three accessible workflow tabs: **Create video**, **Cut a movie**, and **Replace a character**. Create retains song/silent/reel entry choices and its Source, Direction, Storyboard, Preview and Export stages. Its optional character-reference and training tools receive clear labels and progressive disclosure. Cutter and replacement each have a process explanation, their own inputs, reviews, saved results and contextual progress; neither appears above an unrelated creation wizard.

Selecting a finished cut for replacement activates the replacement workspace and retains the selected local scene. Switching workflows preserves unfinished inputs when practical and must not interrupt accepted durable backend work. A selected project deep link activates creation. Navigation is keyboard accessible, responsive and uses existing semantic color tokens/components; no new visual system or dependencies.

Local movie import/cutting/preparation are visibly distinct from the two separately reviewed paid OpenRouter steps. Keep backend validation, cost/transfer approvals, unknown-submission handling and saved result recovery unchanged.

## Implementation and verification

Storage, process/desktop, frontend lifecycle and root integration work have exclusive file ownership. Each confirmed defect gets a failing regression before implementation. Review the integrated diff, then run all frontend tests/build, isolated backend tests, generated-contract check, the CI Python platform typing loop and desktop tests. Exercise the Video navigation in an isolated test app with synthetic/mocked data; never edit the running user's library or invoke paid generation.

## Limits

Automated checks do not establish GPU/perceptual quality, live provider forwarding, actual Windows kernel behavior or clean-machine installer compatibility. Dependency updates must preserve the installed builder version and packaging behavior; no blind force/downgrade fixes.
