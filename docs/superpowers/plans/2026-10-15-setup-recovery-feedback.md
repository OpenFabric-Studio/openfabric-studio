# Setup recovery and useful feedback

**Goal:** Repair the actual HeartMuLa failure, preserve completed setup work and make the current result actionable.

**Evidence:** Job `b46472a3c51d4045a0ff7e3d21002380` failed validation after its converter successfully saved 289 tensors. MLX writes valid safetensors with null metadata; the app rejected it. Import-generated Python caches also changed the dependency fingerprint, incorrectly revoking environment verification. The screen displayed generic instructions and repeated full historical cards.

**Design:** Keep one current run prominent and prior runs collapsed. Show safe, structured failure summaries, failed phases, retry advice, real output timestamps and bounded phase history. Preserve originals, reuse completed conversion components and rearm active polling promptly. Raw process output stays in backend logs. No new infrastructure or destructive repair.

- [x] Reproduce null metadata and cache-fingerprint bugs, fix them, and test component recovery after a later conversion failure.
- [x] Add schema-owned failure/events/output-time fields with legacy defaults, preserve the failed phase, and test safe persisted diagnostics and recovery.
- [x] Improve current/history UI, failure actions and fast polling without restarting work after teardown.
- [x] Run focused/full frontend and backend checks, all platform typing, generated contract check and affected desktop checks; review the integrated diff.
- [x] Restart only while app work is idle, retry the already reviewed HeartMuLa job without expanding its download scope, and verify its actual result in the app.

**Actual recovery:** The existing HeartMuLa job completed on 15 October 2026 at
17:03:45 UTC. Both HeartMuLa and HeartCodec converted and passed structural checks;
media and HeartMuLa steps are verified. The success, phase history and environment
receipt remained valid after another idle app restart and browser reload. Actual
audio generation and GPU output quality were not tested.

**Review:** Fixed two additional recovery cases: failed worker verification now
persists non-retryable step diagnostics (including safe legacy response defaults),
and an older retried job stays prominent after completion by its latest update.
Late phase/output callbacks cannot change a cancelled job. No new dependencies.

**Verification:** Backend discovery passed 2,081 tests (10 skipped); final focused
setup checks passed 58 tests, and the final job-only checks passed 23 tests after
the worker-recovery review changes. Frontend passed 1,563 tests and its strict
type-policy/TypeScript/Vite build. Desktop passed 87 tests (1 skipped). All 21 CI
mypy commands passed across Linux/macOS/Windows typing paths, generated contracts
matched, and the final diff passed whitespace checks.

No commit or push is authorized. No original model, dataset or configuration files may be overwritten or deleted during repair.
