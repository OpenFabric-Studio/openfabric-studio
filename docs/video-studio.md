# Video studio

Create a saved project from a library song, define the direction, edit the storyboard, preview selected shots, and approve variants before export. Projects and jobs live in the data folder, independently of the browser. Changing the source audio requires a new project. Name and musical-marker edits preserve approved clips; changing generation inputs invalidates their approval. Export framing and timed text preserve clip approval and invalidate only the assembled output.

## Managing saved projects

Choose **Manage projects** beside the saved-project selector to open the project library above the wizard. Search by project/song name, filter by job status, and open, download or delete a project. The selected project is marked. Rename it in **1 Song**; use **Duplicate project for comparison** in **2 Direction** to retain the original while trying another approach.

**Delete project** asks for confirmation with the project name. It permanently removes that project's saved storyboard, uploaded reference copies, preview variants and rendered video files. The source song and other projects remain in the library; files already downloaded elsewhere are unaffected. Open an active project and cancel its job before deleting it. The backend also drains owned image uploads and processing that raced with the request, and refuses deletion if a worker cannot be safely identified or stopped.

Deleting the selected project returns the wizard to **1 Song**. Deleting another project retains the current draft and step. Pending autosaves finish before removal, stale polling cannot restore a removed entry, and failed deletion keeps the project and recoverable edits available for retry.

## Testing locally

Install the updated backend dependencies in your existing backend environment, then restart the API and frontend. From the repository root:

```sh
uv pip install --python backend/.venv/bin/python -r backend/requirements.txt
```

In one terminal:

```sh
cd backend
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 9000
```

In another:

```sh
cd frontend
npm run dev
```

Open the frontend URL printed by Vite and choose **Video**. For a quick test without generation weights, choose **Animated cover** or **Audio visualizer**, upload an image, analyze the song, and preview a 2–4 second shot. These paths use FFmpeg on the CPU; audio analysis additionally requires NumPy, and timed text requires Pillow. Approve a preview; render fills missing shots and assembles the song. **Export approved clips** requires valid approvals for every shot. Exact 16:9, 9:16 and square framing is applied by cropping at export.

If analysis reports unavailable tools, install the requirements in the **same Python environment that runs the API**, rather than in the separate generation-engine environment. Direction shows audio-analysis readiness independently of generation models. Missing tools, unreadable audio and timeouts return distinct errors. Restart an API process started without `--reload` after code updates; refreshing the browser does not reload Python modules.

Cover/visualizer modes disable model, diffusion, prompt and reference-strength controls because those controls do not affect their FFmpeg animation. Their shot seed selects one of two movement directions. The project seed is a default for new/analyzed shots; existing shot seeds and approved outputs are preserved when that default changes.

## Generated scenes

The current generated-scene engine is the pinned Apple MLX checkout at commit `1724ca673d59f023a8a95efee06e5d36d61c2765` (0.15.12). The setup preserves existing modified checkouts and checks the installed dependency environment without deleting unrelated packages. Compatibility patches apply inside the render child process and are checked against source hashes. Both A2V generation stages receive the requested tiling. Refinement supports **1–3 actual steps**.

Inspect setup before installing or downloading:

```sh
./setup_video.sh --help
./setup_video.sh --preflight --cache-dir /absolute/path/to/data/models/ltx
```

Install/check the engine with `./setup_video.sh`. Model downloads require an explicit command:

```sh
./setup_video.sh --download-models --cache-dir /absolute/path/to/data/models/ltx
```

Use the actual data folder displayed in the app. Setup downloads only the pinned required files, checks their content, and records reusable verification receipts. Rendering fails with an actionable code when required files are absent; it never silently downloads weights. The Direction step shows installed packs, total required bytes, still-uncached bytes and free disk. LTX-2.5 is an optional experiment, selectable only when installed and compatible; it is not a verified quality upgrade.

## Timeline, quality and recovery

- Source audio stays on its original timeline. Gaps and the final short tail repeat an approved frame instead of shortening the song or shifting later shots.
- Musical analysis reports waveform, energy and estimated onsets/beats/transitions. Listen before snapping a scene. Manual markers and protected shots survive re-analysis. Lyric text requires explicit start/end times; there is no automatic word alignment.
- Storyboard energy uses measured timestamps, including decimated analysis of longer sources. Text layout is checked against the final export aspect before rendering begins, so oversized text is rejected before generating scenes.
- Each preview keeps its seed, effective prompt, settings, source/model fingerprints, media and timings. Approvals are checked against current inputs. Retry/resume reuses validated completed work and retains earlier variants for comparison.
- Child process ownership, cancellation and startup recovery preserve validated outputs and reject ambiguous worker identity. GET polling does not rewrite job metadata. Generation admission checks active music, training and native requests on the backend.
- Approved-clip export is CPU work and can run alongside model work. It still checks clip/source/model provenance and unresolved workers, and CPU video jobs share one processing slot. Updating backend implementation fingerprints can require regeneration of earlier previews; their retained files are not deleted.
- Elapsed time uses persisted timestamps. Remaining-time estimates describe only a measured current phase; overall time remains “estimating” when comparable evidence is absent.

CPU media tests verify stream/duration/frame-rate/dimension validation, covers, visualizers, timed text, framing, reuse and recovery. Installed-engine CPU tests verify the real tiling wrappers. **Perceptual quality, full-song GPU time and peak memory have not been established by these tests.** Compare a few fixed-seed short shots with the same source, prompts and references before committing to a full song.
