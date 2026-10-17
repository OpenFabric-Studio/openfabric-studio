# Music feature implementation progress

Implementation follows the [master plan](2026-10-17-music-features-master-plan.md). This record distinguishes shipped code in the working branch, completed verification and remaining roadmap work. A completed test does not establish musical quality or clear the rights to a recording.

## Workspace and data protection

- Base: `4609467881dc90af4c232b6eb67b6a1db7747782`, fork `main`.
- Implementation branch: `feat/music-features`, in an isolated worktree. Existing work in the original checkout is preserved.
- The configured push remote is the OpenFabric Studio fork. The initial execution request did not authorize Git integration. The maintainer subsequently requested committing all changes into `main` and pushing to the fork; a release and live-library rollout remain unauthorized.
- Tests use temporary configuration, library, module and Seed-VC roots, with model hub access disabled. They do not download weights or train against the live library.
- The live application and its library have not been moved or upgraded. Production backup, quiescence and restore verification remain prerequisites for applying a new schema to that library.
- The source PDF is currently unavailable at its original repository path. The full extraction used to prepare the plan remains available in temporary storage. No implementation action removed the PDF.

## Foundation changes implemented

### Model evidence and acknowledgements

Publisher declarations now distinguish code, weights, auxiliary components and source data. Bounded evidence includes observed document hashes, revisions and expected artifact identities. Expected publisher hashes are labelled as declarations; they are not a claim that installed bytes were verified.

Local acknowledgements are stored transactionally and bound to the exact component declaration, operation and digest. Changes to the declaration require review again. A separate local choice enables non-commercial experiments. Unknown recording and dataset rights remain unknown.

New setup, generation, training and transcription submissions enforce the requirements on the backend. The UI offers explicit review cards, publisher links and a current-state refresh action. Existing jobs, saved outputs, cancellation, idempotent reconnection and save-only retries remain available.

YuE training acknowledges its actual BF16 training weights separately from GGUF generation weights. Native proxy controls accept only server-owned model identities and supported precision options. Alternate raw inference paths cannot bypass the supported task admission route. The separate native loopback server remains a local-process trust boundary.

HeartMuLa remains retired.

### Export lineage

New ACE-Step, YuE and Stable Audio jobs capture backend-owned evidence when submitted and retain that snapshot when their track is saved. Legacy adoption and generic track saves cannot supply forged evidence through client parameters. ACE global adapters, source uploads and known opaque source selectors remain incomplete when trusted identities are missing. Supplied YuE ABC similarly retains the known model restrictions without claiming a captured upstream source/analyzer link. Verified base-only ACE and ordinary/managed-adapter YuE paths preserve their recorded evidence. Existing snapshots are not rewritten.

Export manifests carry recorded restrictions and adapter hashes through bounded track ancestry, including mixes and voice replacement. Absent, malformed, cyclic or truncated ancestry stays incomplete. Shared evidence preserves explicit public attribution and removes private labels and URL tokens. Old manifests remain readable.

The generic sample and conditioning aggregation boundary is covered by tests. Beat rendering now captures its sample identities and can hand a completed mix to Stable Audio conditioning. The Stable source path is verified from its pinned implementation; native inference and musical fidelity have not been measured in this execution.

### Storage and destinations

Local music checks PCM space before admission and worker execution, and checks publication space again. Publication refuses an existing final filename and retains complete pending audio for recovery. It never replaces that existing output.

ACE adapter export checks the complete checkpoint tree and a 64 MiB reserve. The review card shows required and available space before copying. Export creates a new named folder and refuses an existing destination. Disk-full errors preserve the original checkpoint and any recoverable partial copy.

YuE training forecasts decoded audio, latent temporary copies, retained LoRA and AdamW checkpoints, converted adapters and registration copies. Package preparation budgets archive work separately. Installer steps recheck their current download budgets and reject unexpected growth before starting that step.

