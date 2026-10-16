# HeartMuLa Auto generation verification

> Historical record — HeartMuLa retired on 16 October 2026. Its generation, training and installation instructions below no longer describe supported functionality. See [current music documentation](local-music.md).

16 October 2026, Apple Silicon Mac with 128 GiB unified memory. No model files
were downloaded. Existing adapters and source audio were preserved.

Auto chooses one random seed before durable job admission, keeps native sampling
defaults and respects audio EOS. Both runtime paths check the real tokenized
prompt before loading model weights. A context-shortened maximum is validated
and persisted before audio publication. Manual requests are never shortened
silently.

The supported app ceiling is 480 seconds. The base test decoded more than eight
minutes, ending naturally before its requested 600-second maximum. The adapter
boundary test decoded and saved exactly 480 seconds. A requested maximum alone
is not proof of that much generated audio.

| Runtime | Job | Requested maximum | Actual audio | Observation |
| --- | --- | --- | --- | --- |
| Corrected PyTorch with LoRA | `056de11bbdeb4a3c83aef1c6e20f67ce` | 600 s | 11.84 s | Natural EOS after 148 frames; 16.64 s including model loading and token generation. MPS driver allocation after generation: 8,634,941,440 bytes. This is not a peak-memory measurement. |
| Native MLX base | `80f2c59ae047423a852065b0409c7b8f` | 600 s | 483.92 s | Natural EOS after 6,049 frames. Completed generation, decoding and library publication in approximately 32 minutes. Last reported active Metal allocation during token generation was 13.2 GiB; decoder peak was not measured. |
| Corrected PyTorch with LoRA | `6e192431cada49baa3f30446f892fc46` | 480 s | 480 s | Reached the requested ceiling with 6,000 frames and the existing trained adapter, seed 0 and original lyrics (592 prompt positions). Loading plus token generation: 417.32 s; library publication at 9 min 40 s from admission. MPS driver allocation after generation: 10,615,701,504 bytes, not a decoder peak. |
| Auto through the browser, PyTorch with LoRA | `1e8cb376cd90499b88cd52a0958117ba` | 480 s | 14.8 s | Backend selected seed 736278630; mode, seed and ceiling remained unchanged after browser reload while running. Natural EOS after 185 frames; decoded and published to the library. |

New requests and queue normalization use 480 seconds. The 600-second technical
schema/worker bound remains for historical job compatibility. Auto can shorten
the maximum further for long lyrics; natural EOS can produce a shorter song.
The application was restarted after the tests; both long outputs remained
available with their original request settings and saved library records.
Browser verification confirmed Auto as the default, hidden manual inputs,
the Manual input maximum of 480, adapter selection, reconnection to the live
Auto job and its final saved history entry. The app remains running for testing.

Private run receipts, logs and saved job details are under
`OpenFabricStudioData/live/recovery/heart-auto-2026-10-16` and the owned local-music
job folders outside Git. Audio quality has not been evaluated by this check.

## Automated verification

- Full backend discovery: 2,465 tests, 18 skipped, passed. Later duration-policy
  and recovery changes also passed the affected isolated suites (160 tests),
  followed by 152 tests after the Auto admission-order correction.
- Full frontend suite: 1,779 tests across 170 files passed. Strict TypeScript and
  the production build passed.
- Generated contracts: `generate_contracts.py --check` passed.
- All seven CI strict mypy groups passed for Linux, macOS and Windows. Later
  touched files passed the corresponding three-platform checks again.
- The optional HeartMuLa worker passed its strict typing check against the
  actual prepared trainer packages. CPU worker regressions passed; these checks
  do not substitute for the real generation evidence above.
- Desktop suite: 87 passed, one skipped.

Tests used isolated temporary configuration, data and engine roots. Model
downloads and the user's live library were excluded from unit tests.
