# Regional dataset review and comparison

This is a local, rights-aware workflow around the existing saved dataset library,
Stable Audio training owner and local music generation owner. It creates no
second dataset catalog and does not acquire recordings, install models, start
training or establish cultural authenticity automatically.

Select a saved dataset in the Stable Audio adapter page's regional review panel.
Inspect each recording explicitly, review its caption and declared cultural
labels, then record source-scoped permission evidence. Listening/reference,
training, source redistribution and adapter distribution are separate decisions.
Evidence contains a document hash and public URL or opaque private reference;
private contracts and arbitrary filesystem paths are not uploaded. Author,
performer, acquisition date and original filename are optional declarations,
never inferred from filenames. Unknown values remain unknown.

Unknown required listening/training permissions, conflicting grants/denials, NC/ND sources and unreviewed
captions are excluded from this commercially oriented dataset build. That is a
conservative application policy, not a legal determination about a source.
An exact file/decoded copy cannot bypass a known conflicting permission under a
different filename. General unmanaged training inputs elsewhere in the app are
not globally inspected or certified by this workflow.

Saving creates an immutable review revision through compare-and-swap. Preview
rehashes the selected saved sources and binds the split/storage forecast to that
revision. The source must still match when preparation starts. Explicit storage
confirmation authorizes a new retained preparation; network retries reuse its
submission key. A separate fresh-attempt action keeps the previous workspace and
requires another storage review.

Splitting groups exact file hashes, canonical decoded float32 hashes and declared
original-recording, performer, session and related-take identities before assigning
any crop. All included excerpts of one group stay together. Renaming record IDs does not
reshuffle the split. Evaluation-only records force their whole related group out
of training. The proposed 15% holdout is approximate because groups are indivisible.
Declared instrument/technique strata are represented in both splits when the
group budget permits it; incomplete/unknown coverage is a warning. Optional
acoustic candidate screening is described below. Unknown provenance cannot become independent
evaluation evidence merely by making more crops.

## Optional shared-audio candidate screening

An explicit CPU scan can inspect the selected excerpts before preparation.
It uses the existing regional task owner, local FFmpeg and NumPy; it installs or
downloads nothing and takes no GPU lease. The scan request binds a saved review
revision/hash and an idempotency key. Browser navigation does not abandon it.
Cancellation drains its supervised process tree; an unverified exit keeps the
regional owner busy. Restart verifies/stops recorded workers, marks unfinished
scans interrupted and never automatically rescans. Private requests can contain
server-resolved source paths; public reports, decisions and exports do not.

