# Engineering audit — 5 October 2026

## Summary

The audit started from `f51fde8` on fork `main`. Changes are grouped on `refactor/audit-2026-10-05` for review; the maintainer subsequently requested committing and pushing the complete branch. The audit did not modify the original checkout, private configuration, running source app or user library. Test libraries, engine paths and module roots were temporary; no model weights were downloaded.

Discovery inventoried 720 tracked files and 351 maintained source files. Review followed central execution paths and dependencies, rather than claiming a line-by-line inspection of every file or rewriting large modules solely because of their size.

| Area | Reviewed paths and boundaries |
| --- | --- |
| Frontend | Vue routing and navigation, home/settings status, music and training observation, speech/audiobook actions, video training, editor import/export, audio graph ownership, MIDI parsing, HTTP/storage validation and generated contracts |
| Backend | FastAPI request/error boundaries, SQLite track/profile/audiobook catalogs and existing migrations, artifact containment/publication, audiobook import/narration/cast/export, optional engines, generation/resource admission, process supervision, cancellation, recovery and shutdown |
| Desktop/setup | Electron preload/setup IPC and navigation, owned backend readiness, process descendants, minimal bootstrap, downloads/resume/archive preparation, resources packaging, managed module installation, legacy Linux/Windows setup and configuration preservation |
| Repository | Contributor/security/roadmap/fork documentation, manifests and lockfiles, compiler/type policies, generated contract toolchain, CI, attribution and dependency reports |

The Vue/Pinia → FastAPI/Pydantic → SQLite/files architecture remains intact. Engines stay in separate environments. Loopback binding and the existing authentication policy are preserved.

## Changes Made

Changes prioritize recoverable data, deterministic worker ownership and UI session boundaries. Existing atomic publication, process supervisors, GPU leases, admission locks and non-overlapping polling helpers are reused. Every behavioral fix has a regression or fault-injection test; the final three accessible control labels were checked in the browser's semantic snapshot.

## Bugs Fixed

Severity below describes the reproduced trigger, not a claim that every installation is affected.

