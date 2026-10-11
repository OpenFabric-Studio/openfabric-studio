# Video studio

Choose one of three workspaces at the top of **Video**:

- **Create video:** start from a library song, a silent video or a reel, then follow **Source → Direction → Storyboard → Preview → Export**. Character references and training are optional tools under Direction.
- **Cut a movie:** choose a local movie, preview and mark a scene, then save an MP4 with its audio. This uses local processing.
- **Replace a character:** prepare a short clip locally, review the paid character-frame step, then review that frame before approving the separate paid video step through OpenRouter.

Only the selected process is shown. Switching tabs retains unfinished inputs during the current visit and pauses hidden playback. Arrow keys, Home and End navigate the workflow tabs; browser Back/Forward restores the selected workspace. Saved projects, cuts and replacement jobs live in the data folder independently of the browser. Unsaved file selections and form drafts do not survive a page reload.

For created videos, changing the source audio requires a new project. Name and musical-marker edits preserve approved clips; changing generation inputs invalidates their approval. Export framing and timed text preserve clip approval and invalidate only the assembled output.

## Managing saved projects

The **Create video** workspace keeps a project library beside the wizard. Search by project/song name, filter by job status, and open, download or delete a project. The selected project is marked. Rename it in **1 Source**; use **Duplicate project for comparison** in **2 Direction** to retain the original while trying another approach. Opening a project link selects Create video automatically.

**Delete project** asks for confirmation with the project name. It permanently removes that project's saved storyboard, uploaded reference copies, preview variants and rendered video files. The source song and other projects remain in the library; files already downloaded elsewhere are unaffected. Open an active project and cancel its job before deleting it. The backend also drains owned image uploads and processing that raced with the request, and refuses deletion if a worker cannot be safely identified or stopped.

Deleting the selected project returns the wizard to **1 Source**. Deleting another project retains the current draft and step. Pending autosaves finish before removal, stale polling cannot restore a removed entry, and failed deletion keeps the project and recoverable edits available for retry.

## Testing locally

Install the updated backend dependencies in your existing backend environment, then restart the API and frontend. From the repository root:

```sh
uv pip install --python backend/.venv/bin/python -r backend/requirements.txt
```

In one terminal:

```sh
cd backend
OPENFABRIC_SETUP_ALLOWED_ORIGINS=http://127.0.0.1:5173,http://localhost:5173 \
  .venv/bin/python -m uvicorn app.main:app --workers 1 --host 127.0.0.1 --port 9000
```

In another:

```sh
cd frontend
npm run dev
```

Open the frontend URL printed by Vite and choose **Video**. For a quick test without generation weights, choose **Animated cover** or **Audio visualizer**, upload an image, analyze the song, and preview a 2–4 second shot. These paths use FFmpeg on the CPU; audio analysis additionally requires NumPy, and timed text requires Pillow. Approve a preview; render fills missing shots and assembles the song. **Export approved clips** requires valid approvals for every shot. Exact 16:9, 9:16 and square framing is applied by cropping at export.

The API accepts only loopback Host headers and rejects browser writes from unapproved origins. If Vite uses another port, set its exact origin in `OPENFABRIC_SETUP_ALLOWED_ORIGINS` before starting the backend. The development launch scripts configure the standard Vite origins.

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

## Character trainer

Silent videos and reels can train one person's look on this Mac. Add at least three photos, and optional clips of eight seconds or less. Consent is required.

Open **Create video → Direction → Optional character references & training** to find these tools. They guide new scene creation; replacing someone in an existing clip uses the separate Replace a character workspace.

Set `OPENFABRIC_VIDEO_CHARACTER_TRAINER` to the video engine Python (`ltx-2-mlx/.venv/bin/python`), or save that path in Direction. OpenFabric then runs `backend/scripts/train_character_adapter.py`, which calls the pinned engine's own `preprocess` and `train` commands. Install `ltx-trainer-mlx` in that environment. The model files must already be on disk (`./setup_video.sh --download-models`). The trainer does not download weights, does not use a cloud, and does not lip-sync.

The adapter is a LoRA file under the OpenFabric data folder. A finished adapter is passed to the local generate command with `--lora` for silent videos and reels only. Song videos are unchanged.

If no trainer command is configured, the job is saved as a dry run. That is not an adapter. The first photo can still pin every shot. A locked still is not training.

`OPENFABRIC_VIDEO_CHARACTER_TRAIN_STEPS` defaults to 800 (100–3000). `OPENFABRIC_VIDEO_CHARACTER_TRAIN_RANK` defaults to 32 (8–64).

## Cut a scene from a movie

Open **Video → Cut a movie**. On macOS, **Open local movie** uses the system file picker and reads the selected AVI, MKV, MP4, MOV or WebM in place. It supports files up to 8 GiB without uploading or copying the whole movie into the library. Keep that file in its original location while previewing or making new cuts. On other platforms, or as a fallback, **Upload movie instead** copies a file up to 2 GiB into the local library.

Choose a time on the movie timeline or enter the time in seconds, then load a preview. Each preview converts only the next fifteen seconds to a browser-playable MP4. Play it and mark **In** and **Out**, or enter exact start/end seconds. The player time is mapped back to the original movie timeline. Exports can be any positive duration up to ten minutes, within a source of at most six hours and 4096 pixels per side.

