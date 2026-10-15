# Local music generation

Music includes experimental HeartMuLa and Stable Audio 3 integrations alongside
ACE-Step and YuE. Both new runtimes require **Apple Silicon macOS**. They run in
separate Python environments, share the studio's owned job and GPU admission
controls, and save completed WAVs into the library. Reloading Music does not
abandon an active job. Supported generation is new audio from a prompt; HeartMuLa
also accepts lyrics. Stable Audio 3's app integration is instrumental only.

These are source integrations verified with mocked model boundaries. No real
inference, quality, speed or memory benchmark is established by those tests.
Installed dependencies and present files are reported separately; neither is
evidence of successful audio generation.

## Music generation queue

Music opens a shared brief and a saved queue. Describe the sound, add lyrics for
vocal models, choose models in execution order, and choose 1–8 variations for
each model. The plan shows duration adjustments, model limitations and the exact
adapted requests before enqueue. Lyrics remain intact; prompts are mapped
deterministically, without an LLM rewrite or silent truncation. Every take gets
a distinct saved seed; identical seeds across engines do not imply similar audio.

Adding a brief does not load a model. **Start queue** runs one take at a time,
saves its output, finishes the selected model's variations and unloads the owned
engine before switching. Compatible ACE-Step and YuE work reuse the native
session. Current HeartMuLa and Stable Audio workers are one-shot processes and
reload for each variation; the plan discloses this limitation.

**Pause after this variation** saves the current take, drains its worker and
releases the model. **Cancel current variation** drains that take and pauses the
rest. Cancel pending removes no history or completed audio. Unstarted briefs can
be reordered; completed tracks appear under Results with existing playback,
favourites, stems and editor tools. A save retry reuses the original child/audio;
an inference retry creates a separate recorded attempt with the same request
and seed. Previous attempts remain in Results history.

Queue state, original briefs, normalized requests, seeds and output associations
live in SQLite. Closing or reloading the browser reconnects to this state; work
continues while the backend remains running. App restart reconciles existing
child publications and leaves pending work paused. Unknown native submissions
are interrupted rather than automatically regenerated. Failed cleanup blocks
advancement: **Retry cleanup** must verify release before Resume is available.
Concurrent controls use revisions, so stale requests return a conflict.

YuE AR/NAR and Stable adapters use the managed adapter registry. The queue uses
the ACE base model and explicitly clears any native session adapter; custom ACE
adapters remain in Advanced generators. HeartMuLa has no supported adapter API.
Direct generators, advanced reference/conversion workflows, cloud music and
training remain available through their existing routes.

The queue allows 1,000 outstanding takes and keeps at most 1,000 attempts per
brief. Its response retains every unfinished brief and up to the latest 1,000
briefs; older completed records remain in the database and audio library. Model
readiness checks installed files and managed runtime evidence. It does not prove
that an unseen model fits memory or produces usable music.

## Source and dependency setup

Open **Settings → Setup & modules** and select HeartMuLa or Stable Audio 3, or run
the matching wrapper from the repository root:

```sh
./setup_heartmula.sh
./setup_stable_audio3.sh
```

Managed setup needs Git and uv. It installs source and dependencies into each
checkout's `.venv` using Python 3.11. It **does not download weights**, including
when the general model-download checkbox is selected. It does not run upstream
bootstrap or web UI scripts. Existing external or modified installations are
preserved. For an external checkout, configure its path in `backend/.env`, restart
the backend, then review its environment through Settings:

```dotenv
OPENFABRIC_HEARTMULA_DIR=/absolute/path/heartlib-mlx
OPENFABRIC_STABLE_AUDIO3_DIR=/absolute/path/stable-audio-3
```

Leave those unset to use the writable managed engine home. The `.bat` wrappers
use the same catalog, which reports these engines unsupported on Windows.