Serialization, filesystem and vendor scratch allowances are conservative estimates. Hardware peak-disk measurements have not yet calibrated them.

## First shared analysis workflow implemented

The first analysis path uses existing NumPy and FFmpeg capabilities. Strict contracts cover retained version, stem and production-asset references; immutable source identity; bounded timelines; typed results and revisioned manual corrections.

The CPU feature kernel processes bounded windows and summarizes energy, spectral balance, generic sections and cautious key candidates. Candidate scores are uncalibrated correlations. Tempo, downbeats, meter and vocal activity remain unknown in this initial kernel. Silence, detuning, channel disagreement and weak evidence must not acquire invented certainty.

The collapsed **Music analysis** panel is connected to saved audio versions. Expanding it loads source-specific history and a space preview; starting work requires an explicit action. The backend retains private source snapshots, runs one owned CPU worker, records immutable raw results and keeps revisioned manual corrections separate. Identical audio on different sources can reuse computation without sharing corrections.

Reloading or closing the panel reconnects to existing work. Server interruptions become explicit retryable jobs. Verified completed private outcomes can be published on retry without repeating measurement; file existence alone is not sufficient. Shutdown closes admission and drains descendants. Unverified workers remain quarantined and block conflicting work. Track deletion and stem replacement coordinate with active analysis consumers.

Source copy and PCM writes are bounded, including malformed duration headers and externally growing sources. Tracks longer than 120 seconds use overlapping windows for energy; global key and sections remain unknown. The two-hour acceptance bound has not been measured as a performance capability.

See [saved music analysis](../../music-analysis.md) for controls, recovery, storage limits and interpretation. The baseline leaves beat/downbeat inference unknown; the optional pinned CPU method was added in the later pass recorded below. The shared alignment, stretch, pitch and mixing implementation and its measured native limits are recorded below.

### Browser and native CPU proof

An isolated app at `http://127.0.0.1:9002/music/yue2` uses a newly created temporary library and an explicitly synthetic eight-second C-major fixture. No live library or model engine is used. The browser flow showed the storage estimate, started one analysis job, displayed a C-major candidate and measured loudness, saved a manual 120 BPM correction, and restored that correction after reload and after a graceful backend shutdown/restart. Keyboard Tab moved from the disclosure to its refresh control. A 390 × 844 viewport check showed wrapped controls and no horizontal document overflow; the normal viewport was restored. Backend read-back confirmed one completed job, the saved correction and source/snapshot hash agreement. The screenshot is `/tmp/openfabric-music-analysis-browser.png`.

A focused review found no remaining concrete high/medium defect in seven reviewed regressions, which it independently ran. Its scope covered the forged-header PCM cap, source correction isolation, old source history, shutdown admission, completed-outcome recovery, reserved evidence stripping and safe editor ancestry IDs. A later provenance review identified the missing native `src_audio_path` selector; its completeness guard and regression were corrected. These reviews did not establish musical quality or cross-platform runtime support.

## Verification recorded so far

These are completed intermediate checks, not the final integration result:

| Check | Observed result |
| --- | --- |
| Initial focused baseline | 40 backend tests and 47 frontend tests passed |
| Terms service, routes and export storage | 51 tests passed before the later copied-library test was added |
| Terms migration and rollback | 14 tests passed, including copied SQLite backup restoration and preservation of an original track |
| Generation evidence writers | 85 tests passed |
| Native admission and proxy review | 71 tests passed |
| Training, packages and installer regression group | 162 tests completed; 1 Windows-only test skipped |
| Evidence and export provenance | 17 focused tests; 80 affected audio/video regression tests passed |
| Complete frontend suite after final analysis integration | 179 files, 1,888 tests passed |
| Frontend production build | Passed after final analysis integration, including strict TypeScript and policy checks |
| Configured strict Python platform loop | All configured groups passed for Linux, macOS and Windows typing paths after analysis integration; the later evidence-writer correction also passed focused strict checks on all three paths |
| CPU analysis contracts and feature kernel | 25 focused tests passed |
| Durable analysis, routes, worker and decode cap | 40 focused tests passed |
| Existing affected audio, video I/O and analysis integrations | 62 regression tests passed |
| Desktop tests | 88 tests completed: 87 passed, 1 Windows-only skip |
| Late evidence-writer regressions | 55 focused ACE/YuE/evidence tests passed after the final two source-selector cases |
| First complete backend run after analysis integration | 2,636 tests completed with 3 stem-operation regressions and 11 skips; these exposed an incorrect lock order |
| Corrected complete backend rerun | 2,640 tests passed, with 11 skips; the original three stem regressions and new deletion-safety tests passed |
| Explicit artifact cleanup | 20 contract/service/route tests passed; includes interrupted detach, changed source, conflicting staging and retained raw/manual results; strict typing passed on all three configured platforms |
| Storage review UI and client | 26 focused tests passed, including existing Settings regressions, explicit confirmation, partial recovery and uncertain network outcomes |
| Shared DSP and streaming alignment | 33 focused tests passed; three core source files passed strict typing on all three configured platforms |
| Rubber Band adapter | 5 focused tests and macOS strict typing passed; separate actual native probes are recorded below |
| Beat timing and MIDI core | 35 focused tests and strict typing on all three configured platforms passed |
| Beat page, API and navigation | 29 focused tests passed; an additional smaller-grid preservation regression passed after its RED reproduction |
| Beat publication | 2 transaction tests passed for missing and duplicate job attachments, including short-number rollback |
| Library relocation after beat integration | 42 tests passed using temporary libraries, including beat/analysis receipts and cleanup journals |

Final backend discovery, generated-contract drift checks, frontend suite/build, desktop checks and diff review must be rerun after the newer DSP, cleanup, Beat Maker and voice integrations are stable. Platform typing is not a Windows or Linux runtime test. No GPU inference or musician review is implied.

## Following slices in progress

Storage review now has a lazy Settings → Library panel and explicit two-step cleanup. Only completed analysis copies with matching retained originals are eligible. Planning and confirmation bind exact hashes, file signatures and owner revisions. An interrupted cleanup can be resumed from its privately owned staging folder; saved results and corrections remain in SQLite. Models, media, datasets, adapters, training checkpoints, unknown files and unverified workers remain protected. Inventory sizes are bounded logical-byte scans, not a full filesystem accounting or a promise of physical reclaimed space.

The shared DSP core now renders frame-exact gain/EQ/compression/fades, bounded ducking and non-normalizing mixes inside its caller's process ownership. Streaming phrase alignment supports bounded whole sources while retaining uncertain or oversized phrases as diagnostics. The native adapter separately verifies executable identity, frame counts and finite samples, uses linked stereo, records compensated headroom and rejects clamped output. See [DSP core](../../music-dsp-core.md) and [native validation](../../music-native-dsp-validation.md).

Beat authoring now includes backend-rendered FLOAT audio and aligned stems, MIDI, exact authoring JSON, saved revisions, deterministic transforms and an editor handoff. The browser rendered a full procedural beat, opened eight aligned editor lanes, followed its pinned revision back to Beat Maker and selected the completed mix as a Stable Audio reference. Audio selection did not start generation. Regional presets are connected but remain explicitly unreviewed and unavailable where required samples are absent.

## Integrated slices and current limits

