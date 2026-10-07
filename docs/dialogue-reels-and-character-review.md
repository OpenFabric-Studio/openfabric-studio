# Dialogue reels and reviewed character adapters

Dialogue reels reuse completed audiobook cast recordings. Video generation is silent; export muxes the copied original recordings. This is an anchored picture workflow, not measured lip synchronization or speech-conditioned video generation. It never substitutes generated music for the cast soundtrack.

## Create a reel

Open a completed chapter’s passages, select the lines and review each selection before creating the reel. Each selection accepts a visual prompt and optional explicit audio bounds in milliseconds. The backend resolves completed, book-owned PCM; the browser cannot supply arbitrary audio paths. Source revisions, current consent, passage identity, PCM hashes and cast provenance are checked and persisted.

Limits remain explicit: one to four selected passages, each 0.2–6 seconds. Each shot occupies a 2, 4 or 6 second slot; trailing silence pads the original audio to that slot. The total, including padding, must fit within fifteen seconds. All selected sources must have the same PCM16 mono/stereo sample format. There is no implicit resampling, hidden cut or automatic summarization. A partial passage requires a caption entered for the selected words; whole passages use their text. Captions mark utterance intervals, not word alignment.

In Video, review the cast soundtrack and cue waveforms, add an anchor image or reviewed compatible character adapter, render selected shots, approve variants, then export. Timing and generic soundtrack replacement are locked for dialogue projects so the cast cues stay aligned. Prompts, references and captions remain editable. The banner states that the model is not synchronizing lips.

After accepting an audiobook repair, use **Use a repaired passage** on the affected cue. Whole-line refresh uses the new complete passage. Trimmed clips require a new explicit caption. Refresh preserves the original shot slot and other shots’ approvals; only the refreshed shot loses approval. Its fingerprint includes the accepted take and copied PCM provenance. A longer repaired line must fit the existing shot or be explicitly trimmed. Stale chapter or project revisions are refused. Consent is rechecked on import, render admission and export publication.

## Recoverable editing and playback

Saved changes retain up to fifty undo and redo snapshots, including variants, approvals, references, soundtrack and selected export. Immutable historical artifacts are retained. Undo/redo rejects active work, stale revisions, cross-project history identities and missing artifacts without replacing the current document. A duplicate starts an independent history and owns only the media actually copied into it.

Current audio players are versioned by immutable clip identity; finished previews, posters and downloads use the selected export’s version. Refresh, undo and redo therefore load the selected media rather than a cached response from the same endpoint. Players share the application playback slot and stop on navigation or teardown. Sampled shot filmstrips contain five frames, and failure to make a filmstrip does not discard a valid video variant.

## Review a character dataset

The built-in trainer requires a reviewed caption for every file, at least three training photos and at least one separate evaluation photo. Evaluation items are saved separately and are excluded from the training input list. Exact duplicate training/evaluation content is rejected. This is an exact-content check, not a near-duplicate detector; review the split yourself.

Captions should describe pose, expression, clothing and setting. Review the bounded step count and LoRA rank. Stored provenance includes normalized artifact hashes, captions, roles, settings identity, engine commit and base-model revision. The built-in script validates the referenced dataset bytes and uses per-item captions and reviewed settings. It does not fetch missing model weights. Its configured Python is probed for the MLX, pipeline and trainer modules without importing model runtimes.

Custom commands remain operator-managed and display an unverified dependency/compatibility status. Older custom training jobs still load; they do not acquire reviewed provenance retroactively. All shipped adapters target the pinned LTX-2.3 base; applying them to LTX-2.5 or a different recorded revision is rejected. Installation readiness is not evidence of likeness quality.

## Compare an adapter

Create the held-out comparison from a completed reviewed job. It produces two saved picture projects with the same evaluation reference, three fixed prompts, seeds, shot durations and anchor strength. One uses the still baseline; the other adds the adapter. Render, approve and review both projects under matching settings, then enter your findings.

The review checks generated modes, fixed prompts/seeds/timing, held-out image bytes, matching settings, actual approved media receipts and content hashes, current engine identity and adapter-bound fingerprints. It records the project revisions, variant IDs and reviewed media hashes. Edits during verification are rejected. Review tasks and their bounded media children are owned, cancellable and drained during shutdown. A recorded review is a human assessment, not an automatic score or proof that quality meets a threshold.

## Caption assets

Exports use pinned, hash-checked OFL Noto Sans and Noto Sans CJK SC Regular assets under `backend/assets/fonts/`. Latin/Greek/Cyrillic text uses Noto Sans; Han, kana and Hangul text selects the pan-CJK face. Its SC Han glyph conventions are deliberate and may differ from regional Japanese/Korean typography. Missing glyphs fail before export with a clear message. No OS font discovery occurs. Desktop packaging copies backend assets, including font licenses. See the asset README for authoritative source commits and SHA-256 values.

## Boundaries still requiring evidence

Real GPU character training, multi-shot identity quality and rendering performance have not been measured by this implementation’s CPU/unit tests. LTX remains on its reviewed 0.15.12 engine pin; see [the compatibility and benchmark report](ltx-compatibility-and-benchmark.md). There is no latent-resume preview, automatic visual ranking, lip-sync engine or automatic ASR repair loop in this workflow.

## Portrait guidance and training transparency — 7 October

Direction offers close-up and near-static portrait prompt presets for local picture projects. They append editable direction without changing approved shots, seeds, geometry or triggering generation. Use a neutral closed-mouth reference and consider a face approximately 150–200 pixels wide in the working output. This is an upstream framing heuristic, not an automatic face measurement or likeness guarantee. Near-static is prompt guidance; it does not install Union Control/canny conditioning, and a cast soundtrack still does not synchronize lips.

New built-in training jobs retain their effective recipe separately from requested rank/steps: 960×544 centre-cropped preparation, 97 frames at 24 fps, AdamW at learning rate 0.0002, batch one, gradient checkpointing and low RAM training. Rank/steps are bounded and not silently reduced based on hardware memory. The UI shows recorded engine/base revisions, settings and whether a held-out comparison was reviewed. Custom and older trainers without an effective recipe stay unknown. File completion, rank and adapter parameter magnitude do not establish likeness; there is no automatic identity score or universal memory threshold.

New still/reference/training-photo imports normalize EXIF orientation and valid ICC profiles into sRGB, including palette images and transparency. Untagged images explicitly assume sRGB. Animated or unsupported formats, invalid profiles, dimensions over 8192 and images over 16,777,216 pixels are refused. Bounded conversion runs in an owned child process. Original upload bytes and hash-bound normalization receipts remain private library artifacts; reference duplication also copies these companions. Existing normalized images are not retroactively presented as originals. Normalized dimensions are recorded after orientation changes.
