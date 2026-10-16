# Engineering audit — 14 October 2026

> Historical record — HeartMuLa retired on 16 October 2026. Its generation, training and installation instructions below no longer describe supported functionality. See [current music documentation](local-music.md).

Reviewed the current working tree on `feat/local-music-engines`, based on
`20b8fd1`. The existing uncommitted local music and LoRA implementation was
preserved. This audit made bounded repairs and added regression tests; it did
not commit, publish, move the user's library or download model weights.

The reviewed operating model is one local user and one backend process per
library, with concurrent HTTP requests, polling and owned workers. Review
followed central paths and their callers rather than every line in the repo.

| Area | Reviewed boundaries |
| --- | --- |
| Storage | Library relocation and rollback, SQLite transactions, track/version artifact ownership, retention receipts, dataset uploads and symlink containment |
| Jobs | Generation-card/track/stem deletion, separation reruns, export source capture, cancellation, shutdown and abrupt backend death |
| Frontend/API | Generated contracts, untrusted native response envelopes, LoRA controls and saved settings, timeline scheduling, accessible generator controls and isolated browser playback/export/reload |
| Desktop/repository | Source startup and loopback binding, navigation/IPC checks, dependency vulnerability scans, ARM64 packaging/installation/signature checks, CI, fork instructions and the previous audit's decisions |

## Reproduced defects and repairs

Severity describes the reproduced consequence; it does not imply that every
installation encounters the trigger.

| Severity | Trigger and consequence | Repair and evidence |
| --- | --- | --- |
| High | Moving a library omitted `local-music/`, `music-lora/` and `stable-lora-training/`, leaving adapters, recovery audio and checkpoints behind | Relocation includes these directories, their JSON metadata and database worker receipts. Folder contracts/types and descriptions include them. Temporary-library tests move/reopen real validated adapter fixtures, recover retained completion/audio, and inject rollback failures. |
| High | A retention request on a worker thread committed another request's unfinished track transaction; the failed publisher then rolled back nothing | Retention plan/receipt writes reuse the existing independent SQLite transaction helper. Threaded tests inject publication failure and verify that no phantom track survives. A failed receipt/plan update rolls back both changes. |
| High | Failed or cancelled forced separation removed previously completed stems before replacement existed | A private run directory stages replacement output. Existing stems/catalog survive failure and cancellation; successful publication prunes only unshared artifacts. Tests preserve a stem referenced by another track. |
| High | Track/card deletion removed input and catalog entries while stem or MIDI jobs were still queued/running | Track deletion checks derivative activity after awaited drains. ACE acquires every existing removal guard, checks the entire batch, then deletes synchronously. Tests cover late starts and prevent partially deleted batches. |
| High | A forced stem rerun could prune an export's input; a lock-queued start could also be forgotten by stem deletion | Stem registration shares the existing export lock. Active exports/removal prevent replacement. Removal rechecks activity under that lock before any export deletion. Interleaved source-capture and HTTP-delete tests preserve sources, completed exports and worker registration. |
| High | Legacy build/preparation and default separation workers survived abrupt backend death | Common voice spawn/FFmpeg and default Demucs/RoFormer paths use the existing parent-pipe supervisor. POSIX tests kill a fake backend and verify termination of CPU workers and their descendants. Existing injected process owners are preserved; FFmpeg diagnostics remain readable. |
| High | Dataset directory symlinks and dangling upload filename symlinks allowed writes outside the engine dataset root | Resolved directory containment and atomic exclusive filename creation prevent escape/overwrite. Failed copies remove only their newly owned partial file. Tests preserve existing originals, duplicates and supported internal aliases. The upload route is now included in strict CI typing. |
| Medium | Native HTTP 200 responses with failure codes were accepted as successful LoRA mutations | Shared native decoding accepts wrapped success only at numeric code 200, rejects failures with safe typed errors, and validates mutation acknowledgements. UI error messages are translated. Failure and malformed-response regressions pass. |
| Medium | ACE advertised strength up to 2 although the installed native handler clamps to 1 | Live controls and client requests use 0–1. Restoring an older strength-2 snapshot sets the new active value to 1 with a notice; stored schema, original JSON and import fingerprints remain unchanged. Tests also retain an existing value of 0.4. |
| Medium | Moving, trimming or fading a clip during playback changed the display/export but left the old live schedule playing | Scheduling invalidation includes the values consumed by the audio/MIDI scheduler and resumes from the current playhead. Existing generation/seek cancellation guards remain. Five timing regressions pass. |
| Low | Generator labels were not associated with selects/ranges/textareas; YuE title keys were absent | Existing translated labels are associated with controls and YuE reuses the existing title translations. DOM regressions verify associations and visible title text. |

