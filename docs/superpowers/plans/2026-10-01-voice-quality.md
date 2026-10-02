# Voice Quality Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development and meaningful regression tests. Preserve the existing dirty workspace; no commits or model downloads are authorized by this plan.

**Goal:** Make low-quality song inputs reviewable, prevent unsuitable material from silently reaching training, and let users compare references and trained checkpoints on held-out audio.

**Architecture:** Keep the 44.1 kHz F0-conditioned Seed-VC engine. Store source/time provenance, preparation revisions, model artifacts, and trial results under each voice; validate app boundaries with shared Pydantic contracts. Preparation, build publication, and comparison jobs own their subprocesses and remain independent of the browser. No unvalidated singer-identification claims or automatic quality promises.

**Tech Stack:** FastAPI/Pydantic, NumPy/SciPy/soundfile in the engine environment, Demucs fast/high presets, Seed-VC, Vue/TypeScript, generated client parsers.

## 1. Selection and DSP correctness

- [x] Reproduce noise fallback, aliasing, stereo cancellation, tonal overconfidence, cleanup damage, and high-F0 overflow with synthetic inputs before implementation.
- [x] Remove unconditional failed-segment retention; use filtered resampling and channel-aware analysis. Report measurements and rejection reasons, with source boundaries, without declaring singer identity or studio quality.
- [x] Add bounded, conservative cleanup with dry/cleaned auditioning. Preserve original samples.
- [x] Track reproducible vendor compatibility patches in the repository, apply idempotently, and reject unknown upstream source rather than guessing replacements. Fix high-F0 overflow, exact optimizer/counter resume, complete saved checkpoints, and deterministic inference seeds.

## 2. Reviewable preparation and training

- [x] Add typed preparation/selection routes and manifests, source exclusion, isolated-vocal bypass, and singer confirmation.
- [x] Prepare original-time segments, preview endpoints, ranked contiguous reference candidates, explicit sample/reference selection, and stale-revision rejection.
- [x] Support reference-only, 200/500/1000-step fresh builds and retained comparison checkpoints. Make resume explicit and require the same prepared data/settings. Preserve the prior usable voice until publication succeeds.
- [x] Expose available model choices and selected model; keep old voices usable and sanitize failures.

## 3. Separation and held-out listening trials

- [x] Test configurable `htdemucs`/`htdemucs_ft` commands and cancellation. Keep fast default and explain the high-mode cost; preserve all existing stem consumers.
- [x] Add separately uploaded held-out sources, independent persisted/cancellable comparison jobs, model/reference/30-or-50-step combinations with fixed seeds, measured output stats, and four distinct human ratings.
- [x] Bound requests and media, constrain paths, copy trial dependencies to keep rebuilds from changing an in-flight comparison, and drain jobs on voice deletion/shutdown.
- [x] Document a reproducible RoFormer comparison procedure and optional installed-engine adapter only if a validated compatible setup is available. Do not claim unrun GPU benchmarks or download checkpoints during unit tests.

## 4. Browser controls and integration

- [x] Generate shared contracts; add typed wrappers and preparation, selection, cleanup audition, model/build options, and held-out comparison controls using existing components.
- [x] Translate labels/errors, guard async work after navigation, and add meaningful mounted/component boundary tests.
- [x] Run isolated backend tests, frontend tests/strict build, desktop tests when startup is affected, contract drift and scoped strict Python checks; inspect final diff and report GPU/platform limits.

## Verification commands

```sh
OPENFABRIC_CONFIG=/tmp/openfabric-review-env.9vBA33/voice-config.json OPENFABRIC_DATA_DIR=/tmp/openfabric-review-env.9vBA33/voice-library /tmp/openfabric-review-env.9vBA33/venv/bin/python -m unittest discover -s app -p '*_test.py'
```

Run from `backend/`. Run `npm test` and `npm run build` from `frontend/`, `npm test` from `desktop/`, and `/tmp/openfabric-review-env.9vBA33/venv/bin/python backend/scripts/generate_contracts.py --check` from the repository root. Run the strict Python command in CI, adding new self-contained typed core modules as they pass.

## Verified implementation — 2026-10-01

- Full isolated backend suite: 205 tests passed.
- Full frontend suite: 121 tests passed; strict TypeScript policy, vue-tsc, and production build passed.
- Desktop suite: 27 tests passed.
- Strict Python check: 18 scoped source modules passed; generated contract drift check passed.
- Setup shell syntax and final whitespace/diff checks passed.
- Additional integration fixes preserve queued comparison references across model/review changes, archive build provenance, identify baseline bytes with SHA256, invalidate changed sources, and drain preparation before deletion/shutdown.

Verification used synthetic WAVs, real CPU FFmpeg/subprocess checks, inspected vendor source, and mocked model/download boundaries. No GPU inference/training, network model download, representative-recording listening benchmark, or packaged platform validation was performed. RoFormer requires a separately installed compatible engine/configuration/checkpoint. Changes remain uncommitted.
