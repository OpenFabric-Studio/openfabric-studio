# Mac-first music LoRA training paths

Status: approved by the maintainer on 2026-10-15. Implementation proceeds in the reviewed Stable Audio 3 → HeartMuLa → YuE2 stages.

## Requested outcome

Create and use adapters for Stable Audio 3, YuE2 and HeartMuLa from the model tabs in OpenFabric Studio. Prefer this Mac, with NVIDIA as an optional fallback, as confirmed by the maintainer. Existing approval covers both training and loading where supported. The implementation must complete the path from recordings to an adapter that the corresponding generator can actually use.

The inspected computer is an Apple M4 Max with 128 GiB unified memory. This establishes hardware identity and capacity, not trainer performance or correctness.

## Verified starting point

| Model | Current app | Strongest training path | Remaining proof/work |
| --- | --- | --- | --- |
| Stable Audio 3 | Native MLX training and adapter loading are implemented. Live status reports the trainer installed, but both variants lack training weights. | Existing pinned official MLX trainer. | Automatic training-asset preparation and a real train/export/generate test. |
| HeartMuLa | Current MLX runtime generates songs but has no adapter loading/training integration. | SimpleTuner HeartMuLa PEFT training; upstream documents Apple Silicon MPS support. | Real HeartCodec token preparation, base-model numerical compatibility, MPS training and adapter inference. |
| YuE2 | audio.cpp generation accepts narrowly validated, separate unfused AR/NAR adapters. No trainer is integrated. | Evaluate speedyrulz's headless, compact NAR-only trainer on MPS, with the same trainer on CUDA as fallback. | MPS operator/training proof and mathematically verified conversion into the native loader's layout. |

Stable Small Music needs two additional files, totaling 1,133,744,146 bytes: `dit_sm-music-base_f16.npz` and `same_s_encoder_f32.npz`. T5Gemma is present. Reinstalling the current inference bundle will not supply those files.

## Recommended approach and alternatives

Reuse existing model-specific trainers in isolated environments, then connect them to the app's dataset, job and adapter lifecycle. Complete Stable Audio first, then HeartMuLa, then YuE2. Each integration remains experimental until its own real training and generation checks pass.

Using separate external trainer interfaces is available sooner but leaves users managing datasets, formats and imports themselves. Writing new trainers would add considerably more correctness work and is not justified while existing implementations can be evaluated. The recommended approach preserves the current generation engines when adapter compatibility is proved.

## User workflow

Each existing model tab provides:

1. **Prepare training:** check its isolated environment and reviewed model assets, show download size and licensing conditions, reuse installed files, and resume verified partial downloads. Dependency installation and model preparation have visible stages and logs.
2. **Choose dataset:** select saved recordings or library tracks; review captions, lyrics and instrumental status. Retain originals. Each model creates its own preprocessing cache; tensors from another model cannot be reused.
3. **Train:** name the adapter and use a small set of safe defaults. Advanced parameters stay collapsed. Show actual preparation/encoding/optimizer/checkpoint stages and elapsed activity; never invent progress before the first optimizer step.
4. **Check and use:** validate the final artifact and base-model identity, register it, and offer a short preview using the adapter. Retain a base-model comparison and held-out recordings separately from training.

Mac is the default target when that model's verified checks pass. NVIDIA fallback initially means a portable, reviewed training package and validated result import; no remote machine is configured yet. Remote execution, credentials, paid GPU provisioning and automatic audio uploads are outside this first design.

## Model-specific implementation gates

### Stable Audio 3

Extend the existing reviewed downloader with training bundles, separately selectable from inference assets. Small Music's published assets were inspected at revision `08c64b96b1e59942aade69759f60fb88c58c90c4`:

| Asset | Bytes | SHA256 |
| --- | --- | --- |
| Small Music training base | 918797526 | `4a8ee6dc2f5ef3fbaa595cb337f1c0718c0fbc1916071f929f78beda4335e231` |
| SAME-S encoder | 214946620 | `a48f80d81c30d74c45e2a3047082c4891f715e24c44645adb9c1f4f07afdaf0c` |

Review and pin Medium's corresponding files before offering its automatic preparation. Keep the existing MLX worker, private audio copies and automatic final-checkpoint registration. Its current training restarts from a fresh optimizer; do not call adapter-only continuation full-state resume.

### HeartMuLa

Evaluate SimpleTuner pinned at `a0e7ffadfcd0d3d0f68b400f9697c464da6f9408`, separately from the app's Python 3.11 MLX environment. Its current package requires Python 3.12 or 3.13. Upstream's documented MPS memory requirement is approximately 36 GB; no training has been measured here.

Prepare genuine audio tokens with the official HeartCodec encoder and its matching 20260123 quantizer/decoder. SimpleTuner expects those tokens and does not create them from WAVs during training. Configure exact local codec paths rather than relying on its obsolete decoder fallback ID.

Before training, compare fixed-input base-model outputs against the original HeartMuLa implementation. Inspect Q/K rotary-position layout and weight mappings: SimpleTuner uses Transformers Llama names/conventions, while the current MLX port uses original torchtune names and interleaved RoPE. A renamed checkpoint alone is insufficient evidence of compatibility.