- **Beat Maker:** usable authoring/render/MIDI/stems/editor flow. One full-render browser artifact had 103,200 frames at 48 kHz, eight aligned stems and SHA-256 `6bf7726952d366c0ce92f3d8ee91c6e037d705c0a32edd56a8cbfa3cdc5140a3`. The preview and full one-pattern render matched. Editor screenshot: `/tmp/openfabric-beat-editor-browser.png`.
- **Native DSP:** the managed Rubber Band 4.0.0 binary was installed and tested only in temporary storage. Uniform ten-minute conversion completed with exact frames. A long varying map exceeded the five-millisecond impulse target (5.458 ms); the thirty-second nonuniform limit remains. No singing-quality claim follows from synthetic probes. See the detailed native validation record.
- **Regional catalog:** 24 preset definitions, 15 rhythm targets, 32 scales and eight tunings are validated. Six tuning-dependent and two timing-dependent presets remain blocked. All require actual compatible sample assets; no procedural drum was relabelled as a regional instrument. Musician review remains unperformed.
- **Stable conditioning:** pinned source-verified audio-to-audio and inpainting paths, independent input copies, separate encoder setup, storage preflight and exact original PCM outside an inpaint region. No native model inference was run in this temporary app.
- **Voice replacement:** prepared/convert/hybrid plans, retained stages, manual alignment, measured mixing, reviewed RVC registration and owned cancellation/recovery are implemented and under integration checks. The CPU prepared-vocal workflow used real decoding/DSP/measurement with only the model boundary replaced. RVC/Seed inference and listening quality remain unverified here.
- **Mashups:** two retained sources, explained uncertain compatibility, confirmed fixed-tempo grids, ordered section planning, editable locked placements, preview/full stems and editor projects are connected. Both preparation and render remain backend-owned across navigation. Drifting live timing and real model separation/listening evidence are not established.
- **Dataset preparation:** source-scoped permission reviews, immutable source/crop hashes and grouped holdouts are implemented. Stable training receives only the selected training excerpts and checks their expected hashes before encoding. Held-out comparison and review UI are still being connected.
- **Storage review:** eligible analysis copies can be reviewed and explicitly removed with recovery; media, models, datasets, adapters, checkpoints and unknown files remain protected. No live cleanup was performed.

A broad integration run completed 2,892 backend tests with 11 skips, exposing five new test-module discovery import errors and one stale inventory expectation. Those failures were corrected and their focused discovery/history checks rerun. A subsequent full frontend run passed 1,990 tests in 201 files; a production build passed strict policy, TypeScript and Vite. Newer edits still require the final applicable checks.

Browser testing exposed a genuine cross-feature contract omission: published `beat` tracks broke library history validation. The shared origin contract now also accepts the concrete `beat`, `mashup` and `beat_song` publication origins; a regression covers library listing and favorites for each. Existing saved origins remain readable.

## Roadmap follow-ups recorded before the later integration pass

The later updates below supersede the software gaps in this intermediate list. External acceptance gates remain open.

- Finish retained-source conditioned queue integration and deterministic beat/song assembly.
- Complete held-out comparison/review integration and source-bound dataset/model cards.
- Add measured beat/downbeat inference through an explicitly reviewed optional runtime; manual timing remains the current fallback.
- Prove the pinned ACE Complete/Lego base-model path before enabling it; do not infer that Turbo/SFT can do it.
- Acquire source-specific sample assets with verified redistribution terms and actual musician review. Asset absence is not resolved by changing a badge.
- Record optional technology adoption/decline decisions, hardware measurements, copied-library upgrade/restore, recovery and accessibility evidence.
- Run all final backend/frontend/contracts/strict-platform/desktop checks and review the complete tracked and new-file diff before integration or release.


## Integrated verification update — 17 October, late pass

Implementation remains isolated on `feat/music-features`; the original checkout, live port 9000 application and user library have not been migrated or replaced. At that point there was no authorization to commit, push or merge; the later maintainer request explicitly authorizes integration into `main` and a fork push.