Native ACE behavior was verified against the installed source at revision
`ca1e85fe9430179831e6bc6be790c332190a3866`. Its relevant LoRA route/control files
were clean; the response wrapper was inspected in the locally modified
`api_server.py`. These findings concern the selected runtime, not every possible
third-party engine build.

## Follow-up repairs

The request to fix the remaining issues also covered the original validation
gaps. These checks reproduced further defects, which were fixed without adding
engines, dependencies or a new public job workflow.

| Severity | Trigger and consequence | Repair and evidence |
| --- | --- | --- |
| High | Direct metadata, codec, runtime-version and worker/listener identity commands survived abrupt backend death | These commands reuse the parent-pipe supervisor. Short captures have bounded output and timeouts; binary audio and diagnostic stream behavior are preserved. Seventeen CPU crash-test subcases kill a fake backend and verify worker and descendant exit. Synchronous video analysis already runs below an owned worker. |
| High | A failed character-dataset normalizer drain removed its unpublished inputs and ownership evidence while the worker remained alive | A private owner map retains process/identity state; persisted receipts support startup recovery. Failed cleanup retains inputs and keeps admission blocked. Explicit cancellation, shutdown and verified recovery drain the worker before deletion. Real CPU and injected-failure tests cover these paths. |
| High | A spawn failure after worker creation left no returned process handle; character/reference/speech cleanup then removed inputs without proving worker exit | Cleanup records the spawn attempt, validates bounded receipts and reuses exact PID/token/group verification. Missing, malformed, oversized, symlinked or unverified receipts preserve inputs and return the existing `cleanup_failed` error. Independent live-worker tests verify cancellation drains before deletion. |
| Medium | New output limits/timeouts escaped media validation as internal errors | Media, voice replacement and optional engine boundaries map these failures to their existing stable validation/processing errors. A failed worker drain remains a cleanup failure. HTTP and helper regressions verify the error distinction. |
| Medium | Required mypy checks created a local cache database that resource preparation copied and its privacy guard then rejected | Resource preparation excludes `.mypy_cache`. The regression fails before the change; final resource staging and actual ARM64 packaging pass. |
| Medium | Packaged backend imports wrote Python caches inside the signed application, invalidating its resource seal | Desktop backend environments force `PYTHONDONTWRITEBYTECODE=1`. A real Python-import regression verifies no cache writes. Strict deep signature verification passes before and after installed app launches. |
| Low | Home copy advertised only two music engines, and prerelease notes promised obsolete automatic engine/model downloads | Home copy describes the engine selector; release notes accurately distinguish shared backend bootstrap, engine installation and documented weight setup. Existing home assertions were updated. |

## Verification

All backend regression runs use temporary configuration, library, Seed-VC and
module roots. Workers in crash tests are CPU fixtures; no GPU models are loaded.
Independent review reproduced the final deletion race before the fix and verified
the corrected 409 response, preserved catalog/output and retained worker owner.

- Frontend: 156 test files, 1,393 tests passed; strict policy/TypeScript checks and production build passed.
- Backend: final complete discovery ran 1,805 tests in 380.428 seconds; 1,795 passed and 10 platform/environment cases were skipped. Focused ownership regressions also passed 37 tests, including 17 crash-test subcases. Independent review verified failed-proof input preservation and actual cancelled-worker cleanup.
- Python: all five CI mypy batches passed for Linux, macOS and Windows typing paths, covering 205 selected source files. This is the configured strict scope, not the entire legacy backend.
- Desktop: 87 tests passed; one native Windows case skipped.
- Generated contract drift, dependency consistency (`pip check`) and final diff checks passed.
- Dependencies: npm audits checked 179 frontend and 277 desktop packages. `pip-audit` checked 34 active locked Python packages, all 38 universal lock pins and 62 installed backend packages. No known advisories or skipped Python packages were reported. Optional engine environments and model binaries were outside this scan.
- Browser: native Chrome exercised home/music/adapter/setup navigation in a temporary library. Missing runtimes displayed setup instructions and disabled generation. A synthetic three-second 440 Hz stereo tone played through Web Audio, exported as a persisted track, and its project survived reload. Exported WAV analysis measured 44.1 kHz, two channels, three seconds, 440 Hz, finite nonzero samples and no clipping. The normal master limiter affects gain; this does not establish bit-perfect bypass or subjective audio quality.
- macOS: final local ARM64 DMG/ZIP built with `--publish never`. The shipped bootstrap installed a fresh Python 3.12/backend in a temporary root, without engine weights. The final DMG was checksum-verified, mounted read-only and copied into a temporary Applications folder. Its installed copy served the frontend and all 17 library folders, retained its port across restart, and terminated its backend on quit. Strict deep ad-hoc signature checks passed before and after launch. All 499 packaged backend/frontend files and 24 desktop source/renderer/manifest files matched the frozen working tree. The local artifacts are in `desktop/dist/` and were not published.
- Local app restarted after confirming it was idle with no pending library move; YuE selection was restored and verified running. Backend and frontend returned HTTP 200 at loopback ports 9000 and 5173. The isolated browser backend was stopped after testing; the user's library was not used by regression tests.

