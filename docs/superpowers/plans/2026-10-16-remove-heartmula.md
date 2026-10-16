# Remove HeartMuLa support

**Goal:** Remove HeartMuLa generation, training, installation and UI support while preserving existing user files and keeping the other engines functional.

**Approach:** Delete engine-specific implementations and remove the engine from executable contracts and catalogs. Retain only narrowly scoped compatibility for old library origins and saved records. Existing audio remains accessible in the library; old HeartMuLa work cannot run again. No library migration, model deletion, commit or push is part of this change.

- [x] Remove frontend routes, choices, forms, translations and obsolete tests; retain coverage of the remaining engines.
- [x] Remove installation definitions, downloads, environment options, setup launchers, desktop integration and CI references.
- [x] Remove HeartMuLa training workers, dependencies, conversion code and shared training branches.
- [x] Remove generation workers, adapter validators, queue payloads and engine capabilities; verify saved-record compatibility without deleting files.
- [x] Update maintained documentation and mark historical plans as historical rather than rewriting their recorded decisions.
- [x] Regenerate client contracts; run frontend tests/build, backend tests, strict Python platform checks and desktop tests.
- [x] Review the removal against the source baseline and search for residual executable support.

Source baseline: `/tmp/openfabric-heart-removal-baseline-20261016-200334/`. Existing unrelated changes are retained. Tests use temporary configuration, library and engine paths with mocked model boundaries.

Verification so far: frontend 1,816 tests and strict production build passed; desktop 87 passed and one Windows-only skip; all eight strict Python groups passed for Linux/macOS/Windows. The history-cap follow-up passed 66 local-generation/setup tests and repeat platform checks. Live catalogs exclude the retired engine and all seven saved tracks remain listed. The complete final backend suite passed: 2,480 tests, 11 expected skips. The initial full run exposed a stale Stable Audio catalog-position assertion; it now looks up the engine by ID, and its focused setup tests and the complete rerun pass.
