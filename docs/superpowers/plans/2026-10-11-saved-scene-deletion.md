# Remove saved MP4 scenes

The user requests a remove action for each saved scene in Cut a movie. Scope: delete the owned MP4/metadata for that cut, retain the original/imported movie and siblings, retain already-prepared character-swap copies. No actual user scenes are deleted during verification.

Reuse existing origin/path validation, admission and document locks, generated contracts and UI error handling. Add a typed DELETE response containing the source and deleted cut IDs. Cuts have immutable IDs/intervals and no revision API; reject active task owners, queued/cutting states and unresolved workers. Atomically hide an inactive cut in a marked tombstone before cleanup; retain the marker through interrupted cleanup and retry only validated tombstones during startup.

The UI shows a named, time-range confirmation and disables conflicting actions while deletion is pending. Invalidate stale polling/responses, remove only the captured cut after validating response ownership, restore owned keyboard focus, and clear a matching character-swap scene selection through an event to the Video page. Existing prepared swaps remain independent.

Verification: first reproduce missing DELETE routes, then temporary-library regressions for ready/failed/cancelled cuts, active ownership, worker evidence, source/sibling preservation, escaped paths, origin rejection and interrupted cleanup. Add UI regressions for confirmation/cancel, error/reload/navigation races and handoff selection. Run frontend tests/build, backend discovery, generated-contract drift check and exact CI Python type checks. Rebuild/restart the idle loopback app and inspect controls without mutating user scenes; no commit/push requested.

## Completed verification

- Missing deletion routes first returned HTTP 405; all 15 isolated backend deletion regressions now pass. The real CPU scene-to-character handoff regression additionally verifies that deleting the scene preserves the prepared swap's retained video/audio and source movie.
- Frontend: 1,172 tests in 138 files, plus strict TypeScript/policy production build passed. Focused cutter/page/workspace/swap integration passed 120 tests, including 14 new UI regressions.
- Backend: full discovery ran 1,464 tests successfully, with 10 expected skips, under temporary config/library/Seed-VC/module roots. Generated-contract drift check and both exact CI strict mypy scopes passed on Linux, macOS and Windows typing paths (6 feature / 163 core files per platform).
- Desktop: 85 passed, one skipped. Final diff review and whitespace checks passed.
- Isolated runtime/browser: created two synthetic MP4 scenes with cloud disabled, verified Delete controls, deleted one through its local endpoint, then verified HTTP 404, disappearance from the UI and identical original/sibling hashes. Shut down the test server cleanly.
- Actual app rebuilt/restarted on loopback port 9000, with both existing scene Delete controls visible. SHA-256 checks confirmed all five existing source/scene metadata and MP4 files unchanged. Screenshot: `/tmp/openfabric-saved-scene-delete.png`.

Logs: `/tmp/openfabric-scene-delete-{frontend-full,build,backend-full,desktop,types,green,copy}.log`. Verification deleted only disposable fixtures. No user scenes, external downloads, model assets or paid requests were changed. Work remains uncommitted.