The retained-source queue is connected for Stable audio-to-audio/inpainting and ACE Base Complete/Lego. Acceptance snapshots the source once for all variations, preserves captured usage evidence and survives loss of the original after acceptance. Intake journals and per-source receipts recover a failed queue publication without creating another orphan copy. Explicit source review does not start native inference. ACE setup explicitly requests Base with its decoder/text encoder, leaves ordinary generation defaults intact, and runs only through an owned offline process specification. Complete/Lego return generated full mixes; neither promises an unchanged beat, isolated additions or an exact duration. The initial source window is 10–300 seconds and 512 MiB. Vocal roles require a vocal brief and non-instrumental lyrics. Native ACE GPU execution and listening quality remain unverified.

The deterministic beat/song mixer has completed a real browser-driven CPU render. A two-second authored beat was recompiled four times and mixed with an eight-second synthetic song. Job `b629f546f74e493582ad074f5cdb413f` produced 384,000 stereo frames at 48 kHz, nine aligned processed stems and an editor project. Summing the stems differed from the FLOAT master by at most `1.4901161193847656e-08`. The master SHA-256 is `6b292b807e6127b005c0ec2967fd9af351babe6a1546044a7ea1e6ffc9f65fa4`. It saved as temporary library track 3/project 2; the browser opened all nine audible lanes plus muted dry/master references. The observed run took 48.30 seconds alongside other checks, not a controlled performance benchmark. Screenshots: `/tmp/openfabric-beat-song-browser.png`, `/tmp/openfabric-beat-song-editor-browser.png`; machine-readable proof: `/tmp/openfabric-beat-song-browser-proof.json`.

The optional `music-beats-v1` analysis profile now uses explicitly selected, pinned beat_this small0 on CPU. The original manual/unknown baseline remains unchanged. The actual offline synthetic probes, temporary environment size, package/weight identities, cancellation and retained-checkpoint retry are documented in [the validation record](../../music-beats-validation.md). A 130-second synthetic fixture crossed the 120-second analysis boundary and completed through real decoding, owned CPU children and SQLite publication. All 260 reference beats matched within 70 ms, with one extra predicted beat. This does not establish accuracy on real songs or regional rhythms. Tempo confidence and meter remain unknown, and the estimate exposes half/double ambiguity. No GPU model or user media was used. The temporary proof environment was relocated into the isolated demo module root and revalidated; no second large environment copy or live engine change was made.

Optional analysis cleanup accepts only the exact retained checkpoint, bound to the completed job's typed request/result, pinned identity and source proof. It leaves failed/cancelled/unverified jobs and unknown/window leftovers protected. Installed model files are never traversed or deleted by analysis-copy cleanup.

Regional dataset preparation and held-out comparison/review are connected to the existing dataset library and Stable trainer. Training excerpts carry immutable source/crop hashes, source-scoped permission records and group-separated evaluation material. Comparison records bind real completed base/adapter outputs to matching settings, output hashes and held-out sources. Listening reviews are identified records; they are not a blinded panel, FAD/CLAP score, public release approval or proof of musical quality. Optional acoustic near-copy screening was added in the later pass below; it is heuristic and does not establish independent recordings.

Replacement action/create requests now retain exact idempotency identities across uncertain network outcomes and reloads. Failed jobs with unresolved active workers remain cancellable. Concurrent request, mix and phrase/alignment drafts survive newer saved revisions; the UI blocks mutations until explicit review or discard and advances only the group acknowledged by its own save. The same-revision mashup refresh and conditioned-queue catalog refresh preserve pending edits/submission identities.

A copied synthetic baseline-library exercise upgraded 38 tables to 72 while preserving every legacy row/column, original audio, editor project and training record. Injecting a migration interruption preserved integrity and completed on retry. The archived old backend could read the upgraded library; restoring the backup returned the exact original database bytes and 38-table schema. All four source/training hashes remained unchanged. Evidence is `/tmp/openfabric-migration-proof.4c5fUT/proof.json`; this was not a migration of the user's library.

Verification observed before the current final backend pass: 3,022 backend tests passed with 11 platform skips; 2,043 frontend tests passed after ACE UI integration; strict TypeScript/policy and Vite production build passed. The newer optional-runtime, cleanup and ACE backend changes have focused regression evidence, and another full backend/contracts/desktop/platform loop is running. Do not read these earlier counts as the final integrated result.

