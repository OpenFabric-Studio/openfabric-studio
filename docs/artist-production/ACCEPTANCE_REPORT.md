# Artist production acceptance report

Prepared **2026-10-14** for the implementation changes based on
`dee6721566f320260d1efc642cbc458167830575`. The baseline workspace was clean;
the maintainer subsequently requested a local commit and isolated live testing,
without a push or published release. This report distinguishes
observed test output, inspected implementation and unresolved production evidence.

**Current decision:** the local evidence/workflow implementation passed the final
automated checks below, including concurrent catalog read/startup hardening.
Personal-singer quality, real hardware performance and commercial release
are **not accepted**. No real Gladys audio or signed consent was examined, no real
GPU training/conversion or commercial synthesis ran, and no private recording,
dataset or distributor report was transferred to a cloud service.

## Recorded verification

The following results were read from the named local logs or reported by the owning
implementation agent. Temporary `/tmp` paths are reproducibility notes, not durable
checked-in artifacts. Passing an earlier working tree does not certify subsequent
changes. The baseline backend wrapper had a shell failure after unittest completed;
the result below records unittest output, not a clean enclosing wrapper exit.

| Check | Observed result | Evidence / applicability |
| --- | --- | --- |
| Baseline frontend `npm test` | 140 files, 1,256 tests passed | `/tmp/openfabric-artist-frontend-baseline.log`; 5.92 s. |
| Baseline frontend `npm run build` | Strict type policy, `vue-tsc` and Vite completed | `/tmp/openfabric-artist-build-baseline.log`; Vite 542 ms. |
| Baseline backend discovery | 1,539 tests, `OK (skipped=10)` | `/tmp/openfabric-artist-baseline.xnNdCg/backend.log`; 261.652 s; enclosing wrapper's read-only shell status-variable assignment failed afterward. |
| Earlier integrated frontend `npm test` | 146 files, 1,289 tests passed | `/tmp/openfabric-artist-frontend-complete.log`; 6.57 s; before latest hardening. |
| Earlier integrated frontend `npm run build` | Strict type policy, `vue-tsc` and Vite completed | `/tmp/openfabric-artist-build-complete.log`; Vite 532 ms; existing >500 kB chunk warning. |
| Earlier integrated backend discovery | 1,613 tests, `OK (skipped=10)` | `/tmp/openfabric-artist-final-backend.ZmY09H/backend.log`; 261.220 s; before latest hardening. |
| Earlier strict mypy platform loop | All nine checked scope/platform combinations passed | `/tmp/openfabric-artist-mypy-final.z1Cn4I/mypy-{linux,darwin,win32}-{0,1,2}.log`; scopes reported 16, 10 and 163 source files per platform; before latest hardening. This is checked-scope typing, not a whole-backend audit or native OS execution. |
| Earlier desktop `npm test` | 86 tests: 85 passed, 0 failed, 1 skipped | `/tmp/openfabric-artist-desktop.log`; 4.419 s. The skipped Windows legacy setup test leaves that platform behavior unverified here. |
| Earlier production fixture pilot | 9 tests passed | `/tmp/openfabric-artist-pilot-fixed.3qA0lI/pilot.log`; 3.794 s; before source-amendment/migration regression additions. Real CPU FFmpeg/pYIN, mocked converter. |
| Focused royalty backend checks | 14 synthetic tests passed; owned files passed strict mypy on Linux/macOS/Windows typing paths | Owning agent verification; temporary catalog/config/engine paths, mocked approved-release context. All 22 royalty model schemas were checked against the generator's supported schema fields. |
| Focused royalty frontend checks | 12 tests passed | Owning agent verification; mock APIs/synthetic CSV. Included stale-result/follow-up request prevention after teardown. |
| Concurrent upload failure reproduction | Failed, exit 139 | `/tmp/openfabric-artist-final-check.7sx410/backend.log`; upload error followed by SQLite close during a sibling worker query. Faulthandler confirmed that teardown crash in `/tmp/openfabric-upload-stress.XRcCB9/stress.log`. |
| Drained concurrency reproduction | Failed, exit 1 | `/tmp/openfabric-upload-drained-stress-long.IohwG5/stress.log`; iteration 27 returned `artist_consent_required` despite active consent, without the teardown crash. A focused four-thread authorization regression also failed in `/tmp/openfabric-authorization-concurrency-red.3X1kiP/test.log`. |
| Shared-cache fix stress | Passed, exit 0 | 500 consecutive concurrent-upload test iterations; `/tmp/openfabric-upload-fixed-stress.ltdj56/stress.log`. Application-contract stress, not an audio throughput benchmark. |
| Cold catalog / core retry regressions | Failed before fix; passed afterward | Cold worker received an incomplete catalog (`/tmp/openfabric-catalog-cold-red.KCTzjk/test.log`); simulated core disk-full initialization left a partial connection (`/tmp/openfabric-catalog-retry-red.39jeXc/test.log`). Both fixes included in 32 passing consent/upload tests, 0.560 s, exit 0; `/tmp/openfabric-catalog-retry-green.QTC5ep/test.log`. |