**Save MP4 scene** saves a local H.264 MP4 at 24 fps, with the first audio track encoded to AAC when the movie has audio. It preserves display proportions, accounting for right-angle rotation and anamorphic pixels, with dimensions rounded down to even pixels when necessary. Exports are capped at 2 GiB. This is an encoded export, so it is not a lossless copy. The original movie is never changed. Saved cuts survive reload and remain downloadable after the original is moved; new previews and cuts require the unchanged source. Cancel stops the local worker and removes its partial output. Interrupted publication recovers a validated completed cut or records a failed export without altering the source. The library retains at most three preview windows per movie; it keeps completed scene exports.

Choose **Delete saved scene** on a saved scene and confirm its title and time range to remove its local MP4 and saved record. The original movie, imported movie copy, previews, other scenes and already-prepared character swaps are retained. A queued or cutting scene must finish or be cancelled before deletion; unresolved worker evidence blocks removal. Interrupted cleanup is hidden from the saved list and retried at startup. Deleting a scene clears it from the pending character-swap selection, and does not remove MP4 copies already downloaded elsewhere.

Choose **Use for character swap** on a completed scene of at least two seconds. This opens Replace a character with the saved scene selected. Add the replacement character image, select a whole-number 2–8 second section within the cut, and prepare it locally. The backend reads the saved cut directly; the browser does not upload it again. The existing 100 MiB character-swap source limit also applies to saved cuts. For a large scene, export a shorter cut before swapping.

Scene import, previews, cutting and downloads are local and need no OpenRouter key. Cloud transfer and paid generation happen only through the separate character-swap reviews below. AVI and MKV are containers: an individual movie still needs a codec the installed FFmpeg can decode. Automated checks use synthetic AVI and MKV files (including MKV with FLAC audio) and mocked native selection; they do not establish compatibility with every old movie.

## Character swap on a provided video

Open **Video → Replace a character**. Upload an AVI, MKV, MP4, MOV or WebM video and character image, select a 2–8 second single-person section, then **Prepare locally**. For a whole movie, use Cut a movie first. Originals remain in the local library. Preparation makes a 24 fps clip with a 480-pixel short side, a silent cloud copy, its first frame, and the full normalized character reference without cropping it.

Enable OpenRouter and configure the existing key in **Settings → Providers**. In the same settings workspace, enable **Temporary clip sharing**. On macOS, install the tunnel utility with `brew install cloudflared` and use **Check local setup**. This check does not open a tunnel or share media. Character swap uses only OpenRouter for AI; no additional AI key or storage account is needed. Local preparation, previews and completed downloads need no cloud credential. Run a single backend worker, as in the local testing command above.

There are two separately reviewed paid steps:

1. **Review frame estimate (free)** retrieves current pricing. Approve the transfer, cost and likeness rights, then **Generate character frame**. Seedream 4.5 receives the original first frame, character reference and replacement prompt. The app saves and previews the edited frame.
2. Review that frame, then **Review video estimate (free)**. Approve this separate transfer and charge, then **Generate character video**. Aleph 2 receives the selected silent clip, edited frame and replacement prompt. It is asked to preserve the movement and scene; results can vary.

Quotes expire after five minutes and bind the model, prompt, settings and input hashes. Renewing a quote uses the retained files and clears approvals. The app checks the existing per-request estimate limit for both steps. Prices verified on 2026-10-11 were $0.04 per frame edit and $0.28 per video second (minimum $0.56). A two-second replacement therefore estimates $0.60 across both steps. Four-second and longer video edits exceed the default $1 per-request limit; review and change that limit in provider settings if desired. Fresh quotes are authoritative; estimates are not billing caps.

Inputs: AVI/MKV/MP4/MOV/WebM up to 100 MiB; PNG/JPEG/WebP up to 15 MiB. Source videos must be at most six hours and no larger than 4096 pixels per side in coded or displayed dimensions. Preparation accounts for right-angle rotation and anamorphic pixels, then fits the full picture onto the nearest supported canvas: 16:9, 4:3, 3:2, square, 2:3, 3:4, 9:16 or 21:9. Source ratios within 3% of a supported canvas receive a small border rather than being rejected or cropped; more distant ratios remain unsupported. Review the prepared clip before generation. Each prepared inline cloud asset is capped at 5,000,000 encoded bytes. A complex/noisy clip can exceed this limit even when the uploaded original fits. Original metadata and image EXIF remain in the local originals and are removed from transferred copies.

The full original video and audio remain local. The selected preview encodes audio once to AAC; final output copies that audio stream and discards provider audio. The backend downloads through OpenRouter's authenticated content endpoint, decodes the complete video, and checks duration, framing and frame rate before publication. Saved jobs, edited-frame review, before/after playback and downloads survive closing the panel and restarting the backend. Earlier fal-era jobs remain available locally; further cloud generation on those records is disabled.

