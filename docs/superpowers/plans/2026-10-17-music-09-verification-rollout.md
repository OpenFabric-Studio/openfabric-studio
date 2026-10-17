# Music Workflow Verification and Rollout Implementation Plan

> **For agentic workers:** Use superpowers:verification-before-completion at execution time. These checklists and commands describe future verification; they are not passing test results. Commits, pushes, releases and external publication require the maintainer's explicit request.

**Goal:** Verify the complete music workflows, safe upgrades and target-hardware behavior before calling them supported.

**Architecture:** Test small domain units with temporary libraries and mocked model boundaries, then test real CPU media across APIs and editor output, then separately record bounded Mac model trials and listening evaluation. Each phase integrates only after its required checks pass; later phases do not excuse a broken earlier workflow.

**Tech Stack:** Existing Python unittest/mypy, generated Pydantic contracts, Vitest/Vue strict build, desktop node tests, FFmpeg CPU fixtures, browser/manual accessibility checks and explicit GPU/hardware evidence.

---

## Q01 — Freeze a representative fixture matrix

- [ ] Generate small deterministic fixtures in temporary directories: silence, impulse/click grid, pitched tones, antiphase stereo, asymmetric stereo, variable-tempo clicks, phrase/silence vocals, invalid media and truncated files.
- [ ] Add manually annotated rhythm/phrase fixtures where simple impulses cannot represent realistic ambiguity. Keep exact labels and source/permission evidence.
- [ ] Keep all GPU/model APIs mocked in unit tests. Tests must not download weights, rewrite a configured engine checkpoint link or access the live library.
- [ ] Define deterministic fixture seeds and independent expected quantities: frame positions, known frequency ratios, source hashes, expected source order and expected sample/stem counts.
- [ ] Compare parsed MIDI using an independent decoder; do not prove a broken encoder using only its own decoder.
- [ ] Version native-transform tolerances by declared renderer/build. Do not assert cross-platform bit identity without proof; preview/export identity means the same artifact bytes.
- [ ] Test old editor/queue/training/source documents as compatibility fixtures alongside every new schema/migration.

**Exit:** Domain tests have known expected behavior rather than snapshots mirroring arbitrary implementation output.

## Q02 — Isolation and dependency preparation

Run commands in a dedicated implementation checkout. The following environment is a future test setup, not an instruction to move the real library. Choose the interpreter already configured for the repository; the examples use `backend/.venv/bin/python`.

From the repository root in one shell:

```sh
export MUSIC_TEST_ROOT="$(mktemp -d /tmp/openfabric-music-tests.XXXXXX)"
export OPENFABRIC_CONFIG="$MUSIC_TEST_ROOT/config.json"
export OPENFABRIC_DATA_DIR="$MUSIC_TEST_ROOT/library"
export SEED_VC_DIR="$MUSIC_TEST_ROOT/seed-vc"
export OPENFABRIC_MODULE_ROOT="$MUSIC_TEST_ROOT/modules"
mkdir -p "$OPENFABRIC_DATA_DIR" "$SEED_VC_DIR" "$OPENFABRIC_MODULE_ROOT"
backend/.venv/bin/python -m pip install -r backend/requirements-test.txt
```

- [ ] Confirm these paths differ from the live configuration/library/engine roots before importing tests.
- [ ] Preserve this environment for frontend/API fixtures that start a backend. If a test overrides it, inspect the override and ensure it remains temporary.
- [ ] Install heavy analysis/renderer test dependencies in their reviewed optional environment only; the base import/runtime checks must work without them.
- [ ] Capture logs under the temporary test root. Redact secrets and sensitive source identifiers before sharing logs.
- [ ] Use the OS equivalent of a temporary directory/venv executable on Windows. Do not copy POSIX commands into PowerShell and claim native Windows validation.

## Q03 — Exact required repository checks

After shared contracts change, from the repository root:

```sh
backend/.venv/bin/python backend/scripts/generate_contracts.py
backend/.venv/bin/python backend/scripts/generate_contracts.py --check
```

From `backend/`, with the isolated environment still exported:

```sh
.venv/bin/python -m unittest discover -s app -p '*_test.py' -v
```

From `frontend/`:

```sh
npm test
npm run build
```

From `desktop/` when setup, startup, packaged files, environments or artifact layout change:

```sh
npm test
```

For strict Python checks, execute the actual groups in `.github/workflows/ci.yml` for Linux, macOS and Windows typing paths. The following root command reads the checked scope instead of copying a stale long list into a new script:

```sh
backend/.venv/bin/python - <<'PY'
from pathlib import Path
import shlex
import subprocess

root = Path.cwd()
python = root / 'backend/.venv/bin/python'
workflow = (root / '.github/workflows/ci.yml').read_text()
groups = [line.strip() for line in workflow.splitlines()
          if line.strip().startswith('python -m mypy --strict ')]
if len(groups) < 8:
    raise SystemExit('Review the strict typing groups; fewer than baseline expected')
for target in ('linux', 'darwin', 'win32'):
    for line in groups:
        arguments = [part.replace('$target', target)
                     for part in shlex.split(line)[1:]]
        subprocess.run([str(python), *arguments], cwd=root / 'backend', check=True)
PY
```

