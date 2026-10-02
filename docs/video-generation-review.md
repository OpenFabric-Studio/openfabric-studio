# Video generation review — 2026-10-01

Scope: current Remiqora frontend, backend job/assembly pipeline, installed MLX engine, and authoritative upstream documentation. This review changes documentation only. No GPU generations, model installations, weight downloads, or changes to the user's library were performed.

Recommendation: repair job ownership, recovery, media validation, and engine settings first. Then introduce a saved project with a short preview and per-shot iteration. Test reference-image conditioning and musical timing before spending more compute on larger renders or another model.

## Verified current behavior

- The app invokes `a2v` with LTX-2.3 q8, Gemma 3 12B 4-bit, 24 fps, two-stage generation, and low-RAM streaming. Each shot launches a separate engine process. Shots are assembled locally and the original song is muxed back as AAC.
- The installed engine is `ltx-2-mlx` 0.15.12, commit `1724ca673d59f023a8a95efee06e5d36d61c2765`. Live upstream metadata showed the same HEAD during this review. Updating to current HEAD does not fix the A2V settings defects below.
- Planning uses style/section templates and one-second loudness estimates. Lyric sections are distributed approximately by line count and song duration. Individual lyric text is discarded when constructing visual prompts. There is no detected beat grid or timestamped lyric alignment.
- The browser generated 33 shot forms for the selected 4:20 song. At a 390-pixel viewport the resulting page was approximately 13,696 pixels tall. There was no horizontal overflow or browser page error in the reviewed states.
- Draft prompts/settings are component-local. Navigating to Editor and back removed all shot forms and the custom prompt entered during the review.

