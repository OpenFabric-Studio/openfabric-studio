# LTX compatibility investigation and benchmark evidence

Date: 5 October 2026. The engine pin stays at **0.15.12**, commit `1724ca673d59f023a8a95efee06e5d36d61c2765`. Upgrading to 0.16.0 without verifying the maintained audio-to-video wrapper would break the evidence boundary, not improve it.

## Verified upstream source

The official 0.16.0 tag resolves to `90f76c20864ea612071afbb4e714ceea99e38e34`. Read-only inspection of pinned upstream source found that the A2V CLI still does not forward `tile_count` or the custom LoRA arguments used by the maintained wrapper. The two-stage A2V pipeline still creates an untiled stage-one `x0_model`, while stage-one implementation was refactored into a helper. The maintained 0.15.12 source patch therefore cannot be assumed to match 0.16.0. The inspected CLI exposes no stage-one latent resume contract.

| Official source | Inspected content SHA-256 |
| --- | --- |
| [0.16.0 CLI](https://github.com/dgrauet/ltx-2-mlx/blob/90f76c20864ea612071afbb4e714ceea99e38e34/packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/cli.py) | `2f9cec8a7ae3e4f0225f489ec878e9b5d09a2b2bb2078d443b44494eea20a5f3` |
| [0.16.0 A2V two-stage pipeline](https://github.com/dgrauet/ltx-2-mlx/blob/90f76c20864ea612071afbb4e714ceea99e38e34/packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/a2vid_two_stage.py) | `bfb494ada7158c65290a3f4bbc81f3bd0e0d6b35c46f32c920b656ca0edd69c8` |

These are source observations, not successful inference or compatibility measurements. Before changing the pin, port the wrapper against exact upstream files, test its argument and source-hash guards, and run the hardware matrix below. Keep the old executable/model snapshots available for rollback.

## Actual local evidence — 5 October

The read-only inventory measured a `Mac16,5` machine with **137,438,953,472 bytes (128 GiB)** physical memory. The configured engine checkout matched the reviewed pin. All fourteen LTX-2.3 and thirteen Gemma manifest files were present at their expected sizes, totalling **58,419,728,001 bytes**. LTX-2.5 files were absent. The engine Python dependency probe found MLX, the LTX pipeline and trainer modules without loading models. Model contents were not freshly rehashed in this inventory; readiness explicitly warns that its fingerprint uses file metadata.

Recorded evidence:

- [Read-only inventory JSON](benchmarks/2026-10-05-video-inventory.json)
- [Exclusive launch admission JSON](benchmarks/2026-10-05-video-admission.json)

**GPU inference was not executed.** A local application was listening on its configured backend port. The app’s GPU lease is in-process and cannot be reserved safely from an isolated benchmark process. The harness refused the launch with `running_app_present`; no running app, model cache or library was stopped or altered. Wall-clock inference time, MLX peak allocation, GPU output quality and real character training quality remain unmeasured.

## Reproducible harness

`backend/scripts/benchmark_video.py` performs inventory by default, creates temporary app configuration beneath the chosen output directory and writes an atomic `report.json`. It neither downloads weights nor accepts model licenses. Output must be separate from engines and caches.

```sh
backend/.venv/bin/python backend/scripts/benchmark_video.py \
  --engine-dir /path/to/reviewed/ltx-engine \
  --cache-dir /path/to/existing/ltx-cache \
  --output-dir /path/to/new/benchmark-output
```

For an operator-confirmed exclusive session with the app and other model work stopped, append `--run --exclusive-offline`. Set `--app-port` if the app uses a non-default port. The listening-port check is conservative, not an atomic cross-process GPU lock: the operator must prevent another model process from starting during the run. Optional `--reference /path/to/still.png` selects I2V; otherwise the baseline is T2V. Sizes are `704x448`, `704x1280` and `1088x1920`. The latter is a research benchmark case, not a new supported studio export preset.

The baseline requests 49 frames (about two seconds), seed 42, ten stage-one steps and three refinement steps, with offline environment flags. Workers use the existing owned supervisor and descendant drain, a twenty-minute deadline, decode/size/frame-rate/duration validation, and typed failure results. POSIX child RSS is reported for the CLI session; Windows marks it unavailable. RSS is not MLX unified-memory telemetry. There is no automatic memory-budget supervisor in this harness; observe memory and terminate a runaway exclusive run. The harness does not claim a Windows or Linux MLX inference path merely because inventory is portable.

## Acceptance matrix before claims or an engine upgrade

1. Run T2V and held-out I2V with the pinned old engine, exact cached revisions and no competing model process. Record wall-clock time, process RSS, actual MLX peak allocation if upstream exposes it, output hashes and full decode validation.
2. Repeat at 704×1280 before attempting 1088×1920. Confirm native geometry, duration, aspect and absence of unexpected music/speech. Reject memory runaway; record failures rather than retaining only favourable runs.
3. Run A2V with an existing music fixture and verify the maintained tiling/LoRA wrapper executes against reviewed source. Compare the identical fixture and settings on the candidate 0.16.0 port.
4. Train one consented reviewed character dataset and compare both fixed-prompt held-out projects. Record identity consistency and motion defects across all prompts; do not use training images as evaluation proof.
5. Export a dialogue reel and listen to the original cast soundtrack, check cue timing/captions and selective refresh, reload, undo and cancellation. Audio preservation and identity quality are separate acceptance conditions.
6. Claim platform support only after native installation/process-ownership evidence on that platform. Current generated video remains Apple Silicon MLX; portable inventory and CPU media tests do not establish cross-platform GPU support.

## Candidate experiment — 6 October

The exact candidate is commit [`bfa5755371a973651ea218ac3b56dcd34aa92c45`](https://github.com/dgrauet/ltx-2-mlx/commit/bfa5755371a973651ea218ac3b56dcd34aa92c45), after the merged [unfused adapter change](https://github.com/dgrauet/ltx-2-mlx/pull/191) and [image-conditioning preprocessing fix](https://github.com/dgrauet/ltx-2-mlx/pull/193). It identifies itself as 0.16.0; 0.16.1 was still a pending release during inspection. The reviewed core/pipeline Python source, package metadata and uv lock have aggregate SHA-256 `ab4006d332cffb58c3a6fdf580df2ace7d31638509a902ff3ca483e0d4f8af8c`. The research runner verifies that exact aggregate before importing candidate code. Unknown source is rejected rather than using the production compatibility patch against it.

The app and other model services were stopped for these runs. The candidate used a separate checkout/environment and the existing LTX-2.3/Gemma snapshots offline. No model weights were downloaded and the configured production engine, cache and library were not replaced. All cases used 704×448, 49 frames, 24 fps, seed 42, ten stage-one steps, three refinement steps and the same geometric-character prompt. Generated audio was disabled.

| Case | Wall clock | Peak child RSS | Validation |
| --- | --- | --- | --- |
| Production 0.15.12 T2V, low RAM/fused | 168.15 s | 13.55 GiB | Passed decode/geometry/duration checks |
| Candidate T2V, low RAM/fused | 163.30 s | 13.57 GiB | Passed decode/geometry/duration checks |
| Candidate I2V, resident/unfused selected, synthetic reference | 145.21 s | 21.22 GiB | Passed decode/geometry/duration checks |

These are individual runs, not a speed benchmark with repeated samples or a quality comparison. The I2V input was a deliberately synthetic odd-size image (673×431), not a held-out face. No character LoRA was supplied to that render; selecting unfused mode does not measure adapter influence or switching latency.

Evidence: [production case](benchmarks/2026-10-06-production-lowram.json), [candidate baseline](benchmarks/2026-10-06-candidate-lowram.json), [candidate I2V](benchmarks/2026-10-06-candidate-resident-i2v.json). Videos/logs and environments are retained outside Git under `/tmp/openfabric-ltx-oct6-*`. Reports include output/reference hashes. Cached weights were checked against manifest sizes but not freshly hashed during these runs; that limitation remains explicit.

The upstream candidate's `test_image_preprocess.py` and `test_unfused_lora.py` passed **38 tests** locally with offline environment flags. They exercise the official float-resize golden, EXIF/ICC/CRF behavior and synthetic quantized adapter attach/detach math. They do not test a trained character's appearance. Candidate `--low-ram` plus `--lora-mode unfused` is rejected by this harness because upstream still fuses streamed adapters; the high-rank distilled stage-two adapter also remains fused.

MLX telemetry is recorded separately from process RSS. Upstream VAE decoding resets the allocator peak counter, so the returned value describes the peak since that last vendor reset, **not the whole render's maximum unified-memory allocation**. The report states that scope explicitly; it must not be used to claim total peak reduction.

Reproduce the candidate after checking out the exact commit and creating its separate environment with `uv sync --frozen --no-dev`:

```sh
backend/.venv/bin/python backend/scripts/benchmark_video.py \
  --candidate --engine-dir /path/to/isolated/candidate \
  --cache-dir /path/to/existing/ltx-cache \
  --output-dir /path/to/new/experiment --run --exclusive-offline
```

For resident adapter experiments append `--memory-mode resident --lora-mode unfused --adapter /path/to/reviewed/character.safetensors`. Use `--reference /path/to/held-out.png` for I2V. Selected inputs are copied/hash-checked into the experiment directory before launch. Existing worker/output evidence is not overwritten. This candidate harness supports T2V/I2V only; it does not certify or replace the app's maintained A2V tiling/LoRA wrapper.

Remaining acceptance work: port and verify that A2V wrapper against exact candidate source, held-out consented faces and trained LoRAs, portrait geometry/memory cases, dialogue/lip-sync listening and repeated timing. The production engine pin remains unchanged.

## Candidate A2V compatibility — 7 October

The research harness now targets released **0.16.1**, commit `f0c12418afd601807199eaa1366584a2a53edcf0`, with aggregate Python/package/lock digest `50c81592904e4822890fd2cc76c243aee9c67f24a819465f731a062ace9e4130`. Production remains **0.15.12**. The earlier 6 October reports describe their historical candidate and require that historical app/source revision to reproduce.

The maintained overlay was ported against the exact released CLI and A2V pipeline, retaining separate half/full-resolution modality tilers. A first actual worker attempt found that 0.16.1 explicitly disables A2V tiling arguments in its parser. It failed before model inference; see [the failed attempt](benchmarks/2026-10-07-candidate-a2v-parser-failure.json). A candidate-only, exact-signature parser overlay enables those arguments as well as forwarding the tile configuration. Unknown or edited vendor sources still fail closed. Overlays are installed in memory inside the child; the vendor checkout and configured production environment are not rewritten.

A real offline A2V case then used a copied synthetic 2.2-second tone, no reference/character adapter, 704×448, 49 frames at 24 fps, seed 42, ten guided steps, three refinement steps, low RAM mode and two temporal/two spatial tiles. It completed in **710.999 seconds**, with successful output decode/geometry/duration validation. Peak child RSS was **11,065,737,216 bytes**; reported MLX peak was **13,850,911,956 bytes**, scoped to the allocator since the vendor's last reset, not the maximum of the whole render. See [the recorded case](benchmarks/2026-10-07-candidate-a2v-tiled.json). Cached LTX-2.3/Gemma weights were reused offline and not freshly rehashed; no weights were downloaded. Source and output hashes are in the report; media/logs remain outside Git.

This is one compatibility smoke case on the recorded Mac, not a speed comparison. CPU regressions ran concurrently, so its timing is not a controlled hardware performance measurement. Tone-conditioned geometric video does not establish face identity, speech preservation or lip synchronization. Candidate A2V with a character adapter is deliberately rejected. Before upgrading production, retain the held-out face/LoRA, portrait-memory, repeated timing, matching production A2V and audio-listening acceptance cases above.

Reproduce in an exclusive stopped-app session with the exact candidate checkout/environment and existing model cache:

```sh
backend/.venv/bin/python backend/scripts/benchmark_video.py \
  --candidate --engine-dir /path/to/ltx-0.16.1 \
  --cache-dir /path/to/existing/ltx-cache \
  --output-dir /path/to/new/a2v-case --audio /path/to/fixture.wav \
  --temporal-tiles 2 --spatial-tiles 2 --run --exclusive-offline
```
