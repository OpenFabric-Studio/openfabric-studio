# Local music generation

Music includes experimental HeartMuLa and Stable Audio 3 integrations alongside
ACE-Step and YuE. Both new runtimes require **Apple Silicon macOS**. They run in
separate Python environments, share the studio's owned job and GPU admission
controls, and save completed WAVs into the library. Reloading Music does not
abandon an active job. Supported generation is new audio from a prompt; HeartMuLa
also accepts lyrics. Stable Audio 3's app integration is instrumental only.

These integrations remain experimental. Short synthetic train/load/preview runs
have passed on this M4 Max Mac for Stable Small Music, HeartMuLa and YuE2
acoustic adapters. This establishes the tested pipeline, not musical quality or
performance on another machine. See [training evidence](music-lora-training-proof.md).
Installed dependencies and present files are reported separately from execution.

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
adapters remain in Advanced generators. HeartMuLa adapters are available in its
advanced generator through the separate corrected PyTorch runtime.
Direct generators, advanced reference/conversion workflows, cloud music and
training remain available through their existing routes.

The queue allows 1,000 outstanding takes and keeps at most 1,000 attempts per
brief. Its response retains every unfinished brief and up to the latest 1,000
briefs; older completed records remain in the database and audio library. Model
readiness checks installed files and managed runtime evidence. It does not prove
that an unseen model fits memory or produces usable music.

## Source and dependency setup

**Settings → Setup & modules → Automatic setup** performs music installation,
required downloads and supported conversions as one saved job. ACE-Step reuses
the configured model selection; automatic downloads currently cover Turbo or XL
SFT with the 1.7B or 4B language model. Other custom selections are preserved and
reported as requiring a reviewed model manifest. YuE setup reuses existing native
files or downloads missing pinned packages. Existing model files are never
replaced to make a setup pass. A completed setup means its components passed the
reported checks; it does not claim an inference benchmark or audible quality.

Installed features show their status and cannot be selected for ordinary setup.
Missing required music files keep the feature marked Setup incomplete so setup
can finish them while reusing a verified environment. Existing dependencies are
reused. For supported app-managed Python engines, **Reinstall dependencies** is a
separate explicit choice reviewed before starting: it replaces isolated packages
and preserves source, models, datasets, checkpoints and configuration. External
checkouts and system/native tools cannot be reinstalled through this action.
An interrupted reinstall leaves environment verification incomplete; retry keeps
the unfinished reinstall intent and reuses engines that already completed.

Setup activity keeps the active or most recently updated run visible, with older
attempts under Previous setups. A failed step shows its recovery action;
Technical details contains the error code and saved phase history. The installer
output time reflects actual process output, not an estimated percentage. Older
jobs may have no saved phase history. Retry setup rechecks the reviewed scope and
reuses verified downloads and completed HeartMuLa conversion components. A
changed download plan requires a new review; an unverified worker blocks retry
until its process is confirmed stopped.

Open **Settings → Setup & modules** and select HeartMuLa or Stable Audio 3, or run
the matching wrapper from the repository root:

```sh
./setup_heartmula.sh --download-models
./setup_stable_audio3.sh --download-models
```

Managed setup needs Git and uv. It installs source and dependencies into each
checkout's `.venv` using Python 3.11. Automatic setup includes required model files
by default; review download sizes, disk allowance and licence declarations before
starting. Customize allows a dependency-only install. CLI wrappers require
`--download-models` explicitly. Setup downloads pinned public artifacts with
SHA256 and size checks, resumes interrupted transfers, converts HeartMuLa models
for MLX, and installs Stable Audio's Small Music bundle. A complete existing Small
Music or Medium bundle is reused. Conflicting files stop setup instead of being
overwritten.

For an external checkout, configure its path in `backend/.env`, restart the
backend, then run Automatic setup to check its environment and complete missing
model files without replacing its source:

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

Automatic setup downloads and converts these files when required models are
included. The layout and conversion commands below also support operator-managed
installations. The reviewed public artifacts are
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

