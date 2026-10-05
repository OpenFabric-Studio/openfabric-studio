# LTX compatibility investigation and benchmark evidence

Date: 5 October 2026. The engine pin stays at **0.15.12**, commit `1724ca673d59f023a8a95efee06e5d36d61c2765`. Upgrading to 0.16.0 without verifying the maintained audio-to-video wrapper would break the evidence boundary, not improve it.

## Verified upstream source

The official 0.16.0 tag resolves to `90f76c20864ea612071afbb4e714ceea99e38e34`. Read-only inspection of pinned upstream source found that the A2V CLI still does not forward `tile_count` or the custom LoRA arguments used by the maintained wrapper. The two-stage A2V pipeline still creates an untiled stage-one `x0_model`, while stage-one implementation was refactored into a helper. The maintained 0.15.12 source patch therefore cannot be assumed to match 0.16.0. The inspected CLI exposes no stage-one latent resume contract.

| Official source | Inspected content SHA-256 |
| --- | --- |
| [0.16.0 CLI](https://github.com/dgrauet/ltx-2-mlx/blob/90f76c20864ea612071afbb4e714ceea99e38e34/packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/cli.py) | `2f9cec8a7ae3e4f0225f489ec878e9b5d09a2b2bb2078d443b44494eea20a5f3` |
| [0.16.0 A2V two-stage pipeline](https://github.com/dgrauet/ltx-2-mlx/blob/90f76c20864ea612071afbb4e714ceea99e38e34/packages/ltx-pipelines-mlx/src/ltx_pipelines_mlx/a2vid_two_stage.py) | `bfb494ada7158c65290a3f4bbc81f3bd0e0d6b35c46f32c920b656ca0edd69c8` |

These are source observations, not successful inference or compatibility measurements. Before changing the pin, port the wrapper against exact upstream files, test its argument and source-hash guards, and run the hardware matrix below. Keep the old executable/model snapshots available for rollback.

## Actual local evidence

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
