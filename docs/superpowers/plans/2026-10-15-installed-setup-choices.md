# Installed setup choices implementation plan

**Goal:** Show existing installations everywhere in setup, prevent accidental installation again, and require explicit reviewed dependency reinstallation.

**Design:** The existing catalog remains authoritative. Complete installed/ready modules get locked ordinary choices; incomplete modules remain selectable. Existing dependencies are reused without running installers. A separate per-module Reinstall dependencies action is available only for app-managed Python engines supported by the existing installer. It force reinstalls packages in that engine's isolated environment, preserving source, models, datasets, checkpoints and configuration. External/native/system tools do not gain destructive replacement support.

**Contract:** Add default-compatible `can_reinstall` to ModuleInfo and `reinstall: list[ModuleId] = []` to request, reviewed plan and saved job. Reinstall IDs must be unique and selected. Add `reuse` plan operation, stable already_installed/reinstall_unavailable errors, and include nonempty reinstall scope in review tokens. Preserve old job scope hashes for empty/default flags. Reinstall intent survives retry; ordinary all-reused plans cannot create setup jobs.

- [x] Write failing backend tests for installed reuse, installed-only blocking, missing music models, explicit scoped reinstall, legacy defaults and persisted intent.
- [x] Implement catalog/contracts/job/CLI integration and regenerate frontend contracts. Complete music models must pass bounded file checks; missing models keep setup selectable.
- [x] Add tested explicit dependency reinstall flags to existing owned Python installer paths, preserving ownership, cancellation and filesystem checks.
- [x] Add tested status badges, locked choices, explicit reinstall selection/review and stale-inventory guards in the existing wizard. Keep existing user work.
- [x] Review integrated changes; run frontend tests/build, backend regressions, generated contracts and CI platform typing. Desktop is unchanged unless startup/layout changes prove necessary.
- [x] Verify the real installed choices and review flow in the browser without executing a real reinstall or downloading models. Capture the resulting screen.

**Browser evidence:** YuE and HeartMuLa show Installed with disabled ordinary
choices; Stable Audio 3 remains selectable. HeartMuLa's explicit dependency
reinstall review includes only that engine, with Media tools marked for reuse.
An ordinary HeartMuLa-only API plan returned `can_install=false`, two reuse
steps and zero model download bytes. No actual package reinstall was started.

**Recovery review:** Only unfinished reinstall IDs remain in the active plan
across repeated retries, while the original job intent binds authorization.
Immediate queued cancellation and shutdown clear completed task registry entries;
worker verification/quarantine remains enforced. Managed model completion uses
verification of existing dependencies. Explicit package replacement revokes its
old environment receipt before mutation and restores it only after successful
checks.

**Verification:** Full backend discovery passed 2,096 tests (10 skipped), followed
by 88 focused setup tests covering the final repeated-retry and queued shutdown
fixes. Frontend passed 1,574 tests and its strict policy/TypeScript/Vite build.
All 21 CI typing commands and generated-contract checks passed; the final changed
backend files also passed strict typing again for Linux/macOS/Windows after the
last recovery changes. Desktop passed 87 tests (1 skipped). Final diff whitespace
checks passed. Browser verification used the real installed inventory and only
the read-only plan/review action; real package reinstallation was not executed.

No commit or push requested. No actual reinstall, data removal, library movement or model download is part of this change's browser verification.
