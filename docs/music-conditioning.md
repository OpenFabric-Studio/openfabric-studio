# Retained audio references for Stable Audio 3

This maintained-fork integration offers instrumental audio-to-audio variation and region inpainting through the existing local-music job owner. It does not certify native generation quality, musical similarity, timing quality or rights to the selected recording. ACE Complete/Lego/Cover and YuE audio conditioning remain disabled in this workflow.

## Native source evidence

The reviewed source is Stability AI's [`sa3_mlx.py` at commit `77e0398c26f7d22d801ec1bb036307e8b231f741`](https://github.com/Stability-AI/stable-audio-3/blob/77e0398c26f7d22d801ec1bb036307e8b231f741/optimized/mlx/scripts/sa3_mlx.py). Its inspected file SHA-256 is `312c2833fac5ca270cbeebe7037b3fbeacd87b10c9b5d057587c5eaf824fb951`.

- Lines 639–679 encode `--init-audio` using the selected SAME encoder.
- Lines 702–705 initialize audio-to-audio sampling with the encoded reference and noise. The app accepts `--init-noise-level` from 0.01 through 0.99, reserving at least 1% input weight. Values very close to one can round to pure noise with reduced precision.
- Native `--inpaint-range START,END` rounds the mask to latent cells of 4096 samples at 44.1 kHz. The app rejects a region that collapses on that grid. Native latent preservation alone does not establish identical waveform samples.
- Lines 839–842 trim decoded output to the requested frame count. The app independently requires that exact count, 44.1 kHz stereo and PCM16 before accepting native conditioned audio.

The matching encoder identities already exist in the immutable [music model manifest](../backend/app/assets/music-models.json):

| Variant | Encoder | Bytes | SHA-256 |
| --- | --- | ---: | --- |
| Small Music | `same_s_encoder_f32.npz` | 214946620 | `a48f80d81c30d74c45e2a3047082c4891f715e24c44645adb9c1f4f07afdaf0c` |
| Medium | `same_l_encoder_f32.npz` | 1704313504 | `c5caafc6bd29fc3d4cb7a08b0d3725041fa2100711f86a4f05d2638c532871d2` |

`stable_conditioning_variant` selects the corresponding generation bundle and encoder through the existing reviewed module installer. It excludes the separate training base, forecasts only structurally missing artifacts, and preserves unknown existing files. Native admission verifies the script and all selected generation/encoder byte hashes; structural setup status is not native-runtime proof. Publisher Community License and Gemma terms remain explicit setup/generation requirements.

## Source and publication guarantees

Public requests accept a retained version, stem, production asset or completed full beat render with exact IDs and hashes. They accept no input path or arbitrary URL. Read-only `/api/music-conditioning/source` inspection can report up to 7200 seconds within a 512 MiB file cap; generation preparation and preview independently enforce the 380-second native source limit and the selected model's existing duration cap.

The local-music job registers an original-source reader before it starts. Track removal or stem replacement drains these readers before changing the source. Preparation copies the original into the job workspace, hashes both copies, then releases that original-reader hold. Later source deletion cannot change the retained job input. Private evidence, source identity, input gain, encoder/script hashes and frame mask remain in the durable job recipe. No new process registry or GPU scheduler is introduced.

Model input is a private 44.1 kHz stereo PCM16 copy. It is padded or trimmed to the requested generation duration. Preparation first resamples to float, measures the actual resampled peak, then records any attenuation needed before PCM16 quantization. The original bytes are preserved. This input conversion is not a promise of an isolated added part or harmonic control.

Inpainting initially accepts only PCM16, PCM24 or FLOAT WAV at 44.1/48 kHz, with one/two channels and a whole-second duration exactly equal to the request. The generated region is resampled to the original rate/channels and spliced into a FLOAT WAV. Frames outside the selected interval retain the original decoded float32 PCM values exactly, including finite sample overshoot; fades lie entirely inside the interval. PCM32 and DOUBLE sources are refused because their samples are not always exactly representable in float32. No implicit duration padding is allowed for this splice.

Native execution checks retained input/script identity before and after generation, rejects existing output files, and publishes through an exclusive atomic link. The supervisor validates final audio and stores its hash before normal library publication. Recovery never repeats inference and refuses an unattested conditional output; completed or uncertain audio is retained after failure. Failed worker verification preserves its inputs and blocks unsafe reuse.

## Review and remaining evidence

The UI separates text-only generation, audio-to-audio and inpaint. A library picker inspects an explicit completed saved version; a beat handoff inspects its exact retained full render. Neither action starts inference. A fresh storage/runtime preview plus an explicit checkbox is required for the exact current request. Changing the source, prompt, title, duration, seed, adapter, variant or conditioning options invalidates that review. Late responses after teardown or a settings change cannot reapprove it.

Verification uses temporary libraries, real FFmpeg/SoundFile CPU fixtures, owned fake model subprocesses, restart/cancellation cases and exact PCM comparisons. No native MLX model was downloaded or executed for these tests. Listening quality, model memory usage and native performance still require explicit platform validation. The UI and capability API continue to identify runtime quality as unverified.


## ACE-Step Base Complete and Lego

The music queue now accepts typed `ace_complete` and `ace_lego` source selections. Complete adds selected musical roles; Lego selects one role. Both use exactly `acestep-v15-base` and return a newly generated full mix. Source audio must be 10–300 seconds and no larger than 512 MiB. Vocal roles require a vocal brief and non-instrumental lyrics. There is no automatic repetition, fallback to text generation, target-length guarantee, isolated part or original-PCM promise.

The selected Base setup bundle includes its decoder and text encoder without selecting Turbo, SFT, a language model or changing ordinary generation defaults. The pinned initializer has main-bundle download behavior that is unsuitable for this minimal task by itself. The app therefore verifies the reviewed initializer/download source files and its own standard-library bootstrap, then starts the API with a private process specification. The bootstrap admits only the three selected local components and refuses both native HF and ModelScope download paths, including when files disappear after admission. It does not modify the installed checkout, global model definitions or ordinary launches. A test executes the actual pinned initialization class without torch/models and proves zero download calls with the local bundle present or absent.

Queue acceptance retains a private raw source, its hash and usage evidence before publishing the batch. All variations share that immutable copy; dispatch never silently re-resolves a missing original. The queue budgets all selected source copies together and checks each model's working-space requirement. Multipart Complete roles are forwarded as repeated fields to match the pinned parser. Exact source/model hashes are checked before native initialization, and an already loaded different model is refused.

Native source loading can resample/pad audio and clamp floating-point samples above full scale. Lower the gain of over-unit sources before selecting them; the retained original itself is unchanged. These source and lifecycle checks do not establish GPU execution, harmonic fidelity or listening quality. The current capability contract explicitly keeps runtime verification false.
