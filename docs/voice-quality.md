# Voice preparation and listening comparisons

Voice Clone uses the existing 44.1 kHz, pitch-conditioned Seed-VC singing engine. Preparation measurements screen candidates; they do not identify a singer or prove perceptual quality.

Files → Samples → Coverage → Build → Compare shows the work as numbered steps with objectives, prerequisites and completion states. Changing source options or the reviewed sample selection invalidates downstream completion. Coverage is optional measurement; Compare is subjective evaluation, so merely having an old result does not complete it.

One status summary stays below the steps and remains visible while scrolling. Preparation, coverage and build jobs persist queue/start/phase/finish times and completed work counts. Time spent includes queueing and freezes at completion or cancellation. Current-phase estimates need at least two comparable measured progress deltas and exclude training warmup; unfamiliar phases and total-job time remain “estimating.” Legacy jobs without recorded timestamps show unknown timing. Cancelling while a successful job drains cleanup preserves its completed result.

Install the voice engine with `bash setup_voice.sh`. Song inputs require Demucs or the RoFormer installation described below; an already isolated vocal bypasses separation. FFmpeg must be available through the app's configured binary directory or system PATH.

## Workflow

The workspace has **Files → Samples → Coverage → Build → Compare** tabs and guided Next actions. Revisit any tab without losing unsaved review choices. Save changed selections before building or analyzing coverage. Playback remains available while work is active; one expanded sample presents original/cleaned auditions. Search, source/status filters, sorting and pagination keep long lists manageable. Bulk sample actions apply to the visible page and accepted, trainable passages; rejected passages remain auditionable.

The duration budget controls automatic selection and the saved training selection. Processed passages remain available for review beyond that budget. Change the budget in Files, revisit Samples and save to include more existing passages without separating the sources again. Changing separation, cleanup, enabled sources or singer confirmation requires fresh preparation.

1. Upload songs containing the intended singer. Exclude unwanted sources and mark an already isolated vocal as **Vocal** so it bypasses separation. Confirm that the selected material contains the intended singer; duets and layered backing vocals require manual review.
2. Prepare samples. **Fast** uses `htdemucs`; **High** uses `htdemucs_ft` and is substantially slower. RoFormer becomes available when its engine, configuration, and checkpoint are installed; the adapter validates compatibility before running it.
3. Review the accepted/rejected passages with their original file and timestamps. Periodicity, dBFS level, peak, and near-full-scale sample fraction are numerical measurements. A previously clipped recording attenuated below full scale can evade the last indicator. Quiet/breathy singing and periodic instruments can confuse screening; audition the actual audio.
4. If cleanup was requested, compare original and cleaned versions and select cleaned audio only for passages that improve. The conservative filter is spectral attenuation, not a verified neural dereverberation model. Original samples remain available.
5. Audition reference candidates and save the sample/reference selection. A reference is a contiguous passage of up to ten seconds. Source changes invalidate the prepared revision.
   Coverage analysis runs on the initial selected samples. For existing preparations or newly selected variants, use **Coverage → Analyze selected samples**. This reuses prepared audio and cached measurements without repeating separation. Cancel stops the owned analysis process.
6. Choose **Reference only**, a fresh 200/500/1000-step build, or a 1000-step run retaining the 200/500/1000 checkpoints. Fresh builds use isolated directories. Publication happens after artifacts complete; failed builds preserve the prior active voice.
7. Resume is explicit and requires a matching selection, preparation revision, model configuration, and a full-state checkpoint with a higher target step count. It restores weights, optimizer, scheduler, and counters; it does not promise identical batch order or GPU bitwise reproducibility. Resume and checkpoint comparison are separate modes.

Reference-only conversion uses an immutable copy of the exact baseline checkpoint configured for training, with its matching configuration. The first requested build downloads that baseline into the library cache if it is absent; the download runs separately from GPU training and is cancellable. Comparisons therefore measure fine-tuning against its actual starting model. A content SHA256 identifies the baseline; changed weights under the same filename invalidate resume.

Each build retains its reviewed preparation manifest (original sources/timestamps, selections, and source stamps), one baseline copy, its reference/preview, and any trained checkpoints, including full optimizer state. Existing builds remain available, so allow disk space for multiple model copies when comparing runs. Source-change detection uses file size and modification time; it is not a content-integrity proof against manually edited files that preserve both values.

## Held-out trials

Upload a different recording in the comparison panel, separate from training recordings. Choose a short passage, model(s), reference(s), and 30/50 diffusion steps. A request has at most twelve combinations; all use the same seed and source passage. GPU kernels may still be nondeterministic.

Automatic voice application to saved tracks uses Fast Demucs. Use Fast in a listening trial when evaluating that application path; High/RoFormer trials let you investigate the effect of a different source separator.

Compare low and high notes, sustained vowels, consonants, breathy passages, and transitions. Rate **identity**, **pitch**, **intelligibility**, and **artifacts** independently (higher is better). Duration, dBFS level, peak, and near-full-scale fraction are measured, not inferred identity scores. Training loss alone does not select the best audible checkpoint.

Trial sources never enter the training recordings automatically. Use songs that were never used to build the voice; uploading a copy of training audio here does not make it held out. Results/ratings persist across reloads. Cancellation stops active model work, retaining already completed trial outputs.

## Optional RoFormer separation