### Final integrated rerun status

These fresh runs cover the final implementation, including revision-bound approvals,
named-operator restrictions, export/receipt capacity, retained upload originals,
failed-version origin recognition and concurrent catalog correctness.
They do not establish real singer quality, weight permission or GPU throughput.

| Required final check | Status | Final evidence |
| --- | --- | --- |
| Backend `python -m unittest discover -s app -p '*_test.py' -v` | **Passed, 10 skipped** | 1,649 tests, `OK (skipped=10)`, 269.775 s, exit 0; `/tmp/openfabric-artist-final-verified.gj0sua/backend.log`. Includes cache/cold-open/core retry regressions. |
| Frontend `npm test` | **Passed** | 149 files, 1,310 tests, 5.81 s, exit 0; `/tmp/openfabric-artist-final-check-frontend.log`. Includes actionable retention-capacity error. |
| Frontend `npm run build` | **Passed** | Strict type policy, `vue-tsc` and Vite (560 ms), exit 0; `/tmp/openfabric-artist-final-check-build.log`. No final chunk-size warning. |
| Contracts `python backend/scripts/generate_contracts.py --check` | **Passed** | Exit 0 after regeneration; generated file has no manual edits. |
| CI strict mypy checked scopes for `linux`, `darwin`, `win32` | **Passed** | All nine combinations, scopes 17/10/163 files; `$TMPDIR/openfabric-artist-final-verified-mypy-qmtm15ru/mypy-{linux,darwin,win32}-{0,1,2}.log`. These are typing paths, not native execution. |
| Desktop `npm test` | **Passed, 1 skipped** | 86 tests: 85 passed, 0 failed, 1 Windows-only skip; 4.386 s, exit 0; `/tmp/openfabric-artist-final-verified-desktop.log`. |
| Final diff / generated types / migration and export boundary review | **Reviewed** | Root diff review plus independent audit and regression fixes; `git diff --check` exit 0. No commit, deployment, production migration or private-data deletion. |

Backend checks must use temporary `OPENFABRIC_CONFIG`, `OPENFABRIC_DATA_DIR`,
`SEED_VC_DIR` and `OPENFABRIC_MODULE_ROOT` roots. Model boundaries are mocked;
unit tests must neither read private singer data nor download weights. On this host
the available interpreter is `backend/.venv/bin/python`. Required commands and mypy
scopes are defined by [AGENTS.md](../../AGENTS.md) and
[CI](../../.github/workflows/ci.yml), rather than substituted syntax-only checks.

Final host observations: macOS 27.0 build 26A428, arm64 `Mac16,5`, Apple M4 Max,
128 GiB RAM; Python 3.12.13, Node 24.14.1, FFmpeg 9.0.1. These identify the test host,
not a measured inference benchmark or validation of other platforms. The ten backend
skips are four optional installed-vendor video tests and six native Windows Job
Object/tool-job tests. Windows native execution remains unverified.

Exact final invocation pattern (from the repository root; each backend run creates
its own temporary configuration/library/engine/module/log roots):

```sh
backend/.venv/bin/python -m pip install -r backend/requirements-test.txt
task_tmp=$(mktemp -d /tmp/openfabric-artist-check.XXXXXX)
cd backend
OPENFABRIC_CONFIG="$task_tmp/config.json" OPENFABRIC_DATA_DIR="$task_tmp/data" \
SEED_VC_DIR="$task_tmp/engine" OPENFABRIC_MODULE_ROOT="$task_tmp/modules" \
OPENFABRIC_LOG_DIR="$task_tmp/logs" \
.venv/bin/python -m unittest discover -s app -p '*_test.py' -v
cd ..
backend/.venv/bin/python backend/scripts/generate_contracts.py --check
cd frontend
npm test
npm run build
cd ../desktop
npm test
cd ..
git diff --check
```

