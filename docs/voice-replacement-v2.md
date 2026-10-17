# Durable replacement backend

The v2 owner is `app.replacement_jobs`, using the existing SQLite catalog, source evidence graph, GPU lease, separator, converter and CPU DSP helpers. It does not create another engine server. Legacy `/api/voices/replace` behavior remains separate and compatible.

`app.replacement_contracts` is the public schema authority. Requests select hash-bound versions, stems or production assets; they accept no filesystem paths, filter expressions or executable options. Prepared mode needs no voice. Convert/hybrid require an actual selected Seed-VC model revision or a registered RVC model tied to an existing voice and consent.

## Requests and retained work

The `/api/voices/replace` router exposes capabilities, storage preview, source intake, plans/history, revisioned request/mix/alignment edits, prepare, preview, render, remix, retry, cancellation, artifact audio and the RVC registry. JSON bodies are limited to 64 KiB; alignment edits have a separate 256 KiB/1000-anchor and phrase-decision bound. Mutations use the existing local-origin guard.

Actions carry `expected_revision` and a 32-hex `request_id`. An identical replay attaches before fresh resource, revision or consent admission. A reused ID with different content is a conflict. Cancel remains available to a stale editor. A completed render and its library track attach in the same transaction; a later status-write failure cannot turn an already published track into a failed computation or duplicate it on retry.

`POST /assets` is a bounded wrapper around existing evidence, library tracks and retained versions. Multipart fields are `metadata` (`ReplacementAssetImport`) and one `audio`. Source/prepared inputs are at most 256 MiB and two hours; IR input is WAV/FLAC, at most 16 MiB and ten seconds. Workflow roles do not grant rights. Source declarations remain references, and private replay receipts prevent uncertain uploads from creating duplicate tracks. This intake does not analyze, separate or convert audio automatically.

Private media lives below `FILES_DIR/replacements/<plan_id>`: immutable raw/decoded sources, stage artifacts and receipts, retained model copies, and attempt workspaces. Worker identities remain private in the attempt's `worker_json` and its typed body. Intake uses `artist-production/sources/replacement-intake/<request_id>`; imported RVC bytes use `replacement_models/rvc/<id>`. Public metadata contains IDs, hashes and app-relative audio URLs. Originals and completed stage artifacts survive failure, cancellation and plan soft deletion. Physical cleanup requires a separate reviewed inventory action.

Each new processing attempt checks current free space with a reserve. FFmpeg decode has header-derived byte/time caps and rejects duration disagreement, including a FLAC header that understates actual decoded duration. CPU arrays are bounded; cleanup processes at most 65,536 central frames plus 8,192 context frames on each side. Model files and audio are hashed/copied in bounded chunks. No job installs or downloads a dependency.

## Processing semantics

Separation uses a verified local Demucs repository and private local model copies. It never reuses catalog stems as proof of a different version. Paired RoFormer backing remains unavailable. Automatic alignment is a proposal requiring explicit phrase review; unmatched or ambiguous phrases are not silently accepted as a performance.

Native stretch/pitch uses the optional reviewed Rubber Band adapter. Uniform input is limited to 600 seconds per native call; nonuniform maps to 30 seconds. Longer reviewed maps use explicit overlapping phrase transforms, linear 10 ms crossfades and sample-offset sums, grouped into at most 32 inputs per FFmpeg mix. Uncovered timeline regions remain silent. The per-phrase native recipes, executable hashes and exact frame counts are retained. This is an explicit stitch recipe, not evidence that a whole-song nonuniform native invocation met the timing threshold.

`cleanup=light` reuses the existing conservative falling-tail attenuator with bounded context, no cropping or normalization, and dry audio retained. It is not guaranteed dereverberation. `keep_breaths=false` means shared-stereo attenuation of low-level content by at most 3 dB; amplitude cannot identify breaths. These recipes are recorded in artifact processing diagnostics. Explicit convert-mode pitch also passes through native DSP before timbre conversion; absent native support fails instead of ignoring the request.

Mixing measures actual LUFS, peaks, gain and residual mismatch, with bounded explicit EQ/compression and no hidden normalization. Owned IR convolution requires existing human/third-party source evidence, declared production use and an explicit approved review. It records the IR hash, wet amount and L1 headroom gain, disables FFmpeg automatic IR normalization, and trims/pads to the original timeline. Wet is at most 25%. Dry input, antiphase stereo, over-unit float peaks and exact frame counts have CPU regression coverage.

