# Engineering audit and reliability refactor plan

> **For agentic workers:** Use isolated, exclusively owned tasks and review the integrated result. The initial audit request authorized implementation and verification, without model downloads or changes to the running user's library. After the completed audit and follow-up review, the maintainer explicitly requested committing and pushing all changes.

**Goal:** Fix demonstrated defects while retaining OpenFabric's local studio workflows and public interfaces.

**Architecture:** Keep Vue/Pinia presentation, backend-owned validation/storage, generated API contracts, SQLite catalogs, existing process supervisors and the minimal Electron bootstrap. Reuse existing ownership, cancellation, polling and atomic-write components. Do not replace engines, authentication, storage semantics or working subsystems.

**Tech stack:** Vue/TypeScript, FastAPI/Pydantic, SQLite, asyncio, Node/Electron; npm and uv.

## Discovery and safety net

- [x] Inspect 720 tracked files by architecture, centrality, recent changes and dependencies; identify 351 maintained source files, excluding generated code and tests.
- [x] Read repository/contributor/security/roadmap/setup instructions and actual manifests, compiler configuration, CI, contracts and tests.
- [x] Create isolated `refactor/audit-2026-10-05` worktree from `f51fde8`; install isolated test dependencies. Leave the running checkout unchanged.
- [x] Baseline: 888 frontend tests, strict build, 839 backend tests (10 platform skips), 76 desktop tests, contract drift and strict CI scope on Linux/darwin/win32 all pass.
- [x] Reproduce findings with temporary files and CPU fixtures; distinguish defects from large-but-cohesive modules and unverified platform/model behavior.

## 1. Backend media ownership and persisted artifacts

**Ownership:** media worker agent. Files: `optional_engines.py`, its routes/contracts/tests; `video_character_training.py`, `video_characters.py`, `video_projects.py` and tests; `audiobook_narration.py`, `audiobooks.py`, `audiobook_collection.py` and tests.

- [x] Add regressions that fail for leaked timeout descendants, pre-start training cancellation, shutdown/recovery gaps, escaped media/input/character symlinks, corrupt completed outputs, pre-parsing upload limits and public stderr exposure.
- [x] Preserve completed response contracts; supervise and register workers with existing process identity/receipt helpers, admission and cancellation-resistant drains. Expose `recover`, `shutdown`, `work_busy` for shared integration.
- [x] Extract optional-engine Pydantic contracts into `optional_engine_contracts.py`; root registers generation and the frontend consumes generated parsers.
- [x] Validate current narrator/cast consent before reuse of cache or retained sections. Add repeated-section/cast/revocation regressions.
- [x] Stage cover replacements before publication and preserve previous covers on write/metadata failure. Make collection ZIP candidates independent for concurrent callers. Fault-inject writes and interleave two downloads.
- [x] Invalidate shot approvals when applying changed character conditioning, retaining source variants.

**Risk controls:** keep sources/old outputs until replacement succeeds; never kill unverified PIDs; no model downloads or inference certification. Run the affected audiobook/video/optional-engine unittest groups after each coherent change.

## 2. Frontend session boundaries and resource disposal

**Ownership:** frontend worker agent. Files: `EditorPage.vue` and tests; `AudiobooksPanel.vue` and tests; `useLoraRegistry.ts`; `miniMidiPlayer.ts`; `VideoCharacterTrainer.vue`; LoRA observation store/page and tests; `api/localEngines.ts`, `api/videos.ts`.

- [x] Add delayed-decode, delayed-BPM and delayed-export regressions. Capture editor session/project/buffer identity and immutable export provenance before awaits.
- [x] Guard audiobook cast/language/line/pronunciation/regeneration/cover actions against changed selections; preserve durable results without overwriting another book's draft. Separate single narrator-preview ref from chapter-player arrays.
- [x] Parse LoRA local storage as `unknown`, validate entries and safely reject corrupt JSON; verify valid persistence round trips.
- [x] Bound every MIDI chunk/event read, prevent signed-length backward offsets and test malformed/truncated fixtures. Dispose the complete MIDI graph on natural completion and explicit stop, including throwing stop calls.
- [x] Replace trainer intervals with the existing non-overlapping polling loop and lifetime guards. Resume observation of retained LoRA tasks on re-entry without submitting new work.
- [x] Use generated optional-engine contracts after root regeneration. Remove only the three verified uncalled video API exports.

**Risk controls:** preserve export output/provenance and task submission behavior; no redesign or speculative abstractions. Use meaningful Vue/Vitest regressions and strict build.

## 3. Desktop, installation and packaging

**Ownership:** desktop/setup worker agent. Files: `desktop/src/proc.js`, `server.js`, `main.js`, bootstrap components/config and relevant tests; resource preparation; `module_install.py` and tests; legacy Linux/Windows setup scripts/tests/docs.

- [x] Prevent private `.env` variants and symlinks from packaged resources; allow the intended `.env.example`. Test nested fixtures and the final independent packaging guard.
- [x] Begin descendant draining on leader `exit`, retaining one owned drain through stdio `close`; reproduce inherited-pipe hangs with real CPU children.
- [x] Match a per-launch nonce, service identity and PID in bounded desktop readiness responses. Root supplies `/api/desktop/ready` only for a configured desktop runtime; source startup remains unchanged.
- [x] Restrict setup IPC to the existing top-frame setup document, and compare parsed origins/documents rather than string prefixes. Preserve normal backend navigation and external-link behavior.
- [x] Drain mutating installer deletion threads before releasing admission/leases. Test blocked deletion, cancellation and scratch reuse.
- [x] Propagate Linux Git failures and preserve custom Demucs projects; generate explicit Windows legacy engine configuration without overwriting existing files. Mark real Windows execution limits honestly.
- [x] Remove the optional desktop installer path and unused exports only after confirming all runtime/CLI/dynamic usages. Preserve active backend setup and its recovery checks; update obsolete desktop-only tests/docs.

