# Music LoRA pipeline evidence — 15 October 2026

> Historical record — HeartMuLa retired on 16 October 2026. Its generation, training and installation instructions below no longer describe supported functionality. See [current music documentation](local-music.md).

These are short synthetic execution tests on an Apple M4 Max with 128 GiB unified
memory, macOS. They do not evaluate musical quality, generalization, long-run
stability or CUDA. User recordings and installed base weights were not modified.
Model caches, logs, adapters and audio remain outside Git.

## End-to-end application checks

An isolated library/configuration used the prepared real environments, source
recordings referenced by library track IDs, actual FastAPI routes, supervised
workers and the real managed adapter registry. Each run trained for two steps
with rank 8, learning rate 0.0001 and seed 42 on six seconds of a synthetic
220 Hz tone. Reload recovery retained each registered adapter without retraining.
The corresponding real generator then loaded the registered ID and saved audio.

| Model | App training job | Adapter SHA256 | Saved generation job | Saved WAV |
| --- | --- | --- | --- | --- |
| Stable Small Music | `9cc22bb81c954489a06e03d6896a9731` | `79c9b98f60780e4f8bc7f80372767f0ba189fb59f421e776e6cb1efebd210f94` | `f7862bd6d27943d9bcf704f8a425ce49` | 5.00 s, 44.1 kHz stereo |
| HeartMuLa 3B | `7809b3cdcb3f40c2828110f69d2aff47` | `cccad73536abc17f1b4e452b6908aec4c2cea68d8f244cc9d74be56512933056` | `d4f9507013164b2ca17a24d0cfdefef4` | 5.04 s, 48 kHz stereo |
| YuE2 NAR | `6b3aeeddb0f24b05a299b25c41c3b034` | `f1d6d07aa0e142349d04d2746d12a90ea471f8510b824e97385b768d268a8939` | `d330477ffcda44f58276edfe94d80c2c` | 2.9987 s, 48 kHz stereo |

HeartMuLa's actual final optimizer loss was 3.3603658676. YuE2 logged finite losses
0.1467 and 0.5930. Native YuE generation used Q8_0, eight inference steps and
75 semantic tokens; it completed in 13.699 seconds including model loading.
No Q4_0 inference claim follows from this test. HeartMuLa's corrected-runtime base
comparison also saved 5.04 seconds through app job
`73e80e72bfe641878d1bf1df9281df79`, using the same adapter at strength zero.

Durable evidence: `~/.openfabric-studio/runtime/training-evidence/app-workflow/2026-10-15/`.
`app-evidence.json` records exact audio hashes and shapes. Job/generation JSON,
worker logs, private audio, adapter metadata and the local proof drivers are
retained there. This library is separate from the user's normal catalog.

## Earlier numerical and worker checks

- **Stable:** official source `77e0398c26f7d22d801ec1bb036307e8b231f741`, optimized
  assets `08c64b96b1e59942aade69759f60fb88c58c90c4`. A separate 12-second synthetic
  run produced 280 finite tensors and changed all 140 initially zero B factors.
  Encode/train took 13.14 seconds, with 4.86 GiB reported MLX peak memory. Native
  loading merged 140 targets and generated a five-second preview. The metrics
  belong to that short run, not the app run above. Medium assets are pinned and
  validated, but real Medium training was not exercised.
- **HeartMuLa:** genuine official encoder/decoder, numerical Q/K layout and rotary
  corrections, two MPS steps, complete saved/reloaded adapter and official codec
  decode. See [detailed Heart evidence](heartmula-training-proof.md), including
  exact source/model hashes and limits. The existing MLX generation tree is
  unchanged; adapters use the corrected separate PyTorch runtime.
- **YuE2:** trainer `6a9355efff60589d93b41b569676f09d5db85d09`, ComfyUI
  `b058ec652802e1de24f1d429b3c7c1fb868f791c`, BF16 checkpoint SHA256
  `33765adbf9813c9a50318218760b2fd819a319862460a04884607581961c6fee`.
  An earlier six-second compact acoustic proof changed all 112 initially zero
  down factors (7.34 million trainable parameters), with finite losses 0.1574523
  and 0.5260044. All 112 fused projection delta actions matched converted native
  factors exactly in the tested numerical comparison. Native audio.cpp v0.8.1
  (`f2b4937`) loaded them and produced finite Q8_0 audio. Training took 13.4488
  seconds total and reported peak RSS 10,156,883,968 bytes; this is not GPU peak
  memory. AR composition and acoustic heads stayed frozen.

Earlier evidence directories are `training-evidence/stable/2026-10-15-synthetic/`
and `training-evidence/yue2/2026-10-15-synthetic-bdbe33419cc342ecb7fdef63ab0d0e7b/`
under the same runtime home.

## Automatic preparation and portable fallback

Actual module setup completed Stable training-asset job
`7220b1242d5a46348e2c76ebc081b8d0`, Heart job
`b19ae1cbe7f24c078402c96999aeda3d` and YuE2 job
`69025b9b78c641d7bc92337f7acbc417`. Prepared dependency sets match all 37 Heart
trainer, 33 codec and 79 YuE pins, with successful pip checks and imports.

Fresh installation exposed a YuE import-directory shadowing bug. The corrected
check runs from the reviewed checkout; a newly reviewed setup reused existing
files and completed. The original failed job remains intact.

The portable package has been tested through actual API submission, worker,
publication and ZIP download using temporary data, without models or network.
Original bytes, integrity manifests, source pins and notices were checked. Actual
mid-ZIP cancellation drains the process and removes private staging/partial ZIPs.
The installed desktop `resources/backend` + `resources/licenses` layout is covered.
**CUDA execution remains unverified.** No remote account, upload or paid compute
was used.

## Repository verification

Final checks on this working tree:

- Frontend: `npm test` — 1,633 tests passed across 168 files. `npm run build`
  passed strict TypeScript, the type-safety guard and production bundling.
- Backend: isolated full unittest discovery — 2,264 tests, OK, with 15 optional
  dependency/platform skips. Both earlier outdated API/inventory assertions were
  corrected and the complete suite rerun successfully.
- Optional Heart worker: all five CPU tests passed in its real isolated codec
  environment, without models or downloads. Strict worker typing passed against
  actual Torch and pinned upstream source.
- Contracts: generation drift check passed. All 21 strict core mypy invocations
  passed across Linux, macOS and Windows typing paths.
- Desktop: 87 tests passed, one platform skip. Installed resource license lookup
  also has a backend regression test.
- Independent connected-code review found no remaining blocker after publication,
  cancellation telemetry, ZIP cleanup, desktop licenses and Heart generator fixes.
- Final diff whitespace checks passed. Nothing was committed or pushed.

## Practical limits

Prepared and experimental are separate from measured quality. The tests establish
short training, artifact compatibility, loading, owned cancellation/publication
and persistence on this Mac. They do not prove a useful learned musical style,
long clips, large datasets, lower-memory Macs, Stable Medium, native YuE Q4_0,
CUDA or full-state checkpoint resume. Evaluate held-out recordings separately.
Browser visual inspection was unavailable because the computer-use browser
connection exposed no browsers; component behavior and accessibility controls
were checked through the frontend tests.