The deliberately modest `regional-landmarks-v1` heuristic decodes 8 kHz PCM in
sequential windows of at most 64 seconds, retaining separate channels. Each
256 ms anchor uses a 1,024-sample Hann spectrum and its strongest 80–3,500 Hz
peak. Quantized peak pairs at the next four anchor lags vote for relative time
offsets. The strongest eight offsets require at least 12 matching pair votes,
six distinct anchors spanning three seconds, and median agreement of at least
0.9 between normalized 16-band log spectra. These thresholds are uncalibrated
screening policy, not probabilities. This is an own small implementation inspired
by [landmark fingerprinting](https://www.princeton.edu/~cuff/ele301/files/Wang03-shazam.pdf),
not a claim to reproduce a commercial matcher or its measured accuracy.

The scan preserves existing limits of 100 sources/excerpts, 600 seconds per
excerpt, 512 MiB per source and 8 GiB aggregate source bytes. Packed fingerprints
are capped at two million hashes; each pair allows at most 256 occurrences of a
hash and 100,000 offset votes. The scan worker has a 900-second analysis deadline
and its owner enforces a 915-second process deadline. Budget exhaustion fails
with no usable report rather than silently truncating comparisons. Silence,
steady tones, short excerpts and insufficiently varied spectra are explicitly
`insufficient_information`; attaching such a scan keeps new evaluation
insufficient. Only selected excerpts are screened, not unselected portions of
the original recordings. Pitch/time transformations, noise, repeated material,
codec/decoder differences and other edits can cause missed or false candidates.
A zero-candidate result never proves independent recordings or different
performers. FFmpeg build identity and real-world detection accuracy remain
unmeasured; the configuration binds this implementation and NumPy version.

Each candidate means possible shared sound, not equal recording identity,
performer identity, permission or cultural authenticity. Unresolved candidates
join conservative split-control components before holdout assignment, and an
evaluation-only member keeps its whole component out of training. Candidate
edges do not transfer rights or override exact-content/provenance grouping.

For audition, a completed scan retains paired WAV FLOAT clips of at most
12 seconds, at 8 kHz with the original channel count. Native source-frame spans,
actual file hashes/headers and the intentionally limited audition rate are
recorded. Total audition storage is hard-capped at 32 MiB; preflight requires
that budget plus the existing 64 MiB floor. Candidates beyond capacity visibly
remain without clips and stay together; a narrower explicit scan is needed for
their distinctness review. This does not retain entire excerpts or originals.
Serving a clip reads only the owned completed artifact after hash/header checks,
supports HTTP ranges, and never falls back to live media or decodes on GET.

An explicit `distinct_after_review` decision requires reviewer/date/reason and
both retained audition clips to verify when saving, previewing and preparing.
It can release only the acoustic edge; exact hashes or declared recording,
performer or session relationships still keep the recordings together. The
immutable prepared manifest retains the selected report and its complete
`acoustic_review` snapshot, including the scan identity/hash and reviewer
decisions. Their hash binding and retained source spans are validated together;
later review edits do not alter that snapshot. Source
hashes, decoded hashes and excerpt spans must still match; caption/rights edits
alone can reuse a scan. Every original is rechecked after scanning and audition
creation, including byte changes which leave decoded PCM unchanged.

Older reviews/manifests remain usable with `legacy_unscanned` status and the
existing unavailable-screening warning. Newly absent fields are omitted during
canonical serialization so historical review/manifest hashes remain unchanged.
No historical training run requires a rescan. Neither the legacy path nor this
heuristic certifies absence of leakage, rights clearance or adapter quality.

Preparation retains independent full-original copies, then creates WAV FLOAT
excerpts at exact source frame spans. The reference is decoded float32 PCM; it is
not a claim to preserve a source's higher-precision integer/double representation
bit for bit. Original media is never modified. Sources are limited to WAV/FLAC/MP3,
512 MiB each, 8–192 kHz, one or two channels, and 7,200 seconds. A build has at most
100 recordings and 100 excerpts, and retains at most 8 GiB of source/excerpt data.
Excerpts are 0.1–600 seconds and at most 512 MiB. PCM processing uses 65,536-frame
blocks, finite/peak checks and per-chunk deadline checks; cancellation drains the
owned reader before releasing its inputs. Disk admission includes the known
source copies, two crop budgets and a 64 MiB floor. It is not a measured forecast
of subsequent MLX training memory or training checkpoint growth.

Jobs survive browser navigation. Restart marks every active preparation
interrupted, including jobs outside the 100-row UI history window, and never
starts inference or deletes retained files automatically. A completed private
manifest is hash-bound in the job before database publication. If publication
fails, explicit retry-save verifies the retained original/crop hashes and saves
them without recropping. Incomplete preparations require an explicitly fresh
attempt; partial work is retained rather than silently swept.

Only the manifest's exact training excerpts and reviewed captions can enter its
regional training submission. Held-out or foreign sources are refused. Native
Stable workers receive each expected excerpt hash and compare actual bytes
before encoding. Existing trainer conversion to 44.1 kHz stereo PCM16 remains
explicitly additional preprocessing; its actual output hashes are retained in
the trainer dataset receipt. Legacy non-regional request fingerprints and runs
remain compatible.

Regional training measures selected model-file hashes, the bounded native Python
source tree, Python executable/version and MLX/NumPy package metadata before and
after work. The trainer dataset receipt records those measurements; its digest
is bound by the existing completion receipt and registered adapter hash. These
are selected runtime identities, not a complete operating-system snapshot or a
guarantee of deterministic GPU behavior. Full optimizer/scheduler/RNG crash resume
is not supported. Rank 8 → 16 → 32 remains a staged operator decision, not a sweep.

An evaluation specification binds the prepared manifest, held-out excerpt IDs,
exact training request/receipt, adapter identity and generation settings. Each
base/adapter arm is submitted explicitly to the existing local music owner using
the same prompt, seed, variant and duration. A repeated submission reuses its child
job. Recovery reconnects an exact accepted child; ambiguous ownership stays
unverified and never starts a replacement automatically. Explicit collection
verifies and independently retains comparison audio. Verified partial copies can
be reused after interruption, and later source deletion does not remove collected
comparisons. Publisher declarations are recorded separately; ordinary generation
runtime identity remains unverified.

Listening reviews bind the specification and both exact collected output hashes.
They record the reviewer's declared scope, separate dimensions, preference and
rejection notes. Public reviewer attribution requires consent. The identified form
keeps the arm names visible. A separate local action stores one random A/B
assignment per comparison and reviewer reference before playback, reuses it on
refresh, and refuses it if either retained output hash changes. The session and
clip URLs use labels A and B. Saving sends the hashes of the clips that were
presented; the response then reveals which arm was A, which was B, and the
decoded preference. Opening the labeled view after that assignment is shown ends
the presentation in the UI. The same reviewer reference can exist on both the
identified review and the unlabeled review. A saved rating, a completed job, or
an unlabeled review does not change `quality_status="unverified"` or approve
distribution. This is a local presentation control. It does not recruit a
listener, supervise a panel, or stop someone with database access from reading
the stored assignment.

A source-bound model card can be read for a saved comparison. It repeats the
manifest counts, adapter identity, training settings, measured runtime when one
was stored, collected output hashes, public attributions, and hashes of sources,
rights evidence and prepared excerpts. It omits private reviewer references,
rights-document references, reviewer names, captions, filenames and rating
scores. Its distribution status stays `not_approved` even when the saved split
marks adapter distribution as permitted. Changing a retained output makes the
card refuse rather than repeat the old hash.

No FAD/CLAP/instrument-classifier models are installed or run by these checks.
Those metrics and cultural authenticity remain unavailable. These checks trained
no regional adapter and recruited no musician panel; no improvement, commercial
clearance or release readiness is claimed. A supervised blinded panel, copying
the adapter into an export folder, and release approval remain separate work.
Existing local adapter downloads do not acquire dataset distribution permission.