Select a saved job to **Rename** it or **Delete saved job**. Renaming invalidates its old cost review. Deletion requires confirmation, removes that job's local inputs, frames and output, and retains the separate billing receipts. Active local work must be stopped and cleaned up first. Deleting local media neither cancels provider work nor establishes a refund. Interrupted deletion remains hidden and cleanup is retried at startup; unverified worker evidence is retained.

An ambiguous paid submission is shown as **Submission outcome unknown** and is never automatically retried. Check OpenRouter activity before deliberately preparing a fresh job; the previous request may have been billed. **Stop tracking** drains local work without claiming remote cancellation or a refund. **Resume saved step** recovers retained frame work locally or follows the owned video receipt with GET requests only. Unresolved local workers retain ownership and block unsafe work until verified cleanup succeeds. Video processing is not eligible for zero data retention.

New paid receipts retain the received HTTP status. Explicit validation/authentication/rate-limit rejections are failed submissions; timeouts, HTTP 408/5xx and malformed success responses remain uncertain. Historical unknown receipts are unchanged because their response status was not retained. Advisory provider delivery/polling URLs in responses are ignored; all actual video polling and downloads still use fixed authenticated OpenRouter endpoints.

After a definitively rejected video submission with no remote job ID, **Review new video estimate (free)** reuses the saved edited frame and clip. The backend verifies the owned receipt, input fingerprints and current revision before offering a fresh quote. It preserves the old receipt and requires new transfer/cost/rights approval before a separate paid attempt. Unknown submissions and accepted remote jobs cannot use this recovery.

HTTP rejection details are read with a small byte limit and redacted in backend diagnostics; upstream text is never returned through the app API. A missing generation in OpenRouter's Generations tab is not proof that no request reached its HTTP endpoint. Check its Videos tab as well.

**Temporary clip sharing:** OpenRouter requires an HTTPS video reference and its Files API cannot host the clip. After video-step approval, the backend keeps an owned copy of the prepared silent clip on your Mac and starts a Cloudflare Quick Tunnel to a separate clip-only loopback server. That server permits GET/HEAD/range requests for an unguessable clip link; it does not expose the app, settings, original movie, character library or audio. The backend checks the public link's content and headers before submitting the paid video request. Anyone who obtains that link can fetch the approved clip; Cloudflare transports the clip to OpenRouter/Runway. Links are omitted from the app API and logs, use no-cache headers, and are removed after confirmed completion/rejection or a 24-hour sharing period.

Keep your Mac awake, online, and the app running through generation. Cloudflare Quick Tunnels are intended for development and have no uptime guarantee. Closing/restarting the app closes its links; the next tunnel has a different hostname and cannot repair an already-submitted video's old input URL. GET-only recovery can still retrieve an output the provider already completed, but it never purchases a replacement submission automatically. Stopping local progress tracking or deleting a saved job does not revoke a clip link that an accepted or uncertain provider request might still need. Disable sharing to prevent new links; existing shares continue until their cleanup or app exit. Source media and original audio stay local; the generated frame still transfers in the separately reviewed OpenRouter requests.

If sharing fails before submission, the receipt remains an unsubmitted intent. Request a fresh estimate using the retained frame, then approve again. Expired estimates, source changes and mismatched share identities cannot start a paid POST. There is no automatic paid retry.

Automated checks use temporary libraries, mocked OpenRouter/tunnel boundaries and real CPU FFmpeg. They cover separate approvals, single-use quotes, HTTPS transport with local input/receipt provenance, uncertain outcomes, GET-only recovery, interrupted local publication, cancellation, symlink/playlist rejection and audio preservation. **Live provider generation and visual quality require a separately approved paid test; mocked tests do not establish those results.** Fast motion, occlusion and multiple people can produce poor results. Review the edited frame and completed video.

Official references: [OpenRouter API schema](https://openrouter.ai/docs/openapi/openapi.yaml), [Seedream live capabilities/pricing](https://openrouter.ai/api/v1/images/models/bytedance-seed/seedream-4.5/endpoints), [video live catalog](https://openrouter.ai/api/v1/videos/models), [Runway's reviewed-frame workflow](https://help.runwayml.com/hc/en-us/articles/51683104370451-Creating-with-Edit-Studio), [native media inputs](https://docs.dev.runwayml.com/assets/inputs/), [Cloudflare Quick Tunnel limits](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

## Local replacement on Apple silicon

[Wan 2.2 Animate](https://github.com/Wan-Video/Wan2.2#run-wan-animate) has a replacement mode that conditions an existing video on a character image. Its official implementation requires CUDA. [Spielberg](https://github.com/dtellz/spielberg) is a community MLX port with replacement conditioning for Macs; this is an experimental candidate, not an installed or verified OpenFabric engine. Existing LTX character references/training guide new scene generation and do not perform this replacement workflow.

An M4 Max with 128 GiB of unified memory is a plausible trial machine, but capacity alone does not establish speed or visual quality. Before adding an engine, test one two-second, 480p, single-person clip in an isolated environment. Verify that replacement/relighting and image-encoder weights are actually loaded: the community implementation has fallback paths that can omit them. Its default person mask is approximate, and optional SAM2 integration needs compatibility work. No model was installed or downloaded for this assessment, and no local inference was benchmarked.