- [ ] Confirm new contracts/stores/ownership/DSP/worker/routes are included in CI's actual groups before using this command. Following imports silently is not proof that unchecked new files are strict.
- [ ] Add accurate optional/native dependency declarations based on inspected source when required. No `Any`, unchecked casts or suppressions to silence defects.
- [ ] Run each phase's focused tests during red/green development, then this full required set before integration. Repeat broad checks only when code changes/failures justify them.
- [ ] Run `git diff --check` and inspect the final diff, generated contract changes, schema migrations, dependency locks and notices.

**Expected outcomes:** Zero contract drift, strict frontend build, required behavioral tests passing, actual strict Python scope passing for all three typing paths, and desktop checks when affected. Record skipped tests and reasons; these commands have not run as part of planning.

## Q04 — Cross-workflow behavioral matrix

Every new job family must cover all relevant rows, with a small direct regression rather than one giant end-to-end mock.

| Scenario | Expected behavior |
| --- | --- |
| Browser reload / brief network loss | Reconnect to saved document/job; running backend work is not restarted |
| Navigate away while polling | Abort/invalidate old requests; no stale response changes another project/session |
| Duplicate submission | Same idempotency key/content reuses operation; changed content conflicts |
| Concurrent editors | Revision compare-and-swap returns conflict; no lost update |
| API restart during CPU phase | Completed verified artifacts retained; unfinished stage marked interrupted/retryable |
| Engine crash during training | Honest supported-resume state; no claim of full resume without optimizer/RNG/settings proof |
| Cancel before process launch | No stray child starts after cancellation |
| Cancel while decoding/inference/rendering | Descendants terminated and awaited; locks released only after verified exit |
| Cleanup cannot be verified | Workspace/device quarantined; conflicting job blocked, technical log retained |
| Source deleted/mutated mid-job | Protected snapshot remains valid or stable source-change error; no wrong-source publication |
| Sample/model changed after preview | Fingerprint mismatch; review/re-render required, no silent substitution |
| Low disk before work | Bounded rejection with required/free-space details and safe recovery |
| Disk full during save | Inputs/previous results intact; partial output not marked complete |
| Interrupted publication | Idempotent receipt reconciliation; no duplicate tracks/projects/adapters |
| Corrupted metadata/history | Preserve bytes, report scoped unreadable state; do not delete unrelated records |
| Destination already exists | Refuse export; never clear the selected folder |
| Unsupported task/model/mode | Backend capability error with setup/alternative action; never silently convert to a different task |
| Optional engine absent | Baseline workflow works; affected feature explains missing component |
| NC/unknown evidence | Visible restriction/unknown state propagates to derived artifacts |
| Old HeartMuLa records | Preserved history/media; no resurrected executable engine support |

## Q05 — Real CPU media and API-to-editor checks

- [ ] Run the API with an isolated copied/synthetic library and configured temporary engines; do not reuse the live API process for destructive fault injection.
- [ ] Create a beat through HTTP, edit a revision, render real WAV/stems/MIDI/JSON, reload, and create an editor project. Decode output and verify frames/onsets/pan/mix numerically.
- [ ] Prepare a vocal fixture with known phrase offsets, fit it, edit one anchor, preview and remix; verify the original source hash and former preview remain unchanged.
- [ ] Build a mashup from two click/tone/stem fixtures, preserve vocal section order and open the generated project. Verify baked timing is not stretched twice.
- [ ] Create a source-conditioned queue request against a mocked native server that inspects actual uploaded source bytes and task/options. A request-body snapshot alone does not prove reference audio reaches the engine.
- [ ] Compare displayed previews with exact downloaded artifact hashes and decoded duration. Confirm the selected export profile, provenance and player all reference the intended version.
- [ ] Test request/body limits before expensive parsing, path/symlink rejection, network-disabled inference and no raw internal error leakage.
- [ ] Verify library deletion guards and explicit cleanup report references and retained sizes accurately.

**Exit:** The complete HTTP → storage → worker → media → project/player path works on real CPU media.

## Q06 — Interface and accessibility review

- [ ] Review concrete layouts before building each major screen, using existing design tokens/components. Keep Music's queue home and existing generators accessible.
- [ ] Check file/library selections visibly confirm names/count/duration and partial import failures.
- [ ] Put active jobs above history: phase, completed units, elapsed time, last update, ETA only when measured, expandable details and clear cancel/retry actions.
- [ ] Add click-open info cards for musical/training parameters with defaults, effects and limits. Help remains keyboard accessible and does not require hovering.
- [ ] Exercise keyboard-only source selection, sequencer cell editing, anchor/placement editing, dialog escape/focus return, transport and export.
- [ ] Test screen-reader names/states, non-color-only status, reduced motion, long/localized titles, narrow desktop/mobile-width layouts and empty/error states.
- [ ] Confirm navigation/reload does not overwrite drafts, another run's selected adapter or a different project revision.
- [ ] Use the allowed browser/computer-use tools or documented manual checks; record screenshots and actual observed behavior, never screenshot-derived claims of GPU quality.