At this intermediate snapshot, diagnostics, acoustic screening, final review and checks were still open. The later pass below adds those software capabilities. Real model/separation/conversion inference, independent musical evaluation, authentic regional assets and musician review remain acceptance gates. No release or live-library rollout has been performed.


### Review corrections and recovered browser analysis

The integration reviews found and corrected four concrete boundaries: Windows source identity used native separators instead of portable manifest names; partial beat setup understated dependency/checkpoint space; a valid detector event at the exact clip endpoint failed validation; and a pristine ACE initializer could enter unreviewed bundle-download paths. Regressions cover each. The beat worker validates the complete returned sequence, then omits only events at/past the endpoint within one 20 ms model frame; nonfinite, negative, unordered, duplicate or further out-of-range values remain invalid. The ACE source proof now includes initialization/download helpers and the separately hashed local-only launch bootstrap. It refuses native HF and ModelScope entrypoints before API imports capture them. Ordinary launches and installed checkouts are unchanged.

The previously failed browser analysis was explicitly retried as the same saved job, `ad85fddd63bc44e5b69b4b9cd91e866a`, attempt 2. It completed in 2.084 seconds with eight estimated beats, four downbeats and the same source SHA as the saved mix. Its descriptive tempo was **60 BPM with half/double ambiguity**, despite the authored grid being 120 BPM. Confidence is null; meter is unknown. This is evidence for recovery and honest uncertainty, not a claim of perfect beat detection. Worker identity was cleared after drain. A full backend restart preserved both the mix and result; reopening the result did not rerun analysis. Proof: `/tmp/openfabric-cpu-analysis-browser-proof.json`; screenshot: `/tmp/openfabric-cpu-analysis-result-browser.png`.

The recovered result now explains its actual saved detector capabilities independently of the method selected for a future job. Saved mixed tracks lazily open the common audio-version, analysis and export tools, avoiding a request fan-out for unopened history rows. Replacing a picker-selected reference with a later handoff no longer aborts the new inspection. Narrow-screen checks at the narrow test breakpoint found equal document/client widths (380 CSS pixels) for the queue and mixer; this is a layout check, not exhaustive device/accessibility certification.

A final integration run exposed a support-report bound still fixed at 17 modules. It now derives capacity from the shared `ModuleId` literal rather than another manual count. All 22 support regressions passed, generated contracts were regenerated and checked, and the final frontend run passed **2,047 tests in 208 files**, followed by strict TypeScript/policy and Vite. All **21 configured Python type-check groups passed for each of Linux, macOS and Windows**. Desktop checks completed 88 tests: 87 passed, one platform skip. The post-fix full backend rerun completed **3,081 tests in 524.730 seconds, passing with 11 platform skips**. These checks cover the core workflow snapshot; the next diagnostics and acoustic-screening additions require fresh applicable verification.

Rollback evidence is deliberately limited: the archived old backend read preserved legacy records in the upgraded schema. This does not establish that old code understands newly created beat/mashup/replacement records. A downgrade must use a consistent backup and preserve any new outputs separately; it must not silently delete unfamiliar data. No live-library upgrade, downgrade, migration or release was performed.

### Current program state

