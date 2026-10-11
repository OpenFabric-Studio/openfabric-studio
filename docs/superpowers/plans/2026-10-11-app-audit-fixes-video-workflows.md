# App audit repairs and Video workflow implementation plan

> **For agentic workers:** Use focused independent work with exclusive ownership. Regression tests precede each behavioral repair. The user has authorized implementation; do not commit or publish.

**Goal:** Repair all thirteen audit findings and organize Video into three distinct processes.

**Architecture:** Reuse existing storage, local-origin, process ownership and Vue lifecycle patterns. Keep each durable backend job independent from its browser panel. Root owns API access, dependencies and Video organization; storage, startup and frontend regressions proceed independently.

**Tech stack:** FastAPI/Pydantic, SQLite/files, Python unittest/mypy, Vue/Pinia/TypeScript/Vitest, Electron/Node tests.

## 1. Storage and recovery (findings 1, 2, 6)

- [x] Extend `backend/app/data_root.py` manifest/inventory and relocation metadata coverage; test every omitted store, native source identity after relocation and interrupted rollback in `backend/app/data_root_test.py` and `backend/app/library_relocation_test.py`.
- [x] Reproduce redirected `video_projects/` and `videos/` roots; reject external serving/writes/deletion and quarantine recovery in `video_projects.py`, `video_jobs.py`, `video_render.py` and associated tests.
- [x] Preserve malformed/unreadable existing config with stable errors; retain missing-config first-run behavior and test exact byte preservation.
- [x] Run focused suites in isolated configuration/library/seed/module roots and inspect the resulting diff.

## 2. Processes and desktop (findings 4, 5, 12)

- [x] Add startup cancellation/failed-drain tests before registering spawned processes immediately in `backend/app/orchestrator/manager.py`.
- [x] Prove foreign successful/404 listeners cannot satisfy health and real owned listeners can in `orchestrator/process.py` and narrowly scoped platform helpers/tests. Require successful readiness and owned-listener evidence without new dependencies.
- [x] Reproduce double Open/Retry and stale launch failure in desktop tests; make `desktop/src/main.js` single-flight with current-run UI ownership.
- [x] Run focused backend lifecycle and desktop tests; review platform failure behavior.

## 3. Frontend state (findings 7–11)

- [x] Turn scratch editor reproductions into tests of saved warp restoration and Undo/Redo during export; fix `EditorPage.vue` with load/session-owned buffers and export cleanup.
- [x] Test dirty listening drafts through a two-second refresh; fix `VoiceTrialCard.vue` hydration ownership.
- [x] Test teardown/project switches during stem decoding; fix `StemsPanel.vue` awaited action ownership.
- [x] Test project/shot switching during catalog load; give `VideoCloudPanel.vue` an independent catalog lifecycle.
- [x] Run affected Vitest files and frontend strict checks.

## 4. Local API access (finding 3)

- [x] Add `backend/app/local_access_test.py` using the real app and mocked handlers. Expect foreign-origin form POST and foreign Host requests to return 403 before body consumption; allow same-origin and non-browser native requests.
- [x] Apply the existing `require_local_origin` policy globally to unsafe methods and restrict every HTTP Host to loopback in `module_security.py`/`main.py` using typed ASGI middleware.
- [x] Update only real-app test clients from synthetic host names to loopback; verify Vite proxy and configured development origins remain functional.
- [x] Run API trust and affected contract/lifespan tests.

## 5. Dependencies (finding 13)

- [x] Inspect actual vulnerable package callers and primary maintainer fixes. Update frontend source-map-js compatibly and resolve desktop sprintf-js exposure without downgrading electron-builder.
- [x] Verify lockfiles/install behavior with npm audit and meaningful caller tests; retain or explicitly document any upstream limitation rather than claiming it was fixed.

## 6. Video workflow UX

- [x] Add failing tests in `frontend/src/views/video/VideoPage.test.ts` for exclusive visible workflow panels, keyboard navigation, retained inputs, project deep links and cutter-to-swap handoff.
- [x] Replace independent cutter/swap toggles in `VideoPage.vue` with Create/Cut/Replace tabs; preserve current creation wizard and optional character tools.
- [x] Add translated workflow copy using existing locale composition and update `docs/video-studio.md` for the new navigation.
- [x] Check responsive layout, focus, errors, cancellation and local/paid step clarity in an isolated UI session.

## 7. Integrated verification

- [x] Run `npm test` and `npm run build` from `frontend/` (build to a temporary output directory while the user app runs).
- [x] Run `python -m unittest discover -s app -p '*_test.py' -v` from `backend/` with temporary `OPENFABRIC_CONFIG`, `OPENFABRIC_DATA_DIR`, `SEED_VC_DIR`, `OPENFABRIC_MODULE_ROOT` and empty provider credentials.
- [x] Run `python backend/scripts/generate_contracts.py --check` and the exact Linux/macOS/Windows mypy commands in `.github/workflows/ci.yml`.
- [x] Run `npm test` from `desktop/`, inspect final diff and report real results plus unverified platform/GPU/provider behavior.


## Completion evidence — 2026-10-11

All thirteen reported findings are repaired. Final integration also repaired the pre-existing desktop shutdown request: Node fetch supplies `Sec-Fetch-Mode: cors`, so the owned request must supply its matching local Origin. Its actual Node-header regression failed before the change and passed afterward; the API guard remains unchanged.

| Check | Actual result |
| --- | --- |
| Frontend `npm test` | 138 files, 1,138 tests passed |
| Frontend `npm run build -- --outDir <temporary preview>/frontend` | Strict TypeScript, type-safety policy and production build passed; running app assets preserved |
| Backend `python -m unittest discover -s app -p '*_test.py' -v` | Final pre-commit recheck: 1,427 tests run, 10 skipped, no failures; temporary config/library/seed/module roots and no provider credential |
| Additional final API header regression (`app.local_access_test app.module_security_test`) | 12 tests passed, including Node-shaped shutdown headers |
| `python backend/scripts/generate_contracts.py --check` | Passed after regenerating inventory keys |
| Exact CI mypy platform loop | 6 feature files plus 163 core files passed for Linux, macOS and Windows typing paths; this is the configured scope, not whole-backend typing |
| Desktop `npm test` after final shutdown fix | 86 tests run, 85 passed, 1 skipped |
| `npm audit --json` in frontend and desktop | Zero reported vulnerabilities in each lockfile |
| Backend `python -m pip check` | No broken requirements |
| `git diff --check` and integrated source review | Passed; prior uncommitted work retained |

An isolated loopback browser app verified the three desktop workspaces, keyboard navigation, browser Back with draft retention, a 375-pixel responsive layout without horizontal overflow, and synthetic MKV → four-second MP4 export → replacement handoff with heading focus and retained scene. No browser console errors were observed. The browser extension denied automated local file selection, so the synthetic movie was seeded through the local API; browser upload automation was not verified. The test tab and server were closed, and the viewport override reset. The screenshot is `/tmp/openfabric-video-workflows.png`.

No user-library relocation, paid request, GPU/model download, live engine generation or installer build was performed. Actual Windows kernel behavior, HTTPS proxy downloads, clean-machine installation and cloud visual quality remain platform/provider checks. Library relocation tests inject cross-volume copy behavior; they do not prove every physical filesystem. Restart the backend and rebuild/refresh the running frontend to load these source changes.
