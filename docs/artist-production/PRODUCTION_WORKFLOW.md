# Artist production workflow

Implementation reviewed on **2026-10-14**, in the working tree based on
`dee6721566f320260d1efc642cbc458167830575`. The maintainer subsequently requested
a local commit and isolated live testing; this is not a published release.
The workflow supplies local evidence records and a strict human-guide conversion
path. **Commercial release is currently blocked:** the renderer cannot attest a
complete runtime dependency inventory. No real Gladys recording, consent document,
GPU render, trained singer model or commercial singing-synthesis result was
verified during this work.

The app has no built-in authentication. Names, confirmations and evidence references
are declarations by an unauthenticated local operator. File hashes identify observed
bytes; they do not authenticate a person, establish copyright or sign an agreement.
Keep the app on its default loopback access boundary. See the
[license evidence](LICENSE_AND_MODEL_EVIDENCE.md),
[security and retention review](SECURITY_AND_RETENTION.md) and
[acceptance report](ACCEPTANCE_REPORT.md) for the limits of the evidence.

## 1. Create the artist and record permission

In the singing workspace, create an artist profile associated with the selected
voice. Append a permission version with an evidence reference, operator name and
human declaration. Active permission requires explicit operations (`train`,
`convert`, `export`) and uses (`evaluation`, `production`, `commercial`); record
territories and effective dates where applicable. An optional evidence SHA-256
records a supplied digest. The app does not fetch, authenticate or legally interpret
the referenced signed document.

Permission revisions are preserved. Registered-artist preparation, builds,
comparisons, conversion and exports check the current applicable scope on the
backend. Long-running work rechecks authorization before publishing results.
Revocation appends a new version and drains owned voice work. A checkpoint or an
old browser selection does not restore revoked permission.

Record named authorized operators when the signed agreement restricts them. The app
has no authenticated actor, so a nonempty `authorized_operators` restriction blocks
new use with `artist_operator_authentication_required`. The operator declaration
label does not satisfy that identity requirement. Leave the list empty only if the
agreement permits the current local operators; do not remove a genuine restriction
to enable an action. OS access controls remain necessary.

Territory-restricted permission fails closed when an operation supplies no target
territory. The current rendering/export workflow has no destination-territory
selector, so recording such a restriction does not make that operation eligible.
Do not remove a genuine restriction merely to enable a button.

Legacy voices without an artist binding retain compatibility paths. Their historical
outputs are unreviewed, and they cannot acquire strict production approval merely
by attaching an artist name later. This is not a retrospective audit of the library.
The implementation is in [artists.py](../../backend/app/artists.py) and
[voice_authorization.py](../../backend/app/voice_authorization.py).

## 2. Capture dry recordings and inspect their actual properties

The capture plan is a **45-minute heuristic**, split into 10 minutes of comfortable
singing, 10 of pitch/vowels, 8 of dynamics, 7 of Spanish, 7 of English and 3 of
expression. Start with 15–30 useful minutes and review the material before adding
more. The existing preparation selection ceiling is 60 minutes. These numbers are
capture guidance, not validated requirements for this singer or a quality guarantee.
The complete typed script is available from `GET /api/artists/recording-script`;
the artist panel presents its guidance summary.

Prefer dry mono 48 kHz/24-bit WAV recordings with a consistent microphone position.
Preserve original files. The QC panel records their original SHA-256, actual format,
sample rate, channels, subtype, duration and size, plus labels for language,
register, articulation and expression. Inspecting a source does not resample or
rewrite it; later preprocessing cannot recover missing recording detail.

Registered uploads retain QC-committed originals even when cancellation, revocation
or a catalog write interrupts acceptance. Each accepted file is indexed separately.
A later successful upload reconciles byte-verified QC originals missing from the
voice catalog; changed or missing files remain unresolved. Duplicate detection is
based on hashes, not filenames or restored modification times.

The displayed peak, full-scale sample fraction, RMS, near-silence fraction and
quietest-decile noise-floor **proxy** are amplitude screening measurements. They
do not establish isolation, room noise, singer identity, musical accuracy or training
quality. Listen to the original and dry samples, review segment selections and
obtain singer confirmation. See
[recording_sessions.py](../../backend/app/recording_sessions.py) and the existing
[voice-quality workflow](../voice-quality.md).

## 3. Keep held-out evaluation separate

Upload held-out passages through the existing comparison workflow; they are stored
under the voice's trial sources rather than its training recordings. The QC panel
lists training and held-out sessions separately. Held-out material does not count
toward the training selection target.

