# Video project management

The user authorized merging the reviewed changes into the fork's default branch and adding a way to manage and remove saved video projects. The reviewed sync branch is now on fork `master` at `5c70cdf`; work continues on a focused topic branch.

## Interface and scope

Add a **Manage projects** button beside the saved-project selector. It toggles a project library near the top of Video Studio, using the existing panel styles. Keep search, status filtering, ten-item pagination, open and download actions, and add project deletion. Mark the selected project and show an empty result message; clamp pagination after deletion or filtering. Existing rename in Song and duplication in Direction remain available.

A header button and an inline library are more discoverable than the current library buried below the wizard. A separate project page or modal adds navigation and focus-management complexity without helping this small task.

## Deletion and ownership

Reuse the existing typed `deleteVideoProject` API. Confirmation names the project and states that its project files, references, previews and renders are permanently removed while its source song is retained. Disable deletion for active projects and tell the user to cancel first. Backend deletion remains authoritative and drains owned work if another browser starts a job during confirmation.

The workspace owns mutation, autosave and selection. Prevent concurrent actions while deletion runs. Wait for an already-started save before deleting the current project, clear its pending autosave timer, and invalidate poll responses. Remove the entry and its recoverable browser draft only after successful deletion. Leave selection empty and return to Song if the selected project was deleted; retain the selected draft and wizard step when deleting another project. A failed deletion preserves the project and recoverable edits and shows a translated stable error. Teardown aborts the request and ignores late results.

The backend safety audit reproduced image upload work surviving DELETE and deletion losing a retained worker receipt after a cleanup failure. Reference upload admission now shares the project deletion lock and registers an owned task before awaiting anything. Cancellation and shutdown drain those tasks and their encoders before artifact removal; failed cleanup retains ownership and blocks deletion. DELETE additionally checks the persisted worker record after draining runtime work. These are ownership fixes within the existing storage model; no schemas or API contracts change.

## Verification

Cover confirmation cancellation, exact project identity, active-job restrictions, list and page updates, source-song retention, failed deletion, autosave and stale-poll races, removal of the last project, and teardown. Use synthetic frontend fixtures and temporary backend storage. Run the full frontend suite, strict types and production build, applicable backend regression checks, contract drift and protected-branch CI before merging and updating the normal checkout. Never delete a real user project as a test.