| Engine | Source revision reviewed 14 October 2026 | Runtime |
| --- | --- | --- |
| HeartMuLa | [Acelogic/heartlib-mlx `02dd2e22`](https://github.com/Acelogic/heartlib-mlx/tree/02dd2e22f6a1bf1a2f769a13f8af952a7a4e9cee) | Community MLX port |
| Stable Audio 3 | [Stability-AI/stable-audio-3 `77e0398c`](https://github.com/Stability-AI/stable-audio-3/tree/77e0398c26f7d22d801ec1bb036307e8b231f741/optimized/mlx) | Official MLX runtime |

## HeartMuLa local weights

Choose the model downloads explicitly. The reviewed public artifacts are
[HeartMuLa-oss-3B](https://huggingface.co/HeartMuLa/HeartMuLa-oss-3B/tree/d12ac79c6b3387d5c9eee456323495d8f08bb09d),
[HeartCodec-oss-20260123](https://huggingface.co/HeartMuLa/HeartCodec-oss-20260123/tree/f889dab0532cfa4bf459f2a3367eb6d346b8eeda),
and `tokenizer.json` from
[HeartMuLaGen](https://huggingface.co/HeartMuLa/HeartMuLaGen/tree/2e18e01702011f4f7dc8642b6260967df62417ea).
Their publisher declares Apache-2.0. Download each model's config and all its
safetensors shards into the corresponding `ckpt/` folder below. Keep the original
files and model notices. The older `HeartCodec-oss` link in the community README
was not publicly accessible at review time, and the base 3B repository does not
contain the tokenizer.

Run the inspected converter **once per model** from the HeartMuLa checkout:

```sh
.venv/bin/python -m heartlib_mlx.utils.convert \
  --src ckpt/HeartMuLa-oss-3B --dst ckpt-mlx/heartmula \
  --model-type heartmula --dtype bfloat16
.venv/bin/python -m heartlib_mlx.utils.convert \
  --src ckpt/HeartCodec-oss-20260123 --dst ckpt-mlx/heartcodec \
  --model-type heartcodec --dtype bfloat16
```

The app requires this exact layout under `OPENFABRIC_HEARTMULA_DIR`:

```text
ckpt/tokenizer.json
ckpt-mlx/heartmula/config.json
ckpt-mlx/heartmula/model.safetensors
ckpt-mlx/heartcodec/config.json
ckpt-mlx/heartcodec/model.safetensors
```

The public codec configuration matches fields used by the inspected port, but
converted tensor compatibility and audio parity still need a real preview.
The app offers 5–240 seconds; use a short test first.

## Stable Audio 3 local weights

Download the selected files explicitly from the reviewed
[optimized model repository](https://huggingface.co/stabilityai/stable-audio-3-optimized/tree/08b17423649b5714b22eab20f96bf00751f2358a/MLX).
Place the files directly in
`OPENFABRIC_STABLE_AUDIO3_DIR/optimized/mlx/models/mlx/`:

| Selection | Required files |
| --- | --- |
| Both variants | `t5gemma_f16.npz` |
| Small Music | `dit_sm-music_f16.npz`, `same_s_decoder_f32.npz` |
| Medium | `dit_medium_f16.npz`, `same_l_decoder_f32.npz` |

Text-to-music generation does not require the encoder bundles. Small Music
accepts 5–120 seconds and Medium 5–380 seconds in the app. The official upstream
wrapper can automatically download missing files; OpenFabric instead checks the
local bundle and runs the CLI with Hugging Face offline mode enabled. Missing
models produce a setup error.

Source is MIT; music/codec weights use the
[Stability AI Community License](https://stability.ai/license). The model publisher
identifies the separate T5Gemma encoder under
[Gemma Terms of Use](https://ai.google.dev/gemma/terms). Review the actual artifacts
and terms for your intended use; source licensing does not replace model terms.

## Testing in the studio

Restart after configuring an external path, open Music and choose HeartMuLa or
Stable Audio 3, then refresh its engine status. Enter a style prompt,
optionally add HeartMuLa lyrics, and generate a short preview. Successful audio
appears in the library; failures and cancellation remain visible in job history.
Review audible quality before generating a longer track. Keep downloaded models
and caches outside Git.

## Music LoRA adapters and training

Open **Tools → LoRA training** to reach `/music/lora`. It remains available when
ACE-Step is stopped. Capabilities refer to the selected runtimes:

| Engine | Loading | Training |
| --- | --- | --- |
| ACE-Step | Existing native adapter controls | Existing dataset/training/export workflow |
| Stable Audio 3 | Native SA3 safetensors, matching Small Music or Medium | Native MLX fresh runs on Apple Silicon |
| YuE2-3B | Separate unfused AR and NAR safetensors | No trainer in the selected audio.cpp runtime |
| HeartMuLa | No adapter API in the selected MLX port | No trainer in the selected MLX port |

ACE-Step's installed native handler limits adapter strength to 0–1. The generator
uses that range and shows a notice when an older preset requests more than 1;
the original saved preset remains unchanged. Native operation failures are
reported even when the engine returns HTTP 200 with a failure envelope.

The managed adapter library imports `.safetensors` files up to 512 MiB. Stable
imports require native `lora_config` metadata with `base_model` set to
`sa3-sm-music` or `sa3-medium`, paired tensors and compatible layer dimensions.
PEFT files requiring a companion `adapter_config.json` are not imported by this
integration. YuE accepts the unfused 3B AR/NAR naming and shapes used by
[audio.cpp v0.8.1](https://github.com/0xShug0/audio.cpp/blob/f2b4937306daa25f5c78520f3c626ed31495a37a/src/models/yue2/lora.cpp),
not ComfyUI fused weights. Pick the engine and target during import, then select
the adapter and strength in the corresponding generator. Export returns a copy
of the original checkpoint. Adapters retain their own licensing terms.

Adapter files are copied into private UUID directories under the library's
`music-lora/` folder. Runtime requests use managed IDs; paths are constrained and
checksums verified. Saved tracks record adapter identities, names, checksums and
strengths. YuE adapter loading requires the pinned v0.8.1 runtime. The older custom native
build is rejected for adapters and continues to support base generation. Turning
YuE adapters off clears both modern and legacy resident session adapter options. YuE strength scales its A/B deltas; any NAR full projection replacements
remain at full strength for a nonzero setting, and zero disables the adapter.

### Stable training prerequisites

Training needs the following additional files under the same
`optimized/mlx/models/mlx/` directory. Inference ARC weights cannot substitute
for the rectified-flow base checkpoint:

| Selection | Training files |
| --- | --- |
| Both variants | `t5gemma_f16.npz` |
| Small Music | `dit_sm-music-base_f16.npz`, `same_s_encoder_f32.npz` |
| Medium | `dit_medium-base_f16.npz`, `same_l_encoder_f32.npz` |

Obtain the matching base/encoder NPZs from the optimized model publisher. The
pinned upstream also supplies `scripts/export_base_npz.py` to convert a reviewed
base repository containing `model.safetensors` and `model_config.json`:

```sh
# From OPENFABRIC_STABLE_AUDIO3_DIR/optimized/mlx, using the engine's Python:
../../.venv/bin/python scripts/export_base_npz.py \
  --base-dir /absolute/path/to/stable-audio-3-sm-music-base \
  --output models/mlx/dit_sm-music-base_f16.npz
```

See the pinned [MLX training workflow](https://github.com/Stability-AI/stable-audio-3/blob/77e0398c26f7d22d801ec1bb036307e8b231f741/optimized/mlx/README.md).
OpenFabric checks files first and executes offline; it does not download them
when training starts. Generation with the completed adapter still requires its
usual inference bundle.

Choose or upload tracks through the library picker, give each a caption, choose
the base variant, name the adapter and set steps, rank, learning rate and seed.
The app makes private 44.1 kHz stereo copies, using at most the first 600 seconds
of each track, and runs the real SAME encoder before native training. It trains
plain LoRA with batch size one and gradient checkpointing; the text/seconds
conditioners remain frozen. A completed, validated final-step checkpoint is
registered automatically and can be exported or selected for generation.

Jobs continue after navigation and expose preparation, encoding, training and
publication stages. Cancel stops the owned process tree. Source tracks are never
modified. Training copies, metadata, latents and checkpoints remain under
`stable-lora-training/` for recovery and inspection, so successive runs consume
disk space. Interrupted training does not restart. No resume option is exposed:
upstream adapter reload does not restore the complete optimizer and random state.
Completed publication can recover from an interrupted save without retraining.

These integration and lifecycle checks use synthetic checkpoints and mocked model
boundaries. Adapter inference, training speed, memory use and audible quality
still require tests with real weights on the intended machine.