The backend rejects reuse of the same observed source bytes for training and held-out
purposes within a voice, including renamed or moved files and existing trial sources
without session metadata. This is exact-byte protection, not semantic duplicate
detection: a re-encode or another take still requires a deliberate human split.

Use held-out passages to compare a reviewed, pinned model with its baseline and
record listening decisions. No real singer comparison was run here. Identity,
Spanish/English articulation, register transitions, timing, intelligibility and
musical usefulness remain listening gates, not consequences of a successful job.

## 4. Confirm range before rendering

Save a versioned comfortable MIDI-note range and, if known, complete extended bounds.
Record the operator declaration and the artist's confirmation. Observed pYIN
coverage never overwrites the confirmed range.

Choose the range policy explicitly: `warn`, `block` or `needs_approval`. The guide
analysis retains original-time observed windows. It is approximate pitch occupancy,
not an exact note transcription. Unknown analysis stays visible. A review approval
binds the exact observed assessment and range revision; it cannot override `block`
or turn unknown analysis into measured coverage. Updating the profile invalidates
an older render's range binding.

The chosen-key field is a label. **It does not transpose audio.** To change key,
prepare an appropriate guide and accompaniment outside this field and inspect them
again. See [singer_ranges.py](../../backend/app/singer_ranges.py) and
[guide_range.py](../../backend/app/guide_range.py).

## 5. Supply independently reviewed sources

Upload an isolated human-recorded guide vocal and an independent accompaniment.
Record each source's origin, provider, rights holder, acquisition method, evidence,
terms and permitted/blocked uses. Append a separate rights review. Sources with
unknown or restricted reviews remain ineligible for strict rendering.

Reviews bind the displayed declaration revision. An evidence amendment appends
history, resets its review to unknown and rejects a delayed approval for the old
revision. Origin, provider, role and original filename cannot be amended. Identical
guide/accompaniment bytes cannot pass as independent inputs. Re-uploading a missing
source creates a new unreviewed identity; historical references are preserved.

Known AI-generated ancestry, including known generated-provider/hash evidence,
cannot be relabeled as human recording. Unknown and AI sources remain reference-only
for the strict guide path. There is no automatic classifier that can discover every
incorrect declaration on an arbitrary external upload. Honest source declarations
and human review are still necessary.

An evidence amendment creates a new declaration revision, preserves the source's
origin/provider/byte identity and requires fresh rights review. It does not rewrite
history. The source graph checks ancestor files, hashes, parent links and cycles;
a changed or missing source blocks strict use. See
[artist_production.py](../../backend/app/artist_production.py).

## 6. Render the guide with an exact model selection

Choose a published trained model with a checkpoint, configuration and reference;
reference-only fallback models cannot satisfy the strict pinned-checkpoint gate.
Confirm that the guide is isolated and its singer is the intended recorded performer.
Select its reviewed accompaniment and submit the render.

The backend snapshots the selected model revision, checkpoint/configuration/reference
hashes, engine source hash and observed Git revision, settings and available build
provenance. Installed auxiliary candidates are recorded as observations. Their
presence does not prove which files the engine loaded or establish every transitive
dependency. Bitwise deterministic reproduction is not verified.

This path skips song separation: it converts the isolated guide through the existing
Seed-VC worker, retains the converted vocal, then combines it with the independent
accompaniment using the existing level matching and limiter. The guide and backing
must have compatible durations. Source, permission, range and pinned-model changes
are rechecked before inference and publication. Strict worker launches set the
Hugging Face/Transformers offline flags; a missing required cache can fail the run.
This is not a guarantee that every optional workflow in the app is offline.

Completion persists a run, source graph, output asset and exact immutable audio
version independently of the browser. Reloading the workspace can recover it.
Cancellation and restart recovery use the owned job registry and retain terminal
status/evidence. The synthetic pilot exercised real CPU FFmpeg/pYIN processing with
a **mocked voice converter**. It is not a singer-quality or GPU benchmark. See
[production_render.py](../../backend/app/production_render.py).

## 7. Review exports and the commercial-release gate

A completed run can have technically valid lineage while lacking commercial
permission. A production export revalidates the exact audio version's lineage and
applicable artist permission and carries its production snapshot in export
provenance. Legacy/non-strict versions remain separately unreviewed.

New export creation/reuse, tagged downloads and raw export/manifest retrieval check
live artist permission and retention freeze. Revocation preserves historical
files/records while denying new retrieval/use. Historical source playback in other
existing library views is not an authenticated artist-access service.

Adjacent shareable manifests retain artifact/graph/model hashes, IDs and review
states while replacing free-text evidence/model references with hashed aliases.
Complete declarations and evidence history remain local. `graph_sha256` covers
that complete local evidence; it cannot be recomputed from the redacted shared
snapshot alone. Shared export-record metadata has a consistent 8 MiB read/write
limit; oversized publication fails before overwriting the previous record.