Automatic setup installs the Small Music files from the reviewed
[optimized model repository](https://huggingface.co/stabilityai/stable-audio-3-optimized/tree/08b17423649b5714b22eab20f96bf00751f2358a/MLX).
Place the files directly in
`OPENFABRIC_STABLE_AUDIO3_DIR/optimized/mlx/models/mlx/`:

| Selection | Required files |
| --- | --- |
| Both variants | `t5gemma_f16.npz` |
| Small Music | `dit_sm-music_f16.npz`, `same_s_decoder_f32.npz` |
| Medium | `dit_medium_f16.npz`, `same_l_decoder_f32.npz` |

Text-to-music generation does not require the encoder bundles. Small Music
accepts 5–120 seconds and Medium 5–380 seconds in the app. Generation runs with Hugging
Face offline mode enabled; downloads happen in the saved setup job. Medium remains
an explicit operator choice; setup reuses a complete local Medium bundle without
also downloading Small Music. Missing models produce a setup error.

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

Open **Tools → LoRA training** (`/music/lora`), then choose a model tab. Each
adapter belongs to one model and base; it cannot be exchanged between engines.

| Engine | Loading | Training |
| --- | --- | --- |
| ACE-Step | Existing native adapter controls | Existing dataset/training/export workflow |
| Stable Audio 3 | Native safetensors for matching Small Music or Medium | Official MLX fresh runs on Apple Silicon |
| YuE2-3B | Separate native AR and NAR safetensors | Experimental compact acoustic/NAR training on Mac; AR composition stays frozen |
| HeartMuLa-3B | Reviewed native OpenFabric safetensors through the corrected PyTorch runtime | Experimental Mac training with official HeartCodec tokens |

### Prepare and train on this Mac

Use **Prepare training** in the selected tab, review its downloads and model
terms, then start the saved setup job. It installs pinned, isolated training
sources and dependencies and downloads missing model files. Verified files are
reused; generation environments, recordings and existing checkpoints are
preserved. Training starts offline only after preparation succeeds.

Stable Small Music needs about 1.13 GB of additional base/encoder weights when
its generation conditioner is already present. Medium requires its own matching
base and encoder. HeartMuLa preparation reuses the original 3B model, decoder and
tokenizer where available, and adds the official encoder. YuE2 preparation uses
the reviewed 7.80 GB BF16 training checkpoint; a quantized generation GGUF cannot
replace it. The review reports actual missing bytes separately from unknown
Python dependency download sizes. Model terms remain separate from source terms.

Name the adapter, add recordings from the library or a saved labeled dataset,
and review each description. Saved dataset references bind its revision; changing
or deleting it before the worker takes a snapshot stops the job safely. Existing
ACE tensors are not reused by another model: each prepares its own audio/tokens.
Original recordings remain unchanged. Advanced settings are collapsed.

Stable uses private 44.1 kHz stereo copies of at most the first 600 seconds,
plain LoRA, batch size one and gradient checkpointing. Its text/seconds
conditioners remain frozen. HeartMuLa and YuE2 use the first selected 6–30 seconds
(default 12). HeartMuLa supports an instrumental checkbox and lyrics that match
the selected clip. A vocal recording longer than the selected excerpt with
nonempty lyrics must be trimmed first; silently pairing full-song lyrics with a
short clip would train the wrong thing. YuE2 trains acoustic style, without lyric
or AR training, semantic auxiliaries or acoustic-head replacement.

Progress reports actual preparation/encoding/loading/training/publication stages,
completed steps, loss, elapsed time and measured time per step for all three
trainers. Training ETA appears after enough step measurements; it excludes
loading, encoding and saving and is withheld while disconnected or measurements
are stale. Finished runs retain their recorded duration and final measurements.
**Run details** shows the submitted settings; the info icon beside **Train**
explains every option. An already running older backend keeps its job running:
elapsed time is available immediately, and detailed reporting becomes available
after restarting the app once that run has finished.

Reloading or a network outage reattaches to the backend job. Cancellation drains
its process tree. A completed,
validated checkpoint is registered automatically; **Preview with this adapter**
opens the matching generator. **Compare base model** opens it with adapters
off; HeartMuLa keeps its adapter selected at strength zero so the comparison uses
the same corrected PyTorch base runtime. Generation still requires an explicit action. Compare the same prompt and
seed, and keep evaluation recordings outside the training set.

Training snapshots/checkpoints remain under `stable-lora-training/` or
`music-lora-training/` in the library. New runs consume disk space. Completed
publication can recover after an interrupted save without retraining. Engine or
app crashes do **not** resume optimizer/random state: starting again creates a
separate run. Do not mistake reconnecting to a live job for full-state resume.

### Optional NVIDIA fallback

HeartMuLa and YuE2 tabs offer **Use an NVIDIA machine instead**. After reviewing
the same recordings/settings, create and download a portable ZIP. It contains
original audio, descriptions, settings, pinned worker code/dependencies, model
identities and notices. It does not include weights, upload files, connect to a
remote machine or spend money. Cancelled preparation removes private staging and
partial ZIPs; completed archives remain downloadable after reload.

On the NVIDIA machine, read the included README and run `python bootstrap.py
--check` before `python bootstrap.py`. The latter prepares pinned dependencies,
downloads hash-verified model files and starts a fresh CUDA run. Import the returned
native `adapter.safetensors` through the matching model tab. **CUDA execution has
not been verified**; the package is an optional experimental fallback, not a
claim that every NVIDIA card fits these models.

### Adapter library and compatibility

The managed library imports `.safetensors` up to 512 MiB into new private UUID
folders. It validates tensor pairs, dimensions, finite values, metadata, target
and base identity. Generation uses managed IDs and verifies checksums. Export
returns a copy, and saved audio records the adapter identity and strength.

Stable requires native `lora_config` metadata with `base_model` set to
`sa3-sm-music` or `sa3-medium`. YuE accepts unfused 3B AR/NAR names used by
[audio.cpp v0.8.1](https://github.com/0xShug0/audio.cpp/blob/f2b4937306daa25f5c78520f3c626ed31495a37a/src/models/yue2/lora.cpp);
the integrated trainer converts its fused NAR factors with numerical delta checks.
HeartMuLa imports the reviewed complete 31-layer projection layout with embedded
base/tokenizer identities; generic PEFT plus a companion JSON is unsupported.
See [HeartMuLa numerical evidence](heartmula-training-proof.md) for why adapters
use a separate corrected runtime rather than the existing MLX port.

ACE-Step strength is 0–1, matching the native handler. Other managed strengths
are 0–2. Turning YuE adapters off clears modern and legacy resident options. NAR
full projection replacements in third-party imports remain full-strength when
nonzero; the integrated trainer produces plain LoRA factors only. Adapters and
recordings retain their own terms. Short synthetic tests establish execution;
use held-out listening comparisons before trusting a trained style.