Run `bash setup_roformer.sh`. It installs a pinned [MSST engine](https://github.com/ZFTurbo/Music-Source-Separation-Training), inference-only dependencies and the author-hosted [Kimberley MelBandRoformer vocal checkpoint](https://huggingface.co/KimberleyJSN/melbandroformer), with its matching upstream configuration. The approximately 0.85 GiB download is checked against its fixed size and SHA256. Setup writes only these four server settings to `backend/.env`, preserving unrelated settings, and records provenance beside the weights. Restart the backend after setup:

```dotenv
VOICE_ROFORMER_DIR=/absolute/path/to/Music-Source-Separation-Training
VOICE_ROFORMER_MODEL_TYPE=mel_band_roformer
VOICE_ROFORMER_CONFIG=/absolute/path/to/vocal-config.yaml
VOICE_ROFORMER_CHECKPOINT=/absolute/path/to/vocal-model.ckpt
```

`bs_roformer` is also supported when manually configured with matching artifacts. Default engine commit: `84b1eac0887756b4f1a9d7a1ff49105939749ed2`; author model revision: `ac9b0614ab3cd7f77219e18ba494dfd93956c348`; checkpoint SHA256: `87201f4d31afb5bc79993230fc49446918425574db48c01c405e44f365c7559e`. The supplied inference config changes only batch size to one and AMP to false. Setup preserves a different or modified engine checkout by failing rather than resetting it.

The adapter verifies the inspected CLI signatures, uses floating-point outputs, and rejects missing/ambiguous vocal outputs. An actual two-second synthetic stereo smoke through the adapter succeeded on Apple M4 Max/MPS, producing a finite 44.1 kHz stereo WAV without changing the source. This establishes execution, not better separation on songs. Compare RoFormer and Demucs on the same representative sources; document the device, runtime and audible tradeoffs. Other devices and manual checkpoints need their own runtime verification.

## How much voice data?

Fifteen minutes was an app policy, not a Seed-VC requirement. [Seed-VC's training guidance](https://github.com/Plachtaa/seed-vc#training) requires clean 1–30 second clips and says step count depends on dataset size; it does not prescribe fifteen minutes or a universal quality threshold. This app creates passages up to ten seconds.

Practical starting recommendation: compare reference-only conversion with a build from **15–30 clean, varied minutes**. Try **30–60 minutes** when those recordings add useful notes, vowels, dynamics and delivery styles from the same singer. More duplicates, backing singers, separation artifacts, noise or reverb can make the larger set less useful. These ranges are workflow advice, not a validated guarantee of this singer's quality.

The trainer uses batch size one and shuffles clips. Target steps divided by selected clip count approximates dataset passes for a fresh run, not an audible-quality score. For example, 1000 steps across 360 ten-second clips is about 2.8 passes. Increasing duration without changing steps reduces exposure per clip. Compare held-out output at saved checkpoints; training loss and elapsed minutes cannot choose the best-sounding voice.

## What coverage measures

Coverage caches each real passage and original/cleaned variant separately. The saved selected variants determine the aggregate. Missing measurements stay explicit; measured time excludes unavailable clips. The selected total remains visible separately.

Pitch analysis uses [librosa pYIN](https://librosa.org/doc/0.11.0/generated/librosa.pyin.html), filtered 16 kHz input, 50–2000 Hz bounds, 2048-sample windows, 320-sample hops and voicing probability at least 0.8 with a -60 dBFS RMS floor. Independent 20 ms hop spans count toward analyzed/voiced time; overlapping windows and unanalyzed edges do not inflate it. Voicing probability is not calibrated pitch accuracy. Aggregate central-90% pitch bounds use weighted semitone-bin centers. Octave errors, breathy singing, instruments and damaged recordings can mislead the estimator.

The pitch chart shows **observed note occupancy**. Empty bins between sampled notes mean those notes were not measured in this selection. They do not prove that the singer cannot sing them or that the full range is missing. Phonemes, vowels, register technique and identity are not measured automatically; audition representative low/high notes, sustained vowels, consonants, breathy passages and transitions yourself.

Spectrum clues are duration-weighted means of per-passage 95% Welch power rolloff and energy at/above 4 kHz, measured before pitch resampling. Vowels, timbre, noise and source separation all affect them. They cannot establish original recording bandwidth, prove quality or reconstruct lost information. Prepared audio is standardized to 44.1 kHz; its sample rate is not evidence that an original low-bandwidth recording contained high-frequency detail.

## Limits and compatibility

Cleaning cannot establish voice detail missing from bandwidth-limited or heavily distorted recordings. Seek the best surviving passages, and compare against reference-only conversion before spending more training time. Preparation accepts sources up to one hour/512 MiB each and selects up to the chosen 1–60 minute budget and 1000 passages. The default is fifteen minutes. Trial uploads are capped at 256 MiB/two hours, with a 2–30 second comparison passage.

The tracked Seed-VC compatibility patcher runs during setup and before model work. It fixes high-pitch bin overflow, makes continuation explicit, preserves full-state milestones, and adds inference seeding. Unknown upstream source signatures fail with `engine_incompatible` instead of silently applying a guessed patch. Existing published voices remain selectable.

Unit/integration tests use synthetic audio and mocked model boundaries. GPU training quality, real separation quality, and platform-specific model compatibility require listening trials on representative recordings.