| Severity | Reproduced defect | Resulting behavior and evidence |
| --- | --- | --- |
| High | Deleting a historical track removed audio/ABC used by another track; stems/MIDI directory deletion bypassed reference checks | Contained artifacts are removed individually only when no remaining primary, ABC, stem, MIDI or version reference uses them. Artifact reset preserves other references. Tests cover shared files, unshared cleanup, version sources and surviving catalog entries. |
| High | Catalog paths outside the library were deleted or served through legacy endpoints | Deletion preserves external files; legacy audio/ABC serving rejects external paths and escaping symlinks. Valid contained files still download. |
| High | Optional-engine timeout/cancellation leaked descendants and work was absent from shutdown/setup/GPU admission | Workers have supervised identities, durable receipts, owned tasks, recovery and cancellation-resistant drains. Ambiguous ownership keeps admission closed. Completed response shapes and valid output URLs are retained. |
| High | Accelerator admission worked in only one direction between optional/training workers and native music mutations | The shared admission lock checks both registries before native mutation/submission. Tests cover both submission orders, no upstream forwarding on refusal, and reservation release. |
| High | Character training could outlive shutdown, mutate status during GET polling, retain queued cancellation, or release its GPU lease before descendants exited | Uploads/workers are owned; startup recovery reconciles interrupted jobs; polling is read-only; cancellation persists terminal state. Descendant draining precedes lease release and adapter readiness. |
| High | Optional output/input and character still/adapter symlinks escaped their storage roots | Root, object and leaf containment checks reject escapes before serving or staging. Canonical adapter aliases are materialized as regular files before advertising readiness. |
| Medium | Optional upload limits were enforced only after multipart spooling | Origin and declared/streamed body limits run before parsing. Tests count consumed bytes and verify early rejection, including missing declared lengths. |
| Medium | Plain text/truncated output could be reported as completed audio | Nonempty complete PCM WAV and decodable video are validated before atomic publication. Chatterbox saves explicitly select PCM output across its supported backends. This avoids the [TorchAudio 2.6 SoundFile default](https://raw.githubusercontent.com/pytorch/audio/v2.6.0/src/torchaudio/_backend/soundfile_backend.py) that writes float WAV for float tensors. |
| High | Cached/retained audiobook sections bypassed current voice consent; revocation during final synthesis/encoding or removal from editable cast still permitted new narration publication | Reuse and final public commits recheck current consent, including actual profiles recorded in retained completed sections. Publication/revocation share the established profile lock. Tests revoke consent at section, chapter and encoding boundaries and exercise the removed-actor HTTP sequence. |
| Medium | Cover replacement removed the previous cover before the new write/metadata commit succeeded | Unique staged files, atomic replacement and per-book publication locking preserve the prior cover on write/commit failure. Cleanup failures are logged after a valid commit. |
| Medium | Concurrent collection downloads shared one partial ZIP; regeneration could replace a pending response's file | Unique candidates and immutable content/metadata snapshots preserve download paths and readable archives. Tests interleave downloads and change book content between snapshots. |
| Medium | Applying changed character conditioning retained obsolete shot approval | Approval is cleared while source variants remain available; assembled output pointers are invalidated consistently. |
| High | YuE upload conversion used an unmanaged FFmpeg process, leaked cancellation descendants and exposed raw diagnostics | A cohesive conversion module owns tasks/processes, bounds diagnostics/time, validates complete WAV, maps safe errors and drains shutdown. Failed drains retain the process and scratch receipt, keeping setup/library admission closed until retry succeeds. Real CPU FFmpeg and failure-injection tests verify this. |
| High | Packaging included private `.env.production`/backup variants or symlinked source; the independent scan missed private directories | Filtering and a separate final guard reject private variants, directories and symlinks while preserving intended examples. Temporary packaging fixtures reproduce the leaks. |
| High | Desktop command cleanup waited for `close`, which descendants holding inherited pipes could prevent | Draining begins on leader `exit` and retains one owned drain. Real child fixtures cover inherited pipes and command success/failure. |
| High | Any HTTP 200 on the released backend port could satisfy desktop readiness | A bounded response must match service, fresh launch nonce and owned PID; redirects and malformed/oversized/wrong identities fail. One explicit Uvicorn worker and exclusion of inherited reload mode keep that identity deterministic. Real backend integration includes both conflicting environment settings. |
| Medium | Setup IPC accepted ordinary backend/foreign file frames; navigation trusted string prefixes | Setup privileges are limited to the shipped setup document in the current top frame. Parsed document/origin checks reject lookalike URLs and credentials. Normal backend navigation and external links remain supported. |
| Medium | Installer deletion threads continued after cancellation released setup admission | Mutating deletion threads drain before leases/scratch reuse. Tests hold deletion open, cancel/shutdown, and verify admission remains occupied. |
| Medium | Linux command substitutions hid Git failures and overwrote custom Demucs configuration | Git failures propagate explicitly; existing Demucs project files are preserved. Shell fixtures exercise failed checkout and custom configuration. |
| Medium | Windows legacy setup substituted obsolete example paths and could create ineffective configuration | Explicit quoted engine paths are written atomically only when configuration is absent. Existing configuration is preserved. Native PowerShell execution is still a platform evidence gap. |
| High | Delayed editor decode/BPM work inserted audio into another project; delayed export used live project/title/format metadata | Session, project and buffer identity guard imports. Exports capture immutable project/provenance/format/duration before awaits and preserve durable library results. Tests switch sessions during each boundary. |
| High | Delayed audiobook edits overwrote another selected book's drafts, notices or job observation | Selection generations and owned action signals guard UI feedback, while accepted durable results update the correct library item. Tests interleave saves, selection, regeneration and teardown. |
| Medium | A narrator-preview scalar ref was treated as a list, causing hide/teardown audio errors | Separate typed preview and chapter-player refs pause both safely. Component tests and the mock browser flow cover preview followed by navigation/publication. |
| Medium | Corrupt LoRA storage was asserted into an array and later failed during selection | JSON is parsed as unknown, entries are validated, names use the existing 500-character backend limit, and malformed storage falls back safely. No speculative vendor path cap was introduced. |
| Medium | MIDI chunk lengths could seek backwards indefinitely; completed/cancelled/error playback retained audio graphs | Unsigned chunk lengths and bounded event reads prevent the freeze. All created oscillators/gains/master connections are disposed on natural completion, stop and scheduling failure. Malformed-byte and graph-lifetime regressions exercise these paths. |
| Medium | MIDI tempo changes rescaled elapsed time and conductor-track order changed playback; note tails extended beyond the declared roll duration | Tick endpoints now use accumulated tempo segments, with a shared clock for formats 0/1 and independent format-2 pattern clocks. Overall duration covers returned note tails. Timing, track-order, duplicate/conflicting tempo and duration regressions pass. Interpretation follows the [official SMF specification](https://midi.org/standard-midi-files-specification). |
| Medium | YuE's route and converter buffered complete uploaded/converted recordings in RAM | Incoming audio, PCM validation and native forwarding use bounded 64 KiB chunks and owned seekable scratch files. Output validation follows verified descendant drain. Completed native results survive secondary cleanup faults; failed cleanup retains ownership for retry without leaving false reservations after successful cancelled drains. |
| Medium | Changed audiobook snapshots accumulated complete ZIP copies indefinitely | Response-owned snapshots remain available through queued/streaming ASGI delivery; obsolete generated ZIPs are pruned only after successful replacement. Cancellation, shared references, failed publication/pruning, Range/HEAD and symlink protection are covered. Original audio and user files are retained. |
| Medium | Source launchers could inherit Uvicorn's multiworker/reload environment despite single-backend storage assumptions | Maintained launch commands explicitly select one worker and clear inherited reload settings. Temporary command fixtures reproduce inherited settings without launching models or changing the running app. |
| Medium | Trainer polling could start after teardown or overlap; LoRA observation did not resume on re-entry | Owned polling prevents overlap and late mutation. LoRA re-entry observes retained jobs without submitting replacements; stale pre-submission status cannot stop a newer training poll. |
| Low | Editor project-name, export-format and shared volume controls lacked accessible names | Existing components and localized labels now expose Project name, Export format and Volume of Master in the browser accessibility snapshot. |

## Architecture / Refactoring

- Optional-engine request/response models moved into `optional_engine_contracts.py`; their TypeScript types and runtime parsers are generated from the backend. Existing fields are preserved, and engine identifiers reflect the actual four registered values.
- YuE upload conversion moved out of its HTTP route into `yue_upload.py`, with explicit lifecycle, safe errors and ownership. The route retains transport/proxy responsibilities.
- Track cleanup shares one containment/reference check across primary, ABC, versions, stems and MIDI, rather than recursively deleting folders that can contain shared files.
- Main lifecycle and busy/resource checks include optional engines, character training and conversion work. No queue service, cache server, database replacement or authentication layer was added.
- Strict CI Python scope expands from 99 to 109 production files. This remains scoped checking; it is not represented as whole-backend or vendor-inference typing.

## Cleanup

Removed the verified unused optional Electron installer path, its obsolete ACE swap/cache-growth/patch/extract-promotion helpers, unused `saveConfig` export and corresponding obsolete tests. The active minimal desktop bootstrap and backend/CLI module installers remain.

Removed the unused `analyzeVideo`, `createVideo` and `cancelVideo` client wrappers after checking runtime and test callers. Legacy library/busy fallbacks remain where callers still use them. Removed the direct desktop `diff` dependency after its sole shipping path disappeared.

Updated only the compatible `http-cache-semantics` leaf from 4.2.0 to 4.3.0. The original lockfile had one high advisory in desktop download/build tooling; the updated npm audit reports none. This is an observed advisory result, not a security certification. See the [advisory](https://github.com/advisories/GHSA-ch52-4w7c-c8xp) and [publisher's 4.3.0 release commit](https://github.com/kornelski/http-cache-semantics/commit/b1d4bd682fbab0252985de45219f4e7497c0067c).

Corrected contributor/agent/security documentation to the actual fork `main` and current English UI locale. Preserved upstream MIT attribution and active compatibility functionality.

## Tests

The baseline was already green: 888 frontend tests, 829 passing backend tests plus 10 platform skips, 76 desktop tests, strict build, contracts and the original three-platform type scope. Reproductions exposed missing coverage rather than existing baseline failures.

New tests exercise shared artifacts, escaped paths, failed writes, concurrent snapshots, cached/retained consent, revoked cast provenance, final-publication races, real descendants, drain failures, startup cleanup, early upload rejection, corrupt outputs, false desktop readiness, configuration preservation, stale UI actions, malformed MIDI, graph disposal and resumed observation. The follow-up adds 47 tests for MIDI clocks/tails, streamed uploads and cleanup, collection-response ownership, and inherited source-launch settings. No test uses the user's library or downloads model weights.

## Verification

The final follow-up implementation passed the checks below: 1,947 passing tests and 11 platform skips. The initial audit's independent four-case reproduction suite also passed for admission, retained cast consent, canonical adapters and both Chatterbox output branches. A follow-up HTTP/1.1 socket fixture verified byte-identical WAV forwarding, real FFmpeg conversion, exact Content-Length and preserved native result tokens without starting YuE.

| Check | Recorded result |
| --- | --- |
| Frontend `npm test` | 957 passed across 108 files |
| Frontend `npm run build` | Strict policy, vue-tsc and Vite pass |
| Backend full unittest discovery | 923 run, 913 passed, 10 platform skips (79.672 seconds) |
| Generated contracts `--check` | Pass |
| Scoped strict mypy, Linux/darwin/win32 | 109 production files pass on each path |
| Desktop `npm test` | 78 run, 77 passed, 1 native PowerShell test skipped |
| Dependency integrity/advisories | `pip check` passes; frontend/desktop npm audits report zero; OSV checked all 26 exact base Python pins and returned no findings |
| Shell/Node syntax and final diff checks | Pass |
| Initial audit browser smoke | Home, setup wizard, video empty state and editor load; mock audiobook preview/create/reload/PCM/collection download pass in a temporary library |
| Real desktop backend identity | Fresh nonce/owned PID/no-store and shutdown verified, including inherited concurrency and reload settings |

The browser initially hit a stale lazy module when verification rebuilt `dist` beneath its open tab. Reload restored navigation; the final frozen-build session has no captured console or runtime errors. No product workaround was added for this verification artifact. Frontend tests emit Happy DOM resource connection messages for the test-only localhost origin, but complete with no failed or unhandled tests.

Logs are outside Git under `/tmp/openfabric-audit-20261005-*` and `/tmp/openfabric-followup-*`; test environments, browser artifacts and audit dependencies remain outside the user's data. Final diff review includes generated types, active callers, lifecycle integration, private artifacts and the original checkout's unchanged status. The original app still returns HTTP 200 with ACE-Step running and YuE stopped.

## Remaining Issues

- Real GPU inference, perceptual voice/video quality, native Windows process/PowerShell behavior, packaged installers/signing and a clean-machine Linux/macOS/Windows installation matrix were not executed. CPU fixtures and platform typing cannot establish those outcomes.
- The optional worker's Torch/Kokoro/Chatterbox/Torchaudio imports remain outside isolated strict checking because they belong to separate vendor environments. Worker helpers are annotated and request parsing/output settings are behaviorally tested; no fake declarations or suppressions were added.
- MIDI tempo maps are fixed; SMPTE remains unsupported and format 2 remains an overlay rather than a pattern playlist. Audio-device behavior is not certified by mocked graph tests.
- Collection cleanup follows the documented one-backend-per-library runtime. Maintained launchers enforce one worker; manually starting multiple applications against the same writable library remains unsupported.
- YuE uploads now stream through temporary files, but no rejecting size/disk quota was introduced. Supported sizes and duration remain a product/resource policy decision.
- Advisory scans cover the checked application manifests and base Python lock; separately installed model environments and their weights were not scanned or certified.

## Impact & Decisions Required

Implemented fixes preserve public success shapes, existing catalog schemas, sources, generated outputs, loopback exposure and authentication behavior. No destructive migration, library relocation, major dependency replacement or release was performed. Branch publication was authorized after implementation and review; integration into `main` remains a separate step.

One policy boundary was deliberately preserved: consent revocation blocks new narration/regeneration publication, including retained voices, while previously completed exports remain downloadable and cover-only repackaging remains permitted. Extending revocation to historical downloads, archives or cover remux would change authorization behavior. The recommended approach is to retain this behavior until the product specifies revocation semantics and how to handle already distributed files; no retrospective deletion or restriction was introduced in the audit.

A separate compatibility decision concerns the legacy YuE upload: a rejecting size cap would change accepted input sizes. The safe internal buffering fix is implemented with streamed, owned scratch storage and forwarding. Establish supported size/duration and disk quotas before imposing a cap; staging failures already return stable storage errors.

## Recommended Next Work

1. Run a recorded clean-machine installation and real-engine generation/export/cancellation matrix on the supported hardware and operating systems before making platform or quality claims.
2. Review and integrate the audit branch in focused reliability/data/frontend/setup groups, using the recorded full checks.
3. Specify historical voice revocation semantics, then add a separate policy change if required.
4. Define and test supported YuE upload sizes, durations and disk quotas.
5. Add SMPTE timing or a format-2 pattern playlist only when actual workflows require them.