Append human release reviews for composition, master, voice and dependency rights,
with an evidence reference. Commercial release additionally requires source release
permission, current commercial export scope and a complete runtime dependency
inventory. The current `VoiceModelSnapshot.dependency_inventory_complete` contract
is fixed to `false`; all current real strict renders therefore retain
`dependency_inventory_incomplete`. An operator selecting approved review fields
cannot remove that blocker.

Review [LICENSE_AND_MODEL_EVIDENCE.md](LICENSE_AND_MODEL_EVIDENCE.md) for exact
component evidence. YuE2, SheetSage2 and MuScriptor have published noncommercial
restrictions. Commercial-compatible code/model declarations elsewhere do not clear
the singer, recordings, composition, fine-tune or complete renderer. DiffSinger-like
and personal-artist ACE-Step synthesis remain disabled-by-default research proposals;
this implementation adds no synthesis integration or demonstrated singer quality.

## 8. Trace usage and account for declared distributor revenue

Model-use evidence is append-only and hash-chained, with artist/permission/run/input/
output identifiers where known. Registered legacy usage may lack an exact model
revision. `GET /api/artist-production/usage-ledger` exposes the typed local ledger;
there is no authenticated artist/accountant portal. A retained independent ledger
head can help detect inconsistent later records, but a local administrator able to
rewrite the catalog can recompute the hashes. The ledger creates no royalties or
revenue from jobs.

Strict export requests retain model/source context before encoding, including early
failure and cancellation. Recovery findings append idempotent correction events
that supersede the earlier completion where applicable; they do not rewrite it.

The royalties panel supports these explicit steps:

1. Select or create an immutable agreement version for the artist. Supply a decimal
   percentage from 0 to 100, `gross` or `contract_net` basis, currency, permitted
   deduction categories, eligibility dates and evidence reference/version. There
   is no default royalty rate or inferred contract.
2. Obtain an eligible release record through the API. This requires the exact approved
   run/audio version and master hash, declared platform/master ID, release date,
   agreement and eligibility; ISRC/UPC are optional. **This is presently blocked for
   real current renders by the dependency inventory gate.**
3. Choose a UTF-8 comma-separated CSV, at most 1,000,000 bytes and 5,000 data rows,
   and enter actual header mappings for release ID, period/date, amount and currency.
   Source row ID and deduction category mappings are optional. Preview errors,
   duplicates and reconciliation totals before committing the exact preview hash.
   Editing the file or mapping requires a new preview. Commit revalidates within a
   transaction and is idempotent; invalid rows prevent partial import.
4. Review and download the statement JSON. It traces public release/import/row/
   adjustment/payment IDs and totals, omitting private evidence references,
   idempotency keys and raw reports. Review declared identifiers before sharing.
   Recording a payment declares an actual transaction made elsewhere; the app does
   not transfer money.

Decimal amounts travel as strings. Negative revenue entries are explicit refunds;
deductions must be negative and belong to agreed categories. Gross agreements
exclude deductions from their base; contract-net agreements include them. Currencies
must match the agreement; there is no exchange-rate conversion. A monthly period
must fit the eligibility interval; the app does not invent proration or contractual
rounding rules. Manual adjustments are direct signed royalty corrections, separate
from reported revenue and actual payments.

Release creation and manual adjustment entry are **API-only** in this version:
`POST /api/artist-accounting/releases` and
`POST /api/artist-accounting/adjustments`. Other accounting endpoints are listed in
[routes_artist_production.py](../../backend/app/api/routes_artist_production.py);
their request/response schemas are in
[royalty_contracts.py](../../backend/app/royalty_contracts.py). Accounting tests use
synthetic CSV and a mocked approved-release context, not a real cleared release or
distributor report. The calculations need agreement/accounting review before use
for actual settlement.

## 9. Review scoped deletion and preserve recoverability

The voice-delete action first creates a durable reviewed inventory. **Creating the
plan immediately freezes new voice use; closing the dialog does not withdraw it.**
Confirmation binds the exact voice and inventory hash. Execution drains owned work,
deletes eligible owned voice assets and records completed, retained, pending and
external items. Changed files require renewed review; symlinks, failures and
interruption remain actionable. Destructive work does not silently resume after
restart; retry explicitly.

Production-referenced artifacts, derived tracks/audio versions/exports and shared
model caches are retained. Logs, backups, browser downloads and external copies are
not proven erased. The receipt always reports `full_erasure_verified=false`; an
absent reviewed voice folder is not complete erasure of a singer. Receipts are
unsigned hash-chain records, and file deletion is not secure erasure. See
[voice_retention.py](../../backend/app/voice_retention.py) and the
[retention review](SECURITY_AND_RETENTION.md).