| Phase | Implemented software | Remaining acceptance work |
| --- | --- | --- |
| 00 | Evidence, terms boundaries, durable source snapshots, disk preflight, protected cleanup | Source-specific rights review remains an operator responsibility |
| 01 | CPU features, opt-in beat timing, manual corrections, bounded shared DSP and verified stretch adapter | Real-song/held-out accuracy, non-macOS native evidence, long varying-map limits |
| 02 | Prepared/convert/hybrid replacement, saved alignment/mix stages, reviewed RVC registry, sampled pitch/tonal/correlation guidance, recovery | Actual Seed/RVC listening tests and held-out diagnostic calibration; observed pitch is not full singer-range qualification |
| 03 | Editable saved beats, deterministic rendering, MIDI/stems/JSON, revision/editor handoff | Musical review of authored content |
| 04 | Regional preset/scale/tuning catalog and sample-pack validation | Authentic licensed assets and musician review; unavailable presets stay blocked |
| 05 | Stable/ACE source queue, retained input, explicit model setup, offline ACE launch, deterministic beat/song mixer | Actual model generation/listening evidence; no exact-duration or preserved-source claim |
| 06 | Retained two-source mashup planning, inspected compatibility, placements/render/editor recovery | Actual separation and musical listening evidence; drifting live timing unsupported |
| 07 | Permission/crop/group review, bounded acoustic candidate scans and reviewed grouping, held-out material, Stable training handoff, matched output comparisons and listening records | Real-recording detector calibration, independent benchmarks/panel and release approval |
| 08 | Recorded adoption/defer decisions and bounded native CPU experiments | Deferred technologies reopen only when their stated evidence conditions are met |
| 09 | Full backend/frontend/contracts/typing/desktop checks, isolated browser probes, copied upgrade/rollback exercise | Human/native acceptance and explicitly authorized integration/release |

This is an experimental software implementation of the core workflows, not completion of every research, asset-acquisition and musical-release gate in the full roadmap. Those remaining gates stay explicit instead of being replaced by a green badge.

### Diagnostics and acoustic screening integration

This software pass adds bounded pitch and spectral/lagged-correlation observations over retained replacement artifacts, plus explicit acoustic-candidate screening before regional dataset splitting. These are measured advisory signals, not singer identification, legal clearance or musical-quality scores. Suggestions require explicit review. No new GPU model or automatic download is planned.

A regression first reproduced preparation overwriting already-reviewed timing for unchanged retained reference/prepared inputs. Preparation now preserves the exact current alignment and review when both artifact identities/hashes match. The original CPU workflow regression passed after that correction; all 35 replacement owner/store/contract tests then passed in 14.578 seconds. This is an intermediate check before the new diagnostics integration, not the final program result.

The replacement report binds exact request, revision, alignment and retained artifact identities. Compatible existing CPU environments are probed without installing dependencies. Missing runtime/support remains unavailable; verified-drain worker failures are advisory while source/integrity failures remain fatal. Recommendations enter drafts only. Re-running preparation retains reviewed timing for unchanged inputs. The maximum timing ratio now uses the shared 0.75 compression floor consistently in preparation and manual editing.

Acoustic scans use the existing regional owner, bounded spectral landmarks and optional retained audition pairs. Candidate pairs remain grouped by default. Human distinctness decisions need identified reviews and paired retained clips and cannot break exact/provenance relationships or transfer permissions. Accepted scan requests retain their exact idempotency identity across lost connections and invalid successful responses. Unsaved review decisions survive history refresh and newer saved revisions; explicit re-review or discard is required before rebasing.

The complete frontend suite passed **2,070 tests across 210 files** after UI recovery integration, followed by a successful strict TypeScript/policy production build. Desktop checks completed **88 tests: 87 passed, one skipped**. These are completed intermediate checks before the final backend review corrections and schema regeneration; final applicable verification is recorded next.

### Latest browser and copied-library evidence

An isolated browser test used three newly generated twelve-second CPU fixtures, not user recordings. The scan completed as `05e18097a314404387f065ad60447c04`, found the expected polarity/gain-altered pair and kept it together. The browser saved the report to review revision 2 and showed two training recordings, one held-out recording and two source groups. Both retained auditions served HTTP 206 ranges. One played in Chrome with an observed 11.776-second duration and no media error; playback was stopped. This is decoder/UI evidence, not a listening-quality judgement.

