# Local music engines implementation plan

**Goal:** Add HeartMuLa and Stable Audio 3 to Music with real
offline local runtimes and recoverable saved output.

**Architecture:** Existing module setup installs pinned isolated environments.
An app-owned typed request starts a supervised local subprocess. SQLite jobs
retain requests, worker identity and published track linkage. One shared Vue
panel presents verified engine controls and polls backend job state.

**Tech stack:** Existing Python/FastAPI/Pydantic/SQLite supervision and Vue strict
TypeScript/generated contract validation. No additional core dependencies.

- [x] Inspect upstream pinned CLI source and weight requirements; record exact
  paths, commands and license distinctions before implementing adapters.
- [x] Add failing backend request/lifecycle/publication/recovery regression
  tests, then implement `local_music_contracts.py`, `local_music.py` and routes.
- [x] Add failing runtime adapter tests, then implement offline command
  preparation and PCM normalization in owned workers.
- [x] Extend module catalog, source pins, isolated package installers, readiness
  evidence and license declarations; exercise affected setup tests.
- [x] Add failing UI/API tests, then implement the two routes, selector links,
  shared form, retained job list, cancellation and teardown-safe polling.
- [x] Regenerate contracts with the existing script; include new strict Python
  modules in CI, document setup and integration limits.
- [x] Review the integrated diff and run repository-required checks in isolated
  paths. Restart the existing source app and verify the UI/API.

File ownership: backend jobs/contracts/DB/lifecycle; module setup/config/docs;
frontend routes/forms/API/tests; root owns runtime adapters/workers/CI/generated
artifacts and integrated verification. No commits, deployment or weight downloads.

## Verification recorded 14 October 2026

- Frontend: 1,332 tests pass; strict TypeScript/Vite build passes.
- Backend: full isolated suite runs 1,687 tests with 10 skips. Follow-up support
  report and supervisor checks pass after final review changes (28 tests).
- Desktop: 85 tests pass, one Windows-specific test skipped.
- CI strict Python scope passes Linux, macOS and Windows typing paths; generated
  contracts and diff whitespace checks pass. Native OS tests were not run on
  Linux or Windows.
- Reviewed real source pins and resolved both engine dependency sets in isolated
  dry-runs. No new engine environments or weights were installed for the user.
- Final live API returns HTTP 200 for engine status, persisted history, the full
  17-module inventory/support report and new-engine track filters. HeartMuLa's
  page renders the setup state in Chrome; frontend tests cover both panels.
- Independent review confirmed upstream CLI mappings and caught/fixed recovery
  admission after a supervisor exit, the setup-busy error mapping, and raw-file
  cleanup after successful output normalization. Unverifiable process groups
  remain quarantined; recovery never kills an unrelated process group.
- Real MLX inference, converted Heart checkpoint compatibility, audio quality,
  speed and peak memory remain unverified. DiffRhythm was excluded at the
  maintainer's request because its conditioning weights are non-commercial.
