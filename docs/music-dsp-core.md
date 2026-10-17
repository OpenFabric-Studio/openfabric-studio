# Shared music DSP core

The new CPU helpers are called by an existing workflow owner. They do not add a queue, database, detached task, public filesystem path, model dependency, or automatic installation. A caller must resolve and retain immutable source snapshots, reserve its own work, own every subprocess, persist results and lineage, and publish only the returned validated artifact.

## Interfaces and ownership

`app.music_dsp_contracts` defines the generated-contract family. Sources are the shared version/stem/production-asset reference union; mix inputs can additionally reference a revision-bound transform artifact. Identifiers are 32 lowercase hexadecimal characters and SHA-256 values are 64. Revisions, source hashes, the complete request, actual frames/channels/rate, measurements, configuration identity, engine identities, and warnings accompany each output.

The internal entry points are:

```python
async def render_transform(request, source, staging_root, run, *, native=None) -> DspTransformResult
async def render_mix(request, sources, staging_root, run) -> DspMixResult
async def align_prepared_vocal(request, reference, prepared, staging_root, run) -> AlignmentResult
def level_match_gain(reference, prepared) -> LevelMatchGain
```

Requests/results have explicit Pydantic types in the corresponding modules. `ResolvedDspSource` is an internal record containing a canonical backend-resolved `Path`, source reference and hash, native rate/channels/frame count, and optional existing source-bound `AudioMetrics`. Public request models accept no path or arbitrary command/filter options. The caller's `RunCommand` receives an argument list and timeout, returns bounded captured bytes, and must terminate and await all descendants before returning or raising. Unverified process failure must remain distinguishable to the owner. Failed workspaces remain owned by that caller; deleting a pathname cannot stop its writer.

The optional `NativeStretch` callback takes float WAV input/output paths plus keyword-only `sample_rate_hz`, `channels`, `input_frames`, `output_frames`, `anchors: tuple[tuple[int, int], ...]`, `pitch_shift_semitones`, and the same `run` callback. It returns `DspEngineIdentity`. No native runtime discovery or download occurs here. Missing stretch support returns `dsp_stretch_unavailable` before processing; identity operations bypass native processing.

## Time, frames, and stereo

Anchors map **source seconds into target seconds**. Both axes must increase strictly and each local target-duration/source-duration ratio must lie within the requested range, itself bounded to 0.75–1.5. Runtime validation repeats these checks after rounding to actual source/output frame grids and rejects collapsed intervals. Pitch is bounded to ±12 semitones; shifts beyond ±2 carry `pitch_artifact_risk`.

A transform trims the first-to-last source span. Its output contains exactly `round(last_target * output_rate) - round(first_target * output_rate)` frames. It does not insert leading target silence. `target_start_frame` preserves the target timeline position for the owning arrangement. Native input and anchors are rebased to zero after trimming and any explicit resampling. No output is padded or truncated to hide a failed stretch engine.

The default preserves native sample rate and channel count. Explicit output rates are 44.1 or 48 kHz. Mono-to-stereo duplicates the channel; stereo-to-mono is an explicit arithmetic average. Otherwise stereo remains linked and independent channel content is retained. Mix pan is a stereo balance attenuation with center as an identity; a mono request with nonzero pan is rejected. Float amplitudes are retained, including genuine overshoot. Native adapters must separately demonstrate their clipping/headroom behavior.

Mix inputs use source trim, target sample offset, explicit silence padding to the requested arrangement length, and non-normalizing summation. Source hashes are checked before work and after the final output measurement. The core produces a private UUID-named FLOAT WAV and verifies its exact sample rate, channel count, subtype, frame count and measurement identity before returning it.

## Effects and measurement

Gain is bounded to ±12.0412 dB. EQ uses at most ten verified FFmpeg `equalizer` bands, each ±6 dB, Q 0.1–12, with total positive band gain no greater than 6 dB and frequencies below 0.45 of output rate. Compression is linked RMS/downward with bounded threshold, ratio, knee, attack, release and makeup gain. Fades use verified `afade` linear or quarter-sine curves and frame endpoints; overlapping fades are rejected.

Ducking uses the dry, timeline-aligned sidechain, linked stereo detection, and a parallel dry floor that limits maximum reduction. A zero-reduction request bypasses the compressor. Sidechains must exist and the declared graph must be acyclic. This is a bounded compressor envelope, not an assertion of perceptually perfect vocal placement.

