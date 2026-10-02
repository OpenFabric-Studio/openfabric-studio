# Video Project Management Implementation Plan

> **For agentic workers:** Use focused parallel ownership for the independent UI and workspace tasks, with a read-only backend safety audit and final integrated review. Follow regression-first implementation.

**Goal:** Make saved video projects discoverable and safely removable, then deliver the changes to the protected fork `master`.

**Architecture:** A typed project library presents existing project records and emits open/delete requests. The workspace composable owns deletion, autosave/poll generations and selected-draft cleanup; the existing backend remains the source of truth for deleting project artifacts.

**Tech Stack:** Vue 3, TypeScript strict, generated API contracts, vue-i18n, Vitest, Python unittest, GitHub required checks.

## Tasks

- [x] Verify fork default branch, protection and passing checks; fast-forward fork `master` to the reviewed `5c70cdf` without rewriting history.
- [x] Create ignored `.worktrees/video-project-management` on `feat/video-project-management`; run baseline video tests (30 passed).
- [x] Audit `backend/app/video_render.py`, `video_projects.py` and DELETE route/tests for project-only cleanup and job ownership. Reproduced an undrained reference upload and lost retained worker receipt; add owned upload registration/drain and persisted-worker protection before delivery.
- [x] Add failing workspace tests in `frontend/src/views/video/useVideoWorkspace.test.ts` for selected/nonselected deletion, failures, active jobs, in-flight autosave, stale poll results and teardown.
- [x] Implement `removeProject(id: string): Promise<boolean>` in `useVideoWorkspace.ts` using `api.deleteVideoProject(id, signal)`; serialize actions, invalidate accepted poll versions, preserve state until success and clear only the deleted project's browser draft.
- [x] Add failing UI tests for the header manager, cancellation, named confirmation, active restrictions and page clamping. Implement a focused `VideoProjectLibrary.vue`, wire `VideoPage.vue`, and add English/Russian locale strings in `locales/videoWorkspace.ts`.
- [x] Run `npm test` and `npm run build` in `frontend`, applicable isolated backend regressions and `python backend/scripts/generate_contracts.py --check`; review final diff and independent review findings. Full results: 565 frontend passed; 513 backend ran, 509 passed and 4 optional vendor skips; exact CI strict mypy scope of 50 modules passed for Linux, macOS and Windows typing paths; contracts and production build passed.

Delivery procedure: commit and push to the fork topic branch, wait for all required CI jobs, fast-forward protected fork `master` to the verified commit, update the normal checkout and rebuild its frontend. Git history and GitHub's required checks record completion of these external steps.

## Expected lifecycle regressions

The selected project is removed only after a successful API response, with `project` and `draft` set to null, selection and undo reset, and step returned to Song. Removing another project leaves the current dirty draft intact. A pending save must settle before DELETE so it cannot recreate or overwrite deleted state. Poll results captured before DELETE must never reinsert the deleted project. An API failure must retain its list entry and editable state, and unmount must abort without any late state update.

## Evidence collected

- Regression-first workspace and manager tests observed failing behavior before implementation. Independent lifecycle review found a poll masking a failed deletion; its regression also observed red before the fix.
- Full frontend suite: 565 tests in 68 files passed. Strict frontend policy, vue-tsc and production build passed.
- Desktop (1280 px) and mobile (390 px) production browser checks used actual track/project routes with a temporary synthetic library. Confirmation cancellation retained the entry; confirmed DELETE removed only the addressed folder and preserved both other projects and the source WAV hash. Selected/last-project deletion returned to Song, reload retained the empty library, and source audio stayed available. No horizontal overflow, error overlay or browser errors were observed. The owned browser/server were closed.
- Independent review also identified keyboard focus loss on row removal, a spurious Direction completion tick with no project, and cancellation before the upload's first coroutine step. Each was reproduced before its fix. Focus recovery has eight dedicated regressions and respects focus moved elsewhere; empty/removed projects show no completed steps; backend pre-start tests prove delayed file closure blocks deletion and closure failure retains ownership.
- Backend deletion changes cover reference ownership/drain and the persisted-worker check. Thirteen new regressions passed, including real subprocess termination, source preservation, caller cancellation, shutdown and cleanup failure. No API schemas or database structures changed; GPU inference and native Windows process execution were not part of this verification.
- Final production browser check confirmed native focus moves from the removed project's Delete action to the next remaining project's Open action, with no browser errors. Temporary browser/server were closed afterward.
- Protected-branch CI exposed a stalled backend test run. A deterministic regression reproduced Starlette's disconnect-check cancel scope absorbing a racing watcher cancellation; the download handler now sets an explicit stop signal before cancellation. Actual-Request tests cover successful preparation and caller cancellation without retaining a temporary download. The stalled run was cancelled and its logs retained; delivery requires a passing Linux CI rerun.
- New video regression handshakes now wait for either readiness or child completion with a bound, and release cleanup before draining. Fault injection previously hung until an external timeout; hardened tests surface the original child exception. Three diagnostic cases cover child failure, completion without readiness, and timeout cleanup.