## Q07 — Bounded M4 Max model verification

- [ ] Record exact OS/device/runtime/available disk and idle state. Do not stop an unrelated user training/generation job to run these checks.
- [ ] Install only reviewed missing components after size/terms review, into the configured data/module root; do not duplicate working models merely to obtain a clean path.
- [ ] Verify `beat_this` offline inference with a pinned local checkpoint; measure CPU and any explicitly implemented accelerated path separately.
- [ ] Test Seed-VC and RVC conversion with authorized short vocal/backing fixtures and matching registered model identities.
- [ ] Test ACE base Complete and Lego with a short known beat. Verify source bytes, selected base, task and output publication; listen for beat retention and reanalyse timing. Turbo/SFT success is not proof of these tasks.
- [ ] Test each supported Stable variant/mode separately. If the pinned MLX runtime lacks a proposed conditioning path, record it as unavailable and use the plan 05 decision gate rather than fabricating support.
- [ ] Run short native training smoke/evaluation only with a temporary copied dataset and new output name. Confirm model-specific adapter load/generation afterward.
- [ ] Measure cold/warm wall time, phase time, peak process/unified memory, disk peak, scratch/retained bytes, model hashes and output hashes. Record the measurement method and sampling interval; sampled maxima are lower bounds.
- [ ] Increase duration/data/steps only after short checks pass and measured resource forecasts remain safe. No extrapolation from a two-step smoke to full-song quality or a universal training budget.
- [ ] Record actual Windows/NVIDIA evidence separately if tested. Cross-platform mypy or mocked workers cannot establish CUDA/native lifecycle support.

## Q08 — Listening and musical acceptance

- [ ] Keep evaluation recordings separate from all training/synthetic augmentation sources and document their rights.
- [ ] Voice: report onset alignment against manual references, measured LUFS/true peak, audible artifacts/identity and intelligibility separately.
- [ ] Mashup: report downbeat errors/order preservation and blinded preference/coherence against a naive overlay.
- [ ] Beat Maker: report grid onsets, sample/scale tuning, groove and preview/export identity; evaluate 10 presets × 5 seeds for the baseline where applicable.
- [ ] Song-from-beat: report BPM/phase/beat audibility over multiple takes. Do not label a checkbox 'keep unchanged' unless the exact claim has been established.
- [ ] Regional presets: use the 24 × 5 seed set, appropriate insiders and outside listeners, declared category and review status. Missing packs or unreviewed styles are not passing cases.
- [ ] LoRA: blind base/adapter comparisons using the same prompts/settings and independent holdout; publish negative/inconclusive results as such.
- [ ] The PDF's accuracy/preference/recognition numbers are proposed thresholds. Store sample sizes, failure cases and uncertainty; do not change thresholds after seeing results merely to obtain a pass.

## Q09 — Upgrade, rollback and support evidence

- [ ] Upgrade a copied pre-feature library with saved queues, training histories, dataset trash, originals/versions, adapters and editor projects. Compare IDs and file hashes before/after.
- [ ] Simulate interruption at each schema/publication boundary. New code must reopen a consistent state or return a supported-schema/recovery message without deleting unknown data.
- [ ] Test restore from the consistent backup made under plan 00. For additive schemas document whether old app code ignores or rejects new tables/fields; do not claim downgrade support without running it.
- [ ] Document configured paths, disk layout, retained intermediates, supported recovery, unsupported full-state resume and how to remove unreferenced app-owned artifacts safely.
- [ ] Update feature docs and ROADMAP implemented/experimental/unsupported labels based on evidence. Preserve upstream notices and experimental release labeling.
- [ ] Save an evidence table with columns: command/scenario, date/timezone, source commit, platform/device, result, skips/limitations and artifact/log location.

## Q10 — Integration and release controls

- [ ] Review phase diffs and cross-phase contracts before merging. Require appropriate independent review for storage, source ownership, cancellation, sample rights and model integrations.
- [ ] When the maintainer requests a commit/push, confirm target branch unless explicitly named in that request; check fork remotes, include intended files only and run required checks before integration.
- [ ] Do not commit model caches, private recordings/configuration, credentials, raw review contracts or large transient benchmark outputs.
- [ ] An optional draft prerelease follows `docs/fork-maintenance.md`; clean install, upgrade, first download, GPU path and notices require recorded platform evidence.
- [ ] Publishing installers, uploading model adapters or editing external repository metadata requires explicit scope/authorization for those artifacts. No automatic release/tag as a side effect of implementing a phase.
- [ ] Final implementation report states changes, reason, actual verification and remaining measured/unknown risks. Never convert this planning checklist into a claim that tests passed.

**Verification status:** This document describes future checks. The planning task validates document consistency and references only; no full application test, GPU run, migration or release is implied.