## Limits and next work

This is a central-path audit with reproducible repairs, not an exhaustive security
certification. HeartMuLa and Stable Audio 3 runtimes/weights and Stable training
bundles are absent, so their real generation/training flows remain unverified.
Subjective DSP/voice quality, native Windows execution, Linux desktop installation,
the setup-screen button flow and downloaded macOS Gatekeeper/quarantine behavior
remain unverified. These local artifacts are ad-hoc signed, without Developer ID
or notarization. No repository-wide performance benchmark was run. A clean
dependency scan covers known advisories in the checked packages, not optional
engine dependencies or undisclosed vulnerabilities.

Prioritize platform/engine validation and recovery evidence before additional
engine integrations, consistent with the repository roadmap. The existing Vue/
Pinia → FastAPI/Pydantic → SQLite/files architecture and loopback policy remain.


## Subsequent music queue implementation

The approved music home now uses one brief, ordered model selections, per-model
variations, an input-adaptation preview, a persisted queue and grouped saved
results. Existing direct generators and training routes remain available.
This work adds no dependency or separate queue service.

The backend stores immutable briefs, normalized requests, adapter identities and
resolved seeds in additive SQLite tables. Revisioned controls and submission
keys protect concurrent requests. A private inherited owner reservation spans
model transitions; native children reuse matching sessions, while HeartMuLa and
Stable workers remain explicitly one-shot. Failed cleanup blocks advancement.
Pause saves/drains the active take; cancellation retains completed tracks and
pending work. Restart waits for existing publication, reconciles child records
and leaves pending items paused. Ambiguous submissions are never automatically
regenerated. Save retry reuses retained audio; inference retry records a new
attempt and preserves its predecessor.

Independent reviews reproduced and corrected interrupted-publication recovery,
partial-result cancellation, startup retries across a failed model group,
unknown native submission status, cancellation of the Stop request itself,
pause intent escaping a failed transaction, and retrying superseded attempts.
Retained YuE publication failures now use a save-specific error only when a
completed, confined WAV exists. Existing external ACE installations can be
selected while stopped using their actual launcher/environment footprint;
readiness is a preflight check, not proof of hardware fitness or audio quality.

Queue verification:

- Backend: complete discovery ran 1,920 tests in 369.086 seconds: 1,910 passed and 10 platform/environment cases were skipped. The final 127 queue/engine/YuE/API/lifecycle regressions also passed after adding native timeout and legacy-child cancellation safeguards; both cases pause and release the native session before another take.
- Frontend: 158 files / 1,426 tests passed; strict policy, TypeScript and production build passed.
- Python: all six CI batches passed for Linux, macOS and Windows typing paths (212 selected source files); changed queue/YuE sources were checked again after final review fixes, with separate caches to avoid concurrent mypy cache corruption.
- Desktop: 87 tests passed and one native Windows case was skipped. Contract drift, `pip check` and diff checks passed.
- Browser: native Chrome used an isolated temporary library and CPU fixture engine. It exercised two briefs, ordered models, adaptation disclosure/Escape focus, enqueue without starting, pause after saving, reload/reconnect, resume, cancellation, two-tab observation and saved playback. Four synthetic tracks survived under their original briefs, alongside one cancelled take. Layouts were inspected at 80%, 100% and 150% zoom, then normal zoom restored. These checks demonstrate orchestration, not model generation or GPU residency.
- The fixture backend was stopped. The real app was restarted with an empty queue, YuE selection restored, both native models selectable and absent optional runtimes clearly disabled. Loopback backend/frontend routes returned HTTP 200. Regression tests used temporary configuration/library/module/Seed-VC roots; no test audio was added to the user's library.

The earlier DMG/ZIP verification in this report predates the queue. Those local
artifacts have not been rebuilt or published for this feature. Real model
loading, memory release across transitions, adapter inference and audible output
still need a run with installed weights. HeartMuLa and Stable runtimes/weights
remain absent on this machine; no weights were downloaded.