**Risk controls:** immutable artifact pins, atomic promotion and existing external configuration survive; no real installers/engines run during tests. Validate Node tests, shell syntax and focused Python setup checks.

## 4. Shared integration and legacy track safety

**Ownership:** root. Files: `main.py`, `work_busy.py`, readiness boundary/tests, lifecycle/admission tests, contract generator/generated artifact, CI scope, `db.py`, `audio_versions.py`, track routes/tests, YuE upload conversion/tests.

- [x] Integrate media recovery/draining and busy detection so setup/library operations cannot race active workers; test failed startup, admission, cancellation and normal shutdown.
- [x] Add desktop readiness contract and nonce/PID verification tests without changing authentication or loopback defaults.
- [x] Register optional-engine contracts and expand strict coverage to the audited production files. Regenerate `frontend/src/api/generated.ts`; never hand-edit it.
- [x] Reproduce deletion of shared historical audio and files outside the library; centralize safe, unshared artifact deletion and verify remaining tracks retain their files. Constrain legacy serving paths without changing valid uploads/downloads.
- [x] Replace YuE's unmanaged conversion subprocess with existing owned helpers, cancellation/shutdown draining and safe errors; retain successful upload behavior. Test CPU conversion, cancellation and timeout without launching YuE.
- [x] Audit dependency reports and exact ancestry. Apply compatible verified fixes only; document unpatched/major-upgrade decisions rather than running blind automatic upgrades.
- [x] Correct demonstrated developer-documentation drift (`main` and current locale/support routes), retaining upstream history and attribution.

## 5. Review and broad verification

- [x] Independently review every agent diff and test, including concurrency, recoverability, contracts and active runtime callers.
- [x] Run full frontend tests/build, full isolated backend discovery, generated contract check, strict Linux/darwin/win32 scope and desktop tests. Repeat only for new edits/failures/unresolved risks.
- [x] Run relevant dependency/syntax/packaging checks and an isolated browser smoke test. Do not certify GPU quality, hardware installs or native Windows behavior from mock/CPU evidence.
- [x] Remove obsolete implementations and test scaffolding, inspect final diff/private artifacts and verify original checkout/library remain unchanged.
- [x] Write an audit report with severity, evidence, changes, actual checks, reviewed areas and remaining decisions; leave reviewable work uncommitted.

Final frozen verification: 944 frontend tests; strict frontend build; 892 backend tests (882 passing, 10 platform skips); 75 desktop tests (74 passing, one native PowerShell skip); generated contracts and 109-file strict checks on all three platform paths pass. Independent reproduction suite passes four cases. Browser mock audiobook flow/download/reload and accessible editor labels pass; isolated processes were drained. See [engineering audit](../../engineering-audit-2026-10-05.md) for reviewed scope, retained policy/size decisions and model/platform evidence limits. All changes remain uncommitted.

## Follow-up fixes authorized after review

The user requested further fixes. Preserve the prior audit work and the running checkout. These are internal correctness/performance fixes; upload limits, historical consent policy and unsupported hardware certification remain separate decisions.

- [x] MIDI (`frontend/src/audio/miniMidiPlayer.ts` and tests): reproduce tempo changes within one track and across conductor/note tracks; store note endpoints in ticks and convert through accumulated tempo segments. Preserve flat format-2 preview using independent track tempo maps. Retain malformed-input bounds and audio graph disposal. Verify focused tests, all frontend tests and strict build.
- [x] YuE (`backend/app/yue_upload.py`, upload route and tests): reproduce whole-body buffering; stream request chunks to owned temporary files, validate converted PCM in bounded reads and stream the resulting file to the existing upstream. Keep receive/conversion/forwarding registered through cleanup, preserve opaque responses and avoid a new file-size rejection policy. Test cancellation, shutdown, admission, failed drains and actual CPU conversion.
- [x] Collections (`backend/app/audiobook_collection.py`, audiobook route and dedicated tests): reproduce accumulation of obsolete generated ZIP snapshots; acquire response ownership during cancellation-resistant preparation and release after ASGI delivery/cancellation. Prune only exact generated snapshot names after successful publication, preserving active responses, symlinks, user files and original audio. Keep standalone snapshot paths immutable and preserve FileResponse range behavior.
- [x] Independently review integrated changes and run full backend regressions, contracts, the existing three-platform strict Python scope, frontend tests/build and desktop checks. Update the audit report with actual results and remaining limits. Leave changes uncommitted.

Source-runtime review also reproduced inherited worker/reload settings. Maintained source/backend launchers now pin one worker and clear inherited reload. Three new temporary command/environment fixtures failed before the fix and pass afterward. Final follow-up verification: 957 frontend tests, strict frontend build, 923 backend tests (913 passing, 10 skips), 78 desktop tests (77 passing, one native PowerShell skip), contracts and 109-file strict Linux/macOS/Windows checks pass. Independent HTTP wire and code review pass. Original checkout remains clean at f51fde8 and the app remains healthy. These checks preceded the maintainer's request to commit and push the branch.