First support a bounded standard PEFT adapter directory, retaining configuration and base/tokenizer/codec identities. Prove trained PyTorch adapter inference matches a separately merged and converted MLX copy before adding a narrow MLX adapter loader. Preserve the installed base weights. If MLX equivalence cannot be established, evaluate a separate PyTorch inference worker; do not silently register an unusable adapter.

### YuE2

Start with acoustic/NAR-only training for sound/production style. AR/planner training is a separate capability, not a prerequisite for every YuE adapter.

Use speedyrulz's trainer at `6a9355efff60589d93b41b569676f09d5db85d09` with a pinned compatible ComfyUI runtime. Its headless CLI does not require a ComfyUI server. Compact conditioning with semantic tokens disabled uses audio, style captions and VAE latents; it does not require MERT, a community tokenizer, SheetSage2 or automatic transcription. Its device resolver accepts MPS, CUDA calls are guarded, and differentiable attention already expands KV heads before ordinary SDPA. This is source evidence for a Mac feasibility test, not a verified Mac training result; the project's documented hardware is NVIDIA BF16.

Keep acoustic-head training disabled in the initial supported configuration. Verify MPS BF16 model/VAE operations, prefix caching, backward, checkpointing and AdamW, then a short real training run and generation with its exported adapter. The same trainer on CUDA supplies the NVIDIA fallback, reducing backend variation.

The app's native loader and this trainer use different key/layout conventions. Implement a strict NAR converter only for supported standard LoRA projections. Remove `diffusion_model.model.`, add `nar_` before attention/MLP names, and translate down/up factors to A/B. Split fused B output rows into Q/K/V widths 2048/1024/1024 and gate/up widths 6144/6144, retaining the corresponding A; account for alpha/rank scaling. Validate stage, layer dimensions and exact base identity. Compare converted matrix deltas numerically before testing native playback. Reject unsupported keys rather than silently dropping them. Acoustic-head training stays off so initial exports require no full projection replacements.

Real-recording AR training can require MERT and community semantic-tokenizer weights. Some are published under CC BY-NC 4.0. They are excluded from the initial NAR-only workflow. Preserve asset provenance and show restrictions before any future AR workflow; the base model's separate terms do not automatically clear auxiliary assets or adapter redistribution. Text-only NAR training differs from inference's semantic conditioning, so audible transfer remains a measured quality question.

## Ownership, recovery and data protection

Reuse current backend job ownership and accelerator admission. Run one heavy training/generation job at a time. Persist immutable input/settings snapshots and completed artifacts independently of the browser. Reconnect after navigation or a network drop. Cancel drains owned subprocess descendants and preserves completed checkpoints. Training runs and exports use new named directories and refuse existing destinations.

Expose true checkpoint resume only after verifying restoration of optimizer, scheduler, random state, sampler position and compatible data/settings. Otherwise label weight-only continuation as a new run. Runtime/GPU capability labels distinguish unsupported, experimental, prepared and actually tested states.

## Verification and completion criteria

Use isolated temporary configuration, library, Seed-VC and module roots for unit tests. Cover download interruption/hash mismatch, existing-file preservation, invalid datasets/adapters, cancellation, reload, concurrent work and interrupted publication. Run frontend tests/build, backend regressions, generated contracts and all CI platform typing checks; desktop checks apply to startup/layout changes.

For each model, require a real short training run with finite loss and changed adapter tensors, artifact validation, loading into the target generator and a saved preview. Record actual hardware, model versions, durations and memory measurements. A small training test demonstrates that the pipeline works; musical improvement requires listening comparisons across held-out examples.

No commit, push, vendor modification, paid compute or long training run is part of this design review.

## Primary references

- [Official Stable Audio 3 MLX training](https://github.com/Stability-AI/stable-audio-3/blob/77e0398c26f7d22d801ec1bb036307e8b231f741/optimized/mlx/README.md)
- [Published Stable optimized assets](https://huggingface.co/stabilityai/stable-audio-3-optimized/tree/08c64b96b1e59942aade69759f60fb88c58c90c4/MLX)
- [SimpleTuner HeartMuLa quickstart](https://github.com/bghira/SimpleTuner/blob/a0e7ffadfcd0d3d0f68b400f9697c464da6f9408/documentation/quickstart/HEARTMULA.md)
- [Official HeartCodec encoder](https://huggingface.co/HeartMuLa/HeartCodec-oss-encoder)
- [Headless YuE2 trainer](https://github.com/speedyrulz/ComfyUI-YuE2-Trainer/blob/6a9355efff60589d93b41b569676f09d5db85d09/train_cli.py)
- [YuE2 training attention](https://github.com/speedyrulz/ComfyUI-YuE2-Trainer/blob/6a9355efff60589d93b41b569676f09d5db85d09/yue2_trainer/forward.py)
- [Pinned native YuE2 adapter loader](https://github.com/0xShug0/audio.cpp/blob/f2b4937306daa25f5c78520f3c626ed31495a37a/src/models/yue2/lora.cpp)