The inventory lists accessible conversion work files under `voices/_apply/` as
pending separate review and linked production source assets as retained/pending.
These live outside the reviewed individual voice folder. Every receipt predecessor
is validated. Histories have a 4,096-receipt limit; confirmation reserves a terminal
receipt slot before deletion. A capacity error preserves files and history for
technical review.

Before first startup with these changes, stop the app and drain all work. Preserve
the code revision, private configuration pointer and a consistent catalog backup,
then copy the corresponding library media/models and relevant external caches to a
private backup outside Git. Use SQLite's `Connection.backup()` for the WAL catalog
(as the existing library mover does), rather than copying only `aicollector.db`
while it is active. Validate the saved database with `PRAGMA integrity_check` and
`PRAGMA foreign_key_check`. Do not move the active library during jobs.

Startup adds tables, indexes and evidence constraints without reclassifying legacy
rows. Migrations are idempotent; their individual commits are not one global
transaction. Cold opens are serialized until migration finishes. A failed core or
component initialization closes its partial connection so the next open can retry.
Shared SQL statement caching is disabled to avoid reproduced concurrent-read
inconsistency on the test host's CPython runtime; this does not serialize every
legacy database transaction. No destructive down migration is provided.

For rollback, stop all application connections and preserve the upgraded catalog
and any later media/evidence first. Restore a matched reviewed database/media/config
backup if reverting the whole state is required. Reverting code while leaving new
tables unused may preserve data, but older code does not enforce the new consent or
deletion-freeze guards; that is not permission-safe continuation. Do not drop evidence
tables/triggers or delete later media to make a rollback appear clean. The acceptance
tests use temporary libraries; no user's production catalog was migrated as part of
this review.

## 10. Reproduce the fixture pilot and record real pilot evidence

The production regression suite creates non-sensitive deterministic tone WAVs in a
temporary library, runs real CPU FFmpeg and pYIN, and mocks Seed-VC inference. It
tests independent guide/backing linkage, rendering/publication/restart, missing or
changed artifacts, consent/range/model guards and immutable evidence. Re-run from
the repository root without using an artist's library:

```sh
task_tmp=$(mktemp -d /tmp/openfabric-artist-pilot.XXXXXX)
cd backend
OPENFABRIC_CONFIG="$task_tmp/config.json" OPENFABRIC_DATA_DIR="$task_tmp/data" \
SEED_VC_DIR="$task_tmp/engine" OPENFABRIC_MODULE_ROOT="$task_tmp/modules" \
OPENFABRIC_LOG_DIR="$task_tmp/logs" \
.venv/bin/python -m unittest app.artist_production_test -v
```

This verifies application behavior, not singing quality or inference speed. For a
consented real pilot, retain one row per variant with the following evidence:

| Evidence | Required record | Current real-singer result |
| --- | --- | --- |
| Environment | Device, OS, backend/engine commits, accelerator/backend, RAM and software versions | Unverified inference environment |
| Pinned identity | Artist/consent version, dataset and held-out fingerprints, voice revision, actual loaded checkpoint/config/vocoder/auxiliary hashes and permission evidence | No Gladys material or complete runtime inventory |
| Source chain | Guide/accompaniment IDs and hashes, confirmed range, source reviews, exact processing settings/seed where supported | Synthetic application fixtures only |
| Timing/resources | Model load, extraction if used, conversion, mix/export wall times, peak RAM/VRAM and input duration; processing ratio = processing seconds / audio seconds | Unmeasured |
| Output | Actual rendered/exported sample rate, channels/subtype, audio-version ID, output hash and export diagnostics | Synthetic fixture output only |
| Reliability | Failures, cancellation/interruption, recovery, model reuse/load conditions and rejected variants | Mocked model-boundary regressions only |
| Human review | At least four diverse held-out passages covering Spanish/English, soft verse/strong chorus, sustained notes/vibrato/consonants/transitions; singer and operator observations/scores | Pending authorized listening |
| Production effort | Human review/rework time, accepted/rejected count, total measured effort / accepted variants; retain failures in the denominator evidence | Unknown; no songs/day claim |

Keep objective measurements separate from listening decisions. The singer must
review identity, pronunciation, comfort and audible artifacts; numeric pitch/audio
scores alone cannot accept a model. Record reference-only and pinned-model A/B
settings without letting held-out passages become training data. A changed dataset,
model, dependency or processing configuration requires a new identified comparison.