`audio_quality.measure` remains the sole loudness and true-peak implementation. Optional loudness normalization uses its existing measured filter and then measures the actual normalized output. Target status is derived from those output measurements. Reference matching requires at least three seconds and finite LUFS on both sources; unavailable loudness yields zero applied match gain and an explicit warning. Capped match gain retains residual LU and clipping-risk warnings. It never claims a target was reached from the requested gain alone.

The FFmpeg executable SHA-256, version-output digest, request/configuration digest and implementation digest participate in engine/configuration identity. Native identity can additionally retain its executable hash, exact recipe, compensated power-of-two headroom exponents and license evidence identifiers. These record execution and supplied terms; they do not clear recordings, training data or output rights.

## Automatic alignment and budgets

Automatic alignment is a NumPy implementation, not an exact librosa implementation or a beat detector. It derives channel-power chroma, normalized MFCC summaries, positive spectral flux and energy on a 20 ms grid. Power is combined after channel FFTs so antiphase stereo does not disappear. Energy identifies activity phrases; an ordered dynamic program matches phrase descriptors without reordering lyrics, then a banded monotone DTW proposes phrase anchors. Confidence and measured onset error remain null. Automatic and manual results require review.

Sources may be up to 7,200 seconds. The decoder uses at most 120-second windows at 22.05 kHz (2,646,000 frames, at most two channels) with ten seconds of context overlap. Central retained intervals meet at explicit boundaries; a phrase crossing a boundary remains ambiguous. Whole-song PCM is never assembled in NumPy. Oversized individual phrases remain in diagnostics while other safe phrase candidates can still be inspected. No unsupported automatic beat grid or meter is synthesized.

Bounds before allocation/work include:

| Resource | Bound |
| --- | --- |
| Original source bytes | 8 GiB |
| Sources per mix | 32 |
| Combined activity phrases | 1,024 |
| DTW phrase axis | 2,000 feature frames (40 seconds) |
| DTW matrix cells | 4,000,000 |
| Returned anchors | 4,096; excess phrases retain a budget diagnostic |
| Native transform span | 600 seconds for uniform maps; 30 seconds for non-uniform maps |
| Scratch free-space reserve | 1 GiB in addition to estimated PCM/features |
| Mix filter graph | 128 KiB |

At the DTW cap, the principal matrices occupy about 52 MB: float32 cost, float64 accumulated cost and uint8 step matrix. Phrase matching is bounded by the combined phrase count. Each two-hour feature stream is at most 360,001 × 27 × 4 bytes on disk (about 38.9 MB), with a float32 energy array of about 1.44 MB in memory. Only one decoded window and bounded phrase blocks are loaded at a time. These are allocation bounds, not measured process RSS or a two-hour latency claim. Decoded files and rendered outputs use header-derived byte caps, packet allowance, finite-sample checks and exact frame verification. Long transforms are refused by the native-span cap rather than secretly stitched.

## Verified evidence and limits

Focused tests use temporary libraries, generated tones/impulses and real installed FFmpeg for rendering. They cover exact stereo identity, nonzero source/target offsets, gain/EQ/fade endpoints, mix offsets and silence, linked bounded ducking/compression, zero-effect bypasses, measured final loudness, hash changes, malformed metadata, wrong frame counts, unavailable native support, typed command deadlines and retained unverified partials. Alignment fixtures cover drift, silence, repeated/missing/swapped phrases, manual native-grid anchors and pre-allocation budget refusal. A real 132-second source is decoded as 0–120 and 110–132 second windows, retains exact feature counts, leaves the original unchanged, and marks its crossing phrase uncertain.

The inspected local FFmpeg 9.0.1 has the filters used above but no `rubberband` filter. A separate optional Rubber Band adapter is owned by `music_stretch.py`. Its source, binary and measured behavior must be reviewed independently. Upstream references are [Rubber Band](https://breakfastquay.com/rubberband/), the [v4.0.0 CLI source](https://github.com/breakfastquay/rubberband/blob/v4.0.0/main/main.cpp), and [upstream license text](https://github.com/breakfastquay/rubberband/blob/v4.0.0/COPYING). Synthetic native timing/pitch/stereo probes do not establish musical quality, formant preservation, cross-platform installation support or legal clearance. The owning workflow must present and retain the dry source, reviewable candidates, measured diagnostics and explicit acceptance.