Graceful backend restart and page reload preserved exactly one scan job and its result; reopening did not start another scan. Read-back verified all three original file hashes unchanged and no distinctness override saved. Evidence: `/tmp/openfabric-acoustic-browser-proof.json`; screenshot: `/tmp/openfabric-acoustic-browser-grouped.png`. The isolated app remains on loopback port 9002 with temporary configuration, library and engine roots. The existing port 9000 app remains unchanged.

The copied synthetic upgrade/restore exercise was rerun against the acoustic schema. It upgraded **38 to 73 tables**, retained every legacy row/column, passed SQLite integrity/foreign-key checks and preserved original/training files. Injected migration interruption recovered on retry. Complete backup restoration returned to the original 38-table schema and exact pre-upgrade bytes. The archived backend could still read the legacy records; this does not qualify new-workflow records for downgrade. Current proof: `/private/var/folders/tm/03q99_3145b9ntglkf3bw1mh0000gn/T/openfabric-migration-final4-0owt0hp7/proof.json`.

Final generated-contract drift check, frontend suite (**2,070 tests / 210 files**) and production build passed after the last schema regeneration. All **21 configured strict Python groups × three platform paths** passed with separate caches. Desktop checks remain **87 passed / one platform skip**. The definitive full backend result is recorded in the final verification table below.

The final UI review also reproduced reattaching a scan dropping an existing identified `keep_together` decision. It now preserves that review record unless the reviewer explicitly changes the candidate decision. The complete frontend rerun passed **2,071 tests in 210 files**, and strict TypeScript/policy plus the production build passed again. Browser reload of this final build restored the same completed preparation without another scan or preparation.

Preparation `7ee3f2860d454c97b6edee024d6a3703` completed all six source/crop items. Its retained manifest SHA-256 is `c78f762358a860f85c34757683dde1bd6f03c7d7fdd1051266bf6972ccee3e4e`; API read-back verified the report and human-decision snapshot, the paired recordings’ shared cluster and the separate held-out excerpt. No trainer was started and no publisher terms were accepted during this test.

### Final verification of this implementation pass

| Check | Actual result |
| --- | --- |
| Complete backend discovery | **3,169 tests completed in 580.217 seconds; OK with 11 platform skips** |
| Complete frontend suite | **2,071 tests in 210 files passed** |
| Frontend production build | Strict TypeScript, policy checks and Vite passed |
| Generated contracts | Regenerated from schemas; final `--check` passed |
| Strict Python platform paths | **21 configured groups × Linux/macOS/Windows = 63 successful runs**, isolated caches |
| Desktop | **87 passed, one platform skip** |
| Browser CPU workflow | Scan → restart/reopen → conservative grouping → retained preparation/manifest passed on synthetic audio; originals unchanged |
| Copied library upgrade/restore | 38 → 73 tables, interruption/retry, preserved legacy data/files and byte-exact consistent backup restoration passed |
| Final diff review | Tracked and new-file scope inspected; no model/cache artifacts, conflict markers or whitespace errors found |

The first complete diagnostics/acoustic run exposed an existing ambiguous-spawn receipt regression: a worker without a persisted identity could release its owner too early. The reviewed fix retains bounded receipt-based ownership and cold-restart quarantine until verified drain. The definitive rerun above passed that regression. Missing-source terminal errors, manifest decision retention and the valid maximum-stretch boundary also have regression evidence.

The software implementation is ready for experimental review in the isolated worktree. This does **not** complete the report’s release acceptance: actual model generation/separation/conversion, held-out real-song listening and detector calibration, authentic licensed regional sample assets and musician review remain open. No regional adapter was trained for this report, no public musical-quality claim was established, and deferred embedding metrics remain unavailable. During implementation, the original checkout, live library and port 9000 process were preserved; nothing was committed, pushed, merged, published or released. The maintainer then authorized committing all changes into `main` and pushing to the fork. That Git integration does not authorize a live-library migration, application restart or public release.
