# Trend report: frontend reliability

The 12 October brief identifies useful reliability work in the existing frontend.
This batch implements local calendar date presets and complete handling of pending
audio warps. It adds no engine, dependency, API, storage schema or network access.

## Evidence and selection

- `useDateFilterSort` formats preset boundaries as UTC dates but filters timestamps
  against local midnight. Both ACE-Step and YuE2 use this helper.
- `useTimelineEngine` applies the requested warp factor to an original buffer when
  the stretched buffer is absent. `TimelineLane` makes the same fallback for the
  waveform. Export snapshots buffers before pending stretches finish.
- Cast inspection, passage ASR checks, encoder identity checks, loaded warp
  restoration and capped export-tail fading already exist; do not duplicate them.
- The LTX wrapper verifies exact 0.15.12 sources and patches A2V tiling in memory.
  An engine upgrade needs a reviewed wrapper port and hardware evidence.
  [0.16.3](https://github.com/dgrauet/ltx-2-mlx/releases/tag/v0.16.3) includes
  distilled A2V; the [low-memory LoRA fix](https://github.com/dgrauet/ltx-2-mlx/pull/213)
  was merged after that tag. Do not claim that release contains it.

## Behavior

Format preset dates using local calendar fields, retaining inclusive local-day
boundaries and calendar subtraction across daylight-saving changes.

Resolve an audio buffer together with its actual time factor in one shared helper.
An original fallback uses factor one; a ready warped buffer uses the requested
factor. MIDI and saved timeline coordinates retain their existing behavior.
Waveforms sample the selected buffer using its factor, without changing clip width.

Share in-flight stretches for the same original buffer and tempo ratio. Late
results cannot modify another editor session. When a new warped buffer becomes
available during playback, reschedule at the current playhead. Export waits for
the captured project's required stretches and stops with a safe error on failure.
Muted and solo-excluded audio cannot block export. Playback follows the selected
buffers and time factors, including cached-tempo and Warp-disable transitions;
each completed stretch is adopted without waiting for unrelated stretches.
Keep source recordings and saved trims unchanged.

## Verification and limits

First reproduce UTC-boundary filtering and pending-warp failures. Test ready,
pending, failed and stale stretches, MIDI behavior, source waveform selection,
export waiting and playback rescheduling. Run frontend tests and strict build,
then check generated contracts. Web Audio doubles verify scheduling and selected
PCM boundaries; they do not establish audible DSP quality or GPU compatibility.

Ideas are reimplemented in this repository, with attribution in `NOTICE`:
[Remiqora local dates](https://github.com/inikolax/remiqora/commit/b4d4afe6494f5aa9a259c63a37f53788c3d67501)
and [pending warps](https://github.com/inikolax/remiqora/pull/47).

## Recorded verification

The initial frontend run exposed 51 real request attempts from missing API mocks
in `VoiceStudio.test.ts` and `pollingControls.test.ts`. Add those mocks and a
default fetch guard that fails tests even when components catch request errors.

The oversized entry chunk included unused JSON Schema titles, descriptions and
defaults. Omit these annotations at schema nodes in the generator; preserve
domain properties with those names and every validation constraint. Generated
types and validator functions remain unchanged. Independent review confirmed
that all 329 schemas differ only by removal of these annotations.

- `npm test` from `frontend/`: 140 files, 1,256 tests passed without localhost
  connection errors or unhandled exceptions.
- `npm run build` from `frontend/`: strict type policy, TypeScript and Vite build
  passed without the size warning. Entry bundle: 504.95 to 451.08 kB; gzip:
  129.21 to 119.95 kB.
- Installed `backend/requirements-test.txt`; full backend unittest discovery:
  1,528 tests, passed with 10 platform/tool skips. Used temporary configuration,
  library, Seed-VC and module paths. Expected failure-path logs remain.
- Both strict CI mypy commands passed for `linux`, `darwin` and `win32` paths.
- `backend/.venv/bin/python backend/scripts/generate_contracts.py --check`:
  passed with temporary configuration, library, Seed-VC and module paths.
- `git diff --check`: passed. Independent review findings were reproduced with
  regressions and corrected; the final diff was inspected.
- No browser DSP, GPU inference or unaffected desktop suite was run.