The requirements installation ran at baseline using the equivalent command from
`backend/`. The mypy run parsed the three literal `python -m mypy --strict` command
lines in CI, replaced the interpreter with `backend/.venv/bin/python`, substituted
each of `linux`, `darwin`, `win32` for `--platform`, and ran every command from
`backend/`. All nine exited 0; no checks or platform paths were suppressed.

An isolated real HTTP smoke check used Vite at `127.0.0.1:3890` proxying a temporary
FastAPI library at `127.0.0.1:3891`. Frontend, voice creation, profile creation and
`needs_review` consent returned 200; preparation returned the expected 403
`artist_consent_needs_review`. SQLite contained the matching profile and revision.
Both owned servers were stopped. No engine/model work ran. Manual browser/viewport
inspection could not run: no browser connector was enabled and native Chrome
returned `cgWindowNotFound`. Component behavior/strict builds passed; visual mobile,
tablet and desktop acceptance remains unverified.

The audit reproduced and fixed stale source approvals, missing raw-export permission
checks, incomplete predecessor receipt verification, identical audio used as two
"independent" inputs, missing recovery correction events, early export ledger context,
write/read capacity mismatch, failed-version relabeling and receipt-capacity safety.
Negative tests first failed, then passed. Earlier
fixture failures (missing fake model artifacts, temporary path aliasing and the
shell status wrapper) were corrected. The final backend suite passed after catalog
concurrency hardening; no unresolved automated failures remain in these checks.

