# Setup activity visibility and instructions

**Goal:** Put saved setup activity first and expose actionable manual instructions beside each blocked step.

**Architecture:** Reuse the validated job and module inventory contracts. Saved outcomes remain historical; instructions and evidence are explicitly labeled as current checks, with offline warnings. No installer or persistence changes are needed.

**Scope:** `frontend/src/views/settings/ModuleSetupPanel.vue`, its existing test file, and `frontend/src/locales/moduleWorkspace.ts`. Preserve setup review, safe external URLs, download progress, cancellation and resume guards. No installations or model downloads during verification.

- [x] Add regression tests for activity order, visible manual instructions, current evidence, safe documentation links, offline/missing guidance, timestamps and explicit resume.
- [x] Run the focused tests and confirm the new assertions fail before implementation.
- [x] Move activity below the panel header. Add step counts, saved timestamps, download selection, current guidance/checks, workspace navigation and a clear resume explanation. Retain existing controls and download details.
- [x] Run focused and full frontend tests, strict TypeScript/build, and contract drift check. Review the final diff.
- [x] Inspect the existing saved manual job in the running app without resuming it or installing anything. Capture the updated activity panel.

Verified: 17 focused tests; 165 frontend test files / 1,548 tests; strict type policy, TypeScript and Vite build; generated contract drift check; diff whitespace check. Independent review identified a focus issue after starting setup, fixed with a failing-then-passing regression assertion. Browser inspection used the user's existing ACE-Step manual job and confirmed visible guidance and workspace navigation links. Screenshot: `/tmp/openfabric-setup-activity.png`.

Backend/desktop suites were not rerun for these frontend-only changes. No real installation or GPU generation was started.

No commits or pushes are authorized for this task.
