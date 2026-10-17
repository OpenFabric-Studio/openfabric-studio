# Music Workflow Foundation and License Evidence Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans for execution; use focused subagents only for independent files under AGENTS.md. Steps use checkbox syntax. This document authorizes no commits, publishing, installs or production migration.

**Goal:** Establish safe storage, dependency evidence and explicit module choices for every workflow in the music report.

**Architecture:** Extend the existing module declarations, provenance, SQLite transactions and owned-worker patterns. Do not introduce another task framework, another library root or an automatic legal-clearance Boolean. Existing files, history and retired-engine records remain preserved.

**Tech Stack:** FastAPI, Pydantic, SQLite, Python standard library, Vue 3, generated TypeScript contracts, existing module setup and export manifests.

---

## Scope, evidence and dependencies

Planning baseline: `4609467881dc90af4c232b6eb67b6a1db7747782`; report v1.0, pages 4–8 and 29–34. Every task below is future work. Read the current checkout again before implementation.

Existing anchors:

- `backend/app/module_contracts.py`: `ModuleLicenseDeclaration`, `ModulePlanRequest` and installed-state contracts.
- `backend/app/module_licenses.py`: separate source/model/auxiliary/source-data declarations; YuE2 restrictions already recorded.
- `backend/app/module_security.py`, `module_jobs.py`, `module_install.py`: confined installs, receipts and recoverable setup.
- `backend/app/atomic_files.py`: document locks and atomic metadata replacement.
- `backend/app/export_provenance.py` and `export_provenance_contracts.py`: hash-bound manifests and redacted shared evidence.
- `backend/app/artist_production_contracts.py`: `ReviewStatus`, source declarations and human production/release gates.
- `backend/app/db.py`: SQLite library with WAL; use a consistent SQLite backup, not a live copy of the database file alone.
- `frontend/src/views/settings/ModuleLicenseCards.vue`, `ModuleSetupPanel.vue`: existing presentation.

Changes must reach: module declarations → setup plans and receipts → generated contracts → generation/training controls → queue snapshots → derived media manifests → copied-library migration tests. Source history is an input to this work, not disposable cleanup.

## Proposed contracts and data policy

These are proposed extensions, not existing APIs:

1. Keep existing `status: declared|unknown` compatible. Add bounded evidence metadata with an explicit observation kind, URL, exact revision/file hash where available, observed date and a reviewer label. Observing an upstream license file does not prove training rights.
2. Extend component scope to include `dataset` and `samples`. Record redistribution as `declared_allowed|bank_only|forbidden|unknown`; unknown is the default.
3. Record restriction as `noncommercial|conditional|unrestricted_declared|unknown`. Keep code, weights, encoders, sample banks and source recordings separate.
4. Retain the existing `approved|restricted|unknown` rights review. Do not equate a permissive code license with permission to release a recording. Do not replace the existing production gate with the report's simpler `commercial_eligible=true` example.
5. Record local acknowledgements against a terms/evidence digest and the intended operation. They are local operator declarations, not authenticated signatures. No personal evidence paths or private contracts in shared manifests.
6. New entry points default to permissive candidates. Expose known NC generation/transcription behind an explicit non-commercial setting and visible badges. Preserve historical outputs, saved settings, installed weights and accessible history when this setting is off.
7. Conditional models show their exact publisher terms and acceptance state. Never hard-code a revenue summary as the complete license agreement.

### Files

| Action | Paths |
| --- | --- |
| Modify | `backend/app/module_contracts.py`, `module_licenses.py`, `module_catalog.py`, `module_jobs.py`, `export_provenance_contracts.py`, `export_provenance.py` |
| Create, if acknowledgements cannot fit current settings safely | `backend/app/music_terms.py`, `backend/app/music_terms_contracts.py`, `backend/app/api/routes_music_terms.py` |
| Modify integration | `backend/app/main.py`, `backend/scripts/generate_contracts.py`, `.github/workflows/ci.yml` |
| Modify clients | `frontend/src/views/settings/ModuleLicenseCards.vue`, `ModuleSetupPanel.vue`, `frontend/src/api/modules.ts`, existing music generation/training/queue pages and their locale modules |
| Tests | `backend/app/module_contracts_test.py`, `module_catalog_test.py`, `module_jobs_test.py`, `backend/app/export_provenance_test.py` (new if absent), `backend/app/music_terms_test.py` (new), existing module/UI tests |

Do not hand-edit `frontend/src/api/generated.ts`; generate it from the changed schemas.

## L01 — Record the baseline and protect live data

- [ ] Read `git status`, current HEAD, remotes, active jobs and configured roots. Record them in the implementation evidence document; do not print credentials.
- [ ] Work on a focused topic branch from fork `main`. Preserve the untracked source PDF and any new user edits. Ask the target branch only when a commit/push is requested and not already named.
- [ ] Use a temporary copied library for schema work. No automated migration experiment against `/Users/seth/Documents/OpenFabricStudioData/live/library`.
- [ ] Before a production schema change, wait for active writers to finish or explicitly quiesce them; create a consistent SQLite backup with `sqlite3.Connection.backup`, copy configuration and inventory source media/checkpoint references outside Git.
- [ ] Test restoration of the copied database and config. Verify row counts, original hashes, adapter registrations, dataset names and existing queue history.
- [ ] Record that the report's `/workspace/openfabric/...` stale clone is not this workspace; no migration of that unrelated path is required.

**Exit:** A copied-library rollback succeeds before any new schema is applied to real data.

## L02 — Define evidence without overstating certainty