Concurrent shared-connection reads on the installed CPython 3.12.13 runtime returned
missing artist/consent rows despite unchanged persisted evidence. The observed
failure matches the maintainer-confirmed statement-cache problem in
[CPython #118172](https://github.com/python/cpython/issues/118172). Disabling caching
on the shared connection passed the red/green authorization regression and 500
concurrent-upload iterations. The test also drains both uploads before closing its
catalog, separating the underlying read defect from a teardown-induced segfault.
Cold initialization now waits for migrations, and failed core/component startup
discards the partial connection. These fixes do not serialize every existing
legacy transaction; extra SQL parsing cost remains unmeasured.

## Evidence gates

This table describes inspected behavior and regression coverage. Its implementation
entries are covered by the final integrated checks above. Human evidence and
hardware gates cannot be closed by those checks.

| Gate | Implemented evidence / regression coverage | Acceptance limit |
| --- | --- | --- |
| Artist and consent revisions | Typed profile/voice binding; active/revoked scoped permission revisions; backend registered-artist checks and awaited publication rechecks; revocation drains owned work. [artists tests](../../backend/app/artists_test.py), [workflow tests](../../backend/app/voice_workflow_test.py). | Local operator is unauthenticated. No actual singer agreement was validated. Unbound legacy paths remain compatible and unreviewed. Territory-restricted grants fail closed when no target territory is supplied; current render/export UI has no destination-territory selector. |
| Recording script and QC | 45-minute guidance; original hashes/actual format and labels; bounded amplitude diagnostics; immutable session records. [recording tests](../../backend/app/recording_sessions_test.py). | Capture targets are heuristics. Noise-floor proxy is not measured isolation or a quality score. No real singer material was screened. |
| Held-out separation | Same-byte renamed/moved training/held-out conflicts rejected; existing trial sources protected; held-out audio does not enter training records. [recording tests](../../backend/app/recording_sessions_test.py). | Exact-byte protection does not detect re-encoded copies or semantic overlap. Real held-out singer listening is pending. |
| Confirmed range | Immutable artist-confirmed bounds; warn/block/review policies; original-time pYIN windows; exact assessment/profile approvals; no silent transposition. [range tests](../../backend/app/singer_ranges_test.py). | Pitch occupancy is approximate. Unknown remains unknown; chosen key is a label. No Gladys range was established. |
| Source lineage | Immutable identity/parent graph; source evidence amendments require new review; known generated identity cannot be relabeled human; changed/missing files, cycles and symlinks rejected. [production tests](../../backend/app/artist_production_test.py). | Human declarations are not automatic origin/copyright verification. Arbitrary mislabeled external audio is not detected by a classifier. |
| Pinned guide conversion | Exact trained checkpoint/config/reference snapshot; source/model/consent/range rechecks; guide bypasses separation; converted vocal and mixed master retain graph/audio-version binding. [production renderer](../../backend/app/production_render.py), [fixture tests](../../backend/app/artist_production_test.py). | The successful fixture mocked Seed-VC conversion. Runtime loaded auxiliary identity and complete transitive inventory remain unattested; bitwise determinism is unverified. |
| Job cancellation/recovery | Existing owned worker lifecycle extended for guide analysis/rendering; publication rechecks and terminal usage evidence; interrupted jobs remain recoverable. [job tests](../../backend/app/job_lifecycle_test.py), [owned-thread tests](../../backend/app/owned_thread_test.py), [recording upload tests](../../backend/app/recording_upload_test.py). | Final temporary-library regressions passed. No hardware inference shutdown benchmark. QC-orphan catalog reconciliation requires a subsequent successful registered upload. |
| Export/release boundary | Exact audio-version lineage revalidated; production provenance added to manifests; separate composition/master/voice/dependency human reviews; strict release gate rejects incomplete dependencies. [export tests](../../backend/app/export_provenance_test.py), [production tests](../../backend/app/artist_production_test.py). | **Commercial release blocked.** `dependency_inventory_complete` is fixed false; selecting approved review fields cannot clear it. No complete commercial artist chain is approved. |
| Component terms | Primary source/model declarations and observable installation hashes dated 2026-10-14; native noncommercial model terms recorded in catalog. [license evidence](LICENSE_AND_MODEL_EVIDENCE.md). | Observed hashes establish bytes, not authenticated publishing, training-data permission, singer consent or a full dependency/SBOM clearance. Missing/unidentified dependencies remain unknown. |
| Scoped retention | Durable freeze; hash-bound reviewed inventory; owned-worker drain; protected production references/shared caches/derived outputs; pending failures/interruption; fully verified unsigned receipt chain with reserved terminal capacity. [retention tests](../../backend/app/voice_retention_test.py). | `full_erasure_verified=false`. Logs, backups, external copies, browser downloads and secure erasure are not verified. Closing the dialog does not withdraw a plan. |
| Usage evidence | Append-only idempotent hash-chain events and persisted run/input/output/consent IDs where known. [production tests](../../backend/app/artist_production_test.py). | No authenticated actor or cryptographic signature. A catalog administrator can recompute chains; registered legacy model revision can be unknown. No job-derived revenue. |
| Configurable royalties | Required explicit agreement percentage/basis/currency/dates/evidence version; exact release binding; bounded mapped CSV preview and transactional idempotent commit; Decimal refunds/deductions/totals; sanitized statements and separate declared payments. [royalty tests](../../backend/app/royalty_accounting_test.py). | Synthetic CSV and a mocked approved-release context only. No real eligible release, distributor report, money transfer, contractual rounding or exchange conversion verified. Release/adjustment entry remains API-only. |
| Frontend contracts and lifecycle | Generated decoders at unknown JSON boundaries; accessible labels/statuses; artist/QC/range/royalty panels; cancellation/generation controls suppress stale updates. [artist UI tests](../../frontend/src/views/voice/ArtistProductionPanel.test.ts), [royalty UI tests](../../frontend/src/views/voice/RoyaltyStatementsPanel.test.ts). | Passing component tests/build does not establish a real singer's usability, legal approval or a manual full browser/GPU production session. |
| Additive storage | New evidence/accounting tables and constraints; serialized cold opens/idempotent startup migrations; partial connection discarded after core/component failure; concurrent consent-read and lineage relocation regressions. [database](../../backend/app/db.py), [consent tests](../../backend/app/artists_test.py), [production tests](../../backend/app/artist_production_test.py). | Temporary catalogs only; no real production migration was run. Component commits are not one global transaction; no destructive down migration is supplied. Shared-query parsing overhead is unmeasured; legacy transactions are not comprehensively serialized. |
| Singing synthesis research | DiffSinger/OpenVPI and ACE-Step source/weight evidence separated from conversion and existing generic music generation. [license evidence](LICENSE_AND_MODEL_EVIDENCE.md). | Evidence-only blocked research. No executable adapter or enable flag was added; no new synthesis weights, authorized dataset, singer model, commercial output or quality benchmark. |

## Recoverable migration and rollback

Before opening a real library with the changed app, stop jobs/application connections
and preserve the code revision, private configuration pointer and matching media/model
state. Make a consistent SQLite backup with `Connection.backup()` so committed WAL
contents are included; the repository's existing
[library mover](../../backend/app/data_root.py) uses this method. Do not copy only
the live database file and assume it represents the whole catalog. Keep sensitive
backups and external caches outside Git, and validate the backup with
`PRAGMA integrity_check` and `PRAGMA foreign_key_check`.

The migrations add tables/indexes/constraints and do not relabel historical assets.
They can be retried after an interrupted startup. Preserve the upgraded database
and all later media/evidence before a rollback. Reverting code alone leaves evidence
tables unused, but also removes the new consent/freeze enforcement; it is not a safe
permission-preserving operating mode. A complete state rollback requires a reviewed
matched database/media/config backup and explicit preservation of work created
since that backup. Do not drop evidence tables, rewrite receipts or delete later
media as an improvised down migration. No real library move/deletion was performed
for this acceptance exercise.

## Evidence still required for a real singer pilot

1. Review actual singer/source/composition/master permissions for the intended uses,
   dates, territories, revocation and retention terms. Verify the identity of the
   people granting them outside the unauthenticated app.
2. Capture and review an authorized dry dataset with a separate held-out set. Confirm
   the singer's actual range and selections. No private material is supplied by this
   report.
3. Run the exact pinned converter/training chain on the intended hardware with all
   approved, locally available dependencies. Record loaded weights, source/runtime
   identities, timings, resources, cancellation behavior and output hashes. Close
   the runtime inventory gap before commercial release can be eligible.
4. Have the singer assess held-out passages across registers, dynamics, expression,
   Spanish and English, with intelligibility/timing/identity/artifact observations.
   Record failures and model/settings revisions. The CPU fixture cannot stand in
   for this listening evidence.
5. Obtain human legal/accounting review of release rights and agreement calculations,
   and reconcile actual distributor reports/payment records only after the release
   gate is met. Read-only team accounts require a separately implemented authenticated
   authorization system; they are not present here.

See [PRODUCTION_WORKFLOW.md](PRODUCTION_WORKFLOW.md) for the implemented operator
flow and its explicit API-only features. No commercial clearance, real-singer
quality claim or GPU/platform support claim follows from this report's earlier
successful automated checks.

## Local commit and live-testing handoff

The maintainer requested a local commit after the implementation review. Fresh
pre-commit frontend verification ran `npm test` (149 files / 1,310 passing tests,
5.81 s) and `npm run build` (strict policy, `vue-tsc`, Vite 560 ms), both exit 0.
Evidence: `/tmp/openfabric-artist-commit-frontend.log` and
`/tmp/openfabric-artist-commit-build.log`. Desktop `npm test` ran 86 tests, with 85
passing and one Windows-only skip, exit 0, 4.441 s;
`/tmp/openfabric-artist-commit-desktop.log`. All nine strict mypy scope/platform
combinations passed again; `$TMPDIR/openfabric-artist-commit-mypy-5uxtqnmo/`.
The fresh backend discovery ran 1,649 tests in 275.001 s, with
`OK (skipped=10)` and exit 0;
`/tmp/openfabric-artist-commit-check.8DVyRB/backend.log`. Contracts and staged diff
checks passed. Staging caught and removed one extra EOF blank line in a royalty
test; this whitespace correction changed no test behavior.

The built application is served by one Uvicorn worker at `127.0.0.1:9000`, with
new configuration, data, logs and engine/module roots under a separately created
`~/.openfabric-studio/artist-production-pilot/preview-…/` folder. No existing library
or model cache was copied or migrated. Keep that folder to preserve manual test
records. The preview starts with a synthetic artist/voice profile and `needs_review`
permission; no legal agreement or active artist grant was fabricated. Current
production engines/models are not configured in this isolated preview.

Real HTTP checks returned 200 for the singing route, voices, artists, recording
script and usage ledger. Profile/permission creation persisted in the test catalog;
preparation without permission returned 403 `artist_consent_needs_review`.
Native Chrome successfully opened the preview and its accessibility tree showed
the synthetic profile, permission warning, recording/range/accounting panels and
disabled clean-master action. This establishes desktop page loading and visible
guardrail text, not a complete browser production session, responsive visual QA,
GPU conversion or legal clearance. The loopback server is left running for the
maintainer's manual testing; no public service was exposed.