Sources: [app inference arguments](../backend/app/video_jobs.py#L650), [planner](../backend/app/video_jobs.py#L514), [frontend](../frontend/src/views/video/VideoPage.vue), [pinned upstream CLI](https://github.com/dgrauet/ltx-2-mlx/blob/1724ca673d59f023a8a95efee06e5d36d61c2765/packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/cli.py).

## Defects to fix first

### P1: generation ownership and resource admission

`_spawn` starts a separate process session. Graceful cancellation owns cleanup, but a hard backend crash does not kill that independent child. Recovery only marks metadata interrupted; it stores no worker identity and does not terminate an orphan. A CPU fixture killed the parent with SIGKILL and confirmed the child continued afterward. All fixture processes were subsequently cleaned up.

The server also does not enforce the frontend's `other_work_busy` check when admitting video work. `start_video` excludes another video, and generation takes `stems.gpu_lock`, but that lock does not coordinate native ACE generation/training. Another tab or a direct request can bypass the page's advisory restriction. Memory exhaustion from this overlap is a risk, not a reproduced GPU result.

Use backend-owned resource admission across the affected jobs. Add verifiable worker identity/ownership and crash recovery. PID alone is insufficient because it can be reused. Keep this within the existing local architecture; a distributed queue is not required for one machine.

Evidence: `backend/app/video_jobs.py:967`, `:861`, `:1081`, `:1448`; `backend/app/job_lifecycle.py:74`; `backend/app/api/routes_videos.py:41`.

### P1: advertised tiling does not protect A2V generation

Remiqora passes `--tile-frames 2` for long/large shots and `--tile-spatial 2` for the largest size. The installed A2V CLI does not forward tile configuration to its pipeline, and the overridden A2V implementation bypasses its parent's tiled transformer wrappers. The flags are parsed but do not provide the stated memory protection.

Fix both integration layers with a tracked, compatibility-checked change and behavioral tests, or restrict settings that rely on this protection. Establish actual hardware limits with measured inference. No OOM threshold was measured in this review.

Evidence: `backend/app/video_jobs.py:687`; `external/ltx-2-mlx/packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/cli.py:1385`; `a2vid_two_stage.py:283` and `:379` in the same package.

### P2: misleading refinement controls

The app accepts 1–8 refinement steps; the UI offers up to 6 and Sharper requests 4. The actual refinement schedule has only 3 steps. CPU execution confirmed that requests for 4 and 8 both run 3. Sharper still changes first-stage steps and guidance, but its fourth refinement step does nothing. More first-stage steps or stronger guidance have not been demonstrated to improve perceptual quality for this app.

Restrict the control to effective values and label presets by their compute settings until comparisons justify quality claims. Tests must verify what the vendor executes, rather than only the presence of CLI arguments.

Evidence: `backend/app/video_jobs.py:636`; `frontend/src/views/video/VideoPage.vue:51`; `external/ltx-2-mlx/packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/scheduler.py:123`.

### P2: incorrect first-run download estimate and unpinned setup

The UI says approximately 21 GB plus a text model. The engine calls `snapshot_download(repo_id)` without a revision or file filter. At inspected model revision `6671a7572a530862d1d60ce393b5d93491e3f76b`, repository metadata totals 87,511,991,375 bytes, approximately 87.5 GB / 81.5 GiB, before Gemma. This is remote repository size, not a measurement of bytes still needed on this computer. Existing cached files can reduce the download.

Pin engine/dependency/model revisions, resolve the exact files needed by this A2V configuration, and provide a separate setup/preflight showing uncached bytes, free disk space, compatibility, and progress. Validate a file manifest and record job provenance. Avoid downloading unused transformer variants and unrelated adapters. Setup should not remove an existing checkout merely because one expected file is missing.

Evidence: `setup_video.sh:7`; `frontend/src/locales/en.ts:223`; `external/ltx-2-mlx/packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/utils/_orchestration.py:36`; [inspected model revision](https://huggingface.co/dgrauet/ltx-2.3-mlx-q8/tree/6671a7572a530862d1d60ce393b5d93491e3f76b).

### P2: polling mutates state and races with generation

GET handlers reconcile interrupted jobs using a snapshot of the live job. A new job can start between that snapshot and reconciliation, causing polling to mark a live job `failed/interrupted`. Concurrent reconciliation also writes through the same `video.json.tmp` filename. Controlled thread fixtures reproduced both a live/persisted status disagreement and a `FileNotFoundError` from competing writes.

Perform recovery under coordinated ownership; ordinary polling should read. Serialize read-modify-write transactions and use unique atomic temporary files. Atomic rename alone does not serialize a transaction.

Evidence: `backend/app/video_jobs.py:763`, `:861`, `:886`.

### P2: publication lacks media validation and crash adoption

Generation checks exit status and output existence, not expected media content. A synthetic 0.5-second MP4 passed real ffmpeg muxing for a requested 4-second shot and was published as ready. Probe required streams, dimensions, frame rate, duration, and decodability before accepting a shot or final export.

Final MP4 replacement precedes the ready metadata write. A crash between them leaves completed output behind; recovery marks the job failed and the serving endpoint refuses it. Validate and adopt completed outputs during recovery, using saved job/source/settings identity.

Evidence: `backend/app/video_jobs.py:1088`, `:1345`, `:913`.

### P2: accepted overlaps accumulate timing drift

Validation allows 250 ms overlaps, while assembly concatenates complete shots. Forty accepted 4-second shots starting every 3.75 seconds produced 160 seconds of pieces against a planned endpoint of 150.25 seconds: 9.75 seconds of drift. Assembly must trim to timeline boundaries or validation must reject overlaps using frame-level rules.

The single-shot branch also skips timeline gaps/tails. A real CPU fixture with a 5-second song and one planned 4-second shot produced a ready 4-second output even though `timeline_duration` returned 5. Define excerpt versus full-song output explicitly and use one assembly policy for both single and multiple shots.

Evidence: `backend/app/video_jobs.py:569`, `:574`, `:1260`, `:1292`.

### P2: stale analysis and lost edits

While analysis is pending, the song selector remains enabled. `onAnalyze` assigns its response without checking the original track or a request-generation token. A browser-only delayed-response fixture confirmed that the first song's plan is applied after selecting a second song.

Use track/request identity guards and cancellation. Save drafts per project and protect edits before replacing a plan. Re-analysis currently replaces the entire editable shot list. Changing one shot from 8 to 12 seconds leaves the next shot at 8 seconds, producing an overlap and disabling creation; provide ripple editing or an explicit fixed-position mode with inline errors.

Evidence: `frontend/src/views/video/VideoPage.vue:171`, `:187`, `:363`.

## Recommended product flow

Use a saved project workspace with **Song → Direction → Storyboard → Preview → Render & Export** stages. Keep navigation available and show the next useful action. Save the song/source identity, direction, references, shot plan, settings, seeds, versions, and approved outputs independently of the browser.

1. **Song:** searchable picker, audio player, waveform, excerpt range, duration, and optional detected musical markers. Separate full-song and excerpt modes.
2. **Direction:** choose performance, narrative, abstract visuals, or cover-art motion; define subject, environment, lighting, palette, and recurring details. Allow reviewed reference images and indicate their framing/crop.
3. **Storyboard:** compact timeline/cards with an expanded editor for the selected shot. Show section, source time, prompt, reference, coverage gaps, and validation locally. Provide duplicate, split, reorder, ripple-length editing, undo, and selective re-analysis that preserves locked shots.
4. **Preview:** generate one representative 2–4-second scene, or a small selected set, before the entire song. Audition it with the source excerpt. Compare variants with fixed seeds/settings, approve one, and keep approved shots reusable.
5. **Render & Export:** show per-shot queued/running/ready/failed states, completed previews, measured progress, and retry only affected shots. Persist completed clips and resume after interruption using validated source/settings/model fingerprints. Export using explicit format and encoding settings; provide a download button and project duplication.

Do not assume the process needs a resident model server. Each current shot reloads a process, but the vendor uses low-RAM streaming, switches stage weights, and frees model resources before decoding. Profile loading, text encoding, denoising, decoding, export, and peak memory before implementing safe reuse.

## Quality improvements worth testing

**Reference images/keyframes:** the installed A2V pipeline already accepts image conditioning, but the app exposes none. Use images to anchor appearance and composition, with recurring visual descriptions across shots. This is a promising control mechanism, not a guarantee of consistent identity. [Installed pipeline options](https://github.com/dgrauet/ltx-2-mlx/blob/1724ca673d59f023a8a95efee06e5d36d61c2765/docs/PIPELINES.md), [vendor image guidance](https://docs.ltx.io/open-source-model/usage-guides/image-to-video).

**Musical timing:** detect beats/onsets and musical sections, display confidence, and allow corrections. Align cuts to reviewed beat/bar/section markers. Generate engine-compatible durations and trim at frame-aligned edit boundaries; arbitrary eight-second divisions are not musical structure. Timestamped lyric alignment should be optional and its uncertainty visible.

**Prompt direction:** use editable subject/action/camera/lighting descriptions tailored to the actual excerpt. Prefer focused motion and coherent scenes. Remove the arbitrary 400-character constraint only after validating a sensible tokenizer-aware bound; expose a useful character/token indication. Add critical lyrics, titles, and logos during compositing so spelling can be controlled. [Vendor prompting guidance](https://docs.ltx.io/open-source-model/usage-guides/prompting-guide).

**Export:** offer landscape, portrait, and square targets with engine-compatible generation dimensions and an honest crop/pad preview. None of the three current sizes is exactly 16:9. Keep 24 fps as the measured baseline; changing an export label to 60 fps adds no generated motion detail. Specify video/audio encoding quality and minimize unnecessary lossy re-encoding when concatenating compatible clips.

**Model evaluation:** compare LTX-2.5 with the existing model using the same excerpts, references, prompts, and seeds. Current upstream documents additional modes, but they are not all exposed by the current A2V command. A model-ID change alone will not enable a different decoder or high-quality generation path. For visuals that do not need audio-driven performance, a separate image-to-video path followed by original-song muxing is a reasonable experiment. [Official model](https://huggingface.co/Lightricks/LTX-2.5), [MLX maturity matrix](https://github.com/dgrauet/ltx-2-mlx/blob/1724ca673d59f023a8a95efee06e5d36d61c2765/docs/PIPELINE_MATURITY.md).

**Lower-cost alternative:** a cover-art motion/visualizer mode can combine deterministic camera motion, typography, waveform or beat-reactive effects, and the song. It needs fewer generated assets and offers a usable fallback while complex AI shots are being reviewed. Choose this as an explicit creative format.

## UX and accessibility

- Replace the whole-song default render target with an explicit full-song choice and a clear short-preview action.
- Give setup, unavailable engines, blocking jobs, field errors, and failures actionable recovery steps. The current busy message does not identify the job that must finish.
- Show total-project and current-shot progress separately. The current ETA uses a 36-second fallback per denoise step and omits major phases; it is not a hardware benchmark. Calibrate estimates from compatible completed jobs and show uncertainty until sufficient measurements exist.
- Browser axe audit confirmed the video-length slider has no accessible name. Selected size/quality chips have no `aria-pressed` or equivalent selection semantics. Progress bars also need names, and asynchronous status/errors need appropriate announcements.
- Measured quality chips were 34 pixels tall and Analyze was 38 pixels tall on mobile; enlarge interactive targets and compact text without obscuring actions.
- Add posters and `preload="metadata"` to result players, release hidden playback, and paginate/filter the result library as it grows. Provide source, duration, seed, settings, render time, and model version without burying the main preview.

## Implementation order and verification

1. **Reliability/settings:** crash ownership, backend resource admission, read-only polling, serialized metadata updates, media validation, timeline correctness, actual refinement limits, verified memory controls, pinned setup, and accurate download preflight.
2. **Saved workspace:** guarded requests, persistent drafts, compact timeline, source audition, one-shot preview, per-shot status/retry/resume, fixed-seed comparisons, and accessible controls.
3. **Quality experiments:** references, musical timing, better direction, export profiles, controlled LTX-2.5 evaluation, and optional lower-cost visualizer mode.

Use backend schema types as the source of truth, validate untrusted artifacts, and extend strict checking to the new video domain types. Avoid new `any`, unsafe assertions, broad dictionary-shaped models, or UI-only validation.

Verification performed during this review:

- Root ran the current video backend suite with temporary config/data paths: **14 tests passed**.
- Root ran the mounted video frontend suite: **2 tests passed**.
- Independent backend review ran the combined video/job-lifecycle suites: **33 tests passed**.
- Browser checked actual desktop/mobile page and CPU-only song planning; reproduced navigation draft loss, stale-track analysis with an intercepted response, and overlap after length editing.
- Browser accessibility audit found the unnamed length slider; source/DOM inspection verified missing selected-chip semantics. Gradient contrast was inconclusive, not a claimed failure.
- Temporary CPU fixtures reproduced recovery/write races, surviving crash worker, truncated ready output, final-output recovery refusal, overlap drift, and single-shot tail loss. Real ffmpeg was used for the stated media fixtures; generation was mocked.
- Installed vendor CLI/scheduler CPU inspection confirmed parameter compatibility and refinement clamping. Live repository/model metadata verified version and pack size.

Existing tests passing does not negate these defects: most video tests currently check planning helpers and CLI construction, with very little full job/media behavior coverage.

Unknowns: perceptual quality gain from any recommendation, real-song synchronization accuracy, safe long/large-shot memory limits, full-song wall time, optimal settings, and whether a newer model improves this user's material. Establish a representative fixed evaluation set and record appearance consistency, motion artifacts, temporal stability, musical synchronization, rendering time, peak memory, and failures before claiming improvements.