- [ ] Add failing schema tests: unknown declarations remain valid, an observed license requires a source reference, hash fields reject malformed values, collections/text are bounded, unknown redistribution cannot become allowed by omission.
- [ ] Add tests that code MIT plus unknown weights remains unknown for model use; known NC plus user-approved source still remains restricted for commercial production.
- [ ] Implement the compatible evidence fields and restricted-value enums.
- [ ] Record evidence for exact currently pinned music models and auxiliary encoders. Compare publisher files with current artifact identities; do not trust a filename, badge or report alone.
- [ ] Keep HeartMuLa retired. Add a regression asserting neither the new catalog nor optional-engine settings resurrect its setup, generation or training capability.
- [ ] Regenerate contracts and verify strict frontend decoding accepts old responses with compatible defaults while rejecting malformed new values.

**Exit:** Evidence describes what was observed; no API claims a successful install is a license audit.

## L03 — Local terms acceptance and NC choices

- [ ] Add tests for first use, repeated acknowledgement, changed terms digest, changed model identity, concurrent acknowledgement updates and an interrupted write.
- [ ] Store acknowledgements transactionally with a uniqueness constraint on operation/component/terms digest. If existing configuration can own the update atomically, use it instead of adding a redundant store.
- [ ] Bind setup review tokens to the selected component and terms digest. A changed plan must be reviewed again before download/install.
- [ ] Add a concise acceptance card linking the publisher agreement and showing affected code/weights separately.
- [ ] Add a non-commercial setting and badges on setup, YuE generation, training, reference transcription and queue previews. Server-side validation must enforce new submissions; a hidden button is insufficient.
- [ ] Keep existing finished outputs playable and exportable with truthful evidence. Do not hide or delete restricted history; show the restriction on reuse and release review.
- [ ] Reject a background/retried submission requiring a new acknowledgement with a stable `music_terms_required` code. Reconnect to an already accepted running job without retroactively cancelling it.
- [ ] Cover keyboard navigation, explicit labels, translations and stale async responses after navigation.

**Exit:** Users can understand and explicitly enable restricted experiments without losing their previous work.

## L04 — Propagate evidence to derived artifacts

- [ ] Add lineage tests for a mix containing two source tracks, a sample-rendered beat, a converted vocal and a model-conditioned song.
- [ ] Extend export evidence to carry component identities, exact sample/weight digests and restriction/review states. Preserve schema compatibility for old manifests.
- [ ] Propagate any known restriction through transformations. An unknown parent stays unknown unless supported by an explicit scoped review; processing cannot erase it.
- [ ] Keep full private evidence local. Shared JSON/text identifies sources by stable IDs/hashes and includes required public attributions, without private paths, personal names or credentials.
- [ ] Verify artifact hashing occurs after final encoding/tagging. Fail publication when bytes change between rendering, hashing and registration.
- [ ] Test interrupted manifest writes, duplicate publication, permission failures and old manifest reading.

**Exit:** The visible artifact and its manifest agree, and evidence survives mix/render/export.

## L05 — Disk forecasts and safe destination rules

- [ ] Add CPU fixture tests for pessimistic decoded size, multiple stems, insufficient free bytes, an output directory conflict and symlink escape.
- [ ] Calculate required PCM bytes from duration × sample rate × channels × sample width, then add simultaneous scratch/stem/output copies and a documented reserve. Measure actual peak disk usage during target-hardware trials to refine the multiplier.
- [ ] Display estimated output/scratch bytes, free space and retained files before costly renders or installs. State when a model's download size is unknown.
- [ ] Enforce the forecast on the backend immediately before work and publication. Return `insufficient_disk` before avoidable large writes; handle mid-job disk-full without deleting originals or marking partial output done.
- [ ] Use a newly created ID-owned staging directory for every destructive third-party helper. Never pass a user-selected export root into code that clears a directory.
- [ ] Exports use a folder picker plus a new named subfolder, preview the final path and refuse an existing destination. No replace-folder action in these workflows.
- [ ] Artifact/cache management lists sizes and dependencies. Explicit cleanup removes only selected unreferenced app-owned artifacts after confirmation. No automatic sweep of datasets, adapters, source tracks or model folders.
- [ ] Tests verify cancellation removes only verified stopped-worker partials; retain/quarantine workspaces when worker exit is unverified.

**Exit:** Disk exhaustion and cancellation preserve the user's inputs and previous completed results.

## L06 — Verification and integration

- [ ] Run focused backend contracts/catalog/setup/provenance/terms/storage tests in the isolated environment from plan 09.
- [ ] Run schema generation, strict types and affected frontend tests; run the complete frontend test/build commands before integration.
- [ ] Run the existing strict Python platform loop and include new evidence/terms modules in the actual checked scope.
- [ ] Run full backend regressions; desktop tests apply if setup paths, launch environment or packaged assets changed.
- [ ] Review the final diff for accidental terms claims, leaked paths, generated-file hand edits and unrequested external metadata changes.
- [ ] Save exact command results and limitations; leave commits/pushes to a separately authorized maintainer action.

## Report housekeeping dispositions

- The report's GitHub description correction is a separate external metadata edit: inspect current metadata first, prepare the exact change, and perform only with authorization covering that external change. Do not infer it is still wrong.
- Do not install every catalog candidate. Plan 08 records alternatives and validation gates.
- Do not vendor GPL/AGPL implementations merely because the report mentions them. Preserve existing third-party notices and review exact component obligations.

**Verification status:** Planning only. No tests, migrations, model installs or live data changes executed by this document.