Remix requires the retained dry vocal and backing for the selected revision. Missing/changed stages return structured errors; remix never launches a separator or converter to replace them. Startup recovery terminates verified workers before marking interrupted work; uncertain writers remain busy and block source/model removal. Shutdown drains owned tasks/processes and request-owned intake probes. Closing the UI does not cancel work.

## RVC trust and identity

Registration treats checkpoint/index bytes as opaque. It never imports PyTorch or deserializes a checkpoint. The inspected upstream pin is `81eed5e8f68b6bed1789f682fe78cdd324495afc`; its [CLI](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI/blob/81eed5e8f68b6bed1789f682fe78cdd324495afc/infer/cli.py) and [conversion loader](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI/blob/81eed5e8f68b6bed1789f682fe78cdd324495afc/infer/vc/modules.py) use `torch.load` without an explicit safe mode. Import therefore requires an explicit trusted-local-file and code-execution acknowledgement, plus model/source declarations and the existing voice authorization. A supervised subprocess is not a security sandbox.

Runtime compatibility requires an existing matching setup-verification receipt, environment fingerprint, reviewed entrypoint hashes and local HuBERT/RMVPE assets. Actual auxiliary/model hashes are retained; this is not a complete dependency-rights or whole-checkout security audit. Explicit index rate zero prevents the upstream CLI from discovering an unrelated index. Registered checkpoint/configuration copies protect accepted work from later voice-model replacement; deletion/training hooks also prevent mutation while a worker remains owned.

The upstream path normalizes input and writes its own output subtype. It is not claimed amplitude-transparent or stereo-preserving. Output is measured and duration-checked. Registry `ready` describes runtime compatibility, not musical quality, permission or legal clearance. Unknown and noncommercial source/model declarations remain in retained output lineage. Observed local hashes are kept separately from publisher-declared license identities. Private evidence/rights notes are excluded from shared music evidence; fields explicitly labeled public publisher/license URLs may appear in exports.

## Evidence and remaining qualification

Tests use temporary libraries, opaque fake checkpoints and mocked model boundaries. Real CPU fixtures cover FFmpeg decoding, cleanup, stereo/frame accounting, convolution, alignment/review, durable publication, remix, export lineage after original deletion and process cancellation. No real RVC/Seed-VC checkpoint loading, GPU end-to-end run, held-out song listening evaluation or quality threshold is claimed by those tests. The new advisory measurements below have synthetic CPU evidence; held-out real-song calibration and musical listening acceptance remain open.

## Sampled vocal diagnostics

Preparation and rendering retain a revision-, request-, alignment- and artifact-bound diagnostic report. Editing request, timing or mix settings invalidates the old report. Refreshing unchanged prepared inputs preserves saved timing and phrase review. Saving timing edits first avoids silently replacing an unsaved mapping.

The pitch worker uses an already compatible backend or verified managed CPU environment; it never installs packages or downloads a model. If none is compatible, pitch is explicitly unavailable. It samples at most twelve disjoint five-second windows per artifact and reports reliable voiced support, observed pitch quantiles and unknown confidence. These observations are not a singer-range qualification or an artist-profile update. Prepared-fit suggestions require reviewed corresponding windows and stay within a total ±2 semitone advisory bound. Conversion drift accounts for explicitly applied input pitch and does not propose transposing unchanged backing.

The tonal worker compares six octave bands from 125 Hz to 4 kHz, with the same bounded sampling. Half-strength, centered and smoothed EQ proposals bound each band and total positive boost to 6 dB. Actual post-processing band differences are descriptive measurements, not a promise of improvement. Signed lagged correlation against backing includes ambiguous lags and polarity; shared instruments can correlate, and weak correlation does not establish an absence of bleed or identify a singer. Unmapped or insufficient material stays inconclusive.

Workers reuse the replacement attempt’s existing process owner. Optional runtime failures are advisory only after verified drain. Artifact/header/hash mismatches remain fatal; uncertain cleanup stays owned and blocks conflicting work. Reports retain exact identities and implementation/configuration evidence. Suggestions only enter the UI draft and require saving and listening comparison.
