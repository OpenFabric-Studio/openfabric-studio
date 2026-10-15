# Mac-first music LoRA training Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Prepare and train usable model-specific adapters for Stable Audio 3, HeartMuLa and YuE2, on this Mac where verified, with portable NVIDIA fallback.

**Architecture:** Preserve the existing supervised Stable MLX trainer and module installer. Validate the two new trainer/runtime boundaries before exposing training; use backend-owned jobs and immutable per-run datasets. Each model is a separate stage with its own checks, retaining the existing engines and all prior work.

**Tech Stack:** Python/Pydantic/FastAPI/SQLite, Vue/TypeScript, official MLX Stable trainer, SimpleTuner/PyTorch HeartMuLa, headless YuE2 acoustic trainer.

---

The maintainer approved the staged design in `docs/superpowers/specs/2026-10-15-music-lora-training-paths-design.md`. Work stays in the existing dirty topic branch because earlier fixes are dependencies. No commit or push is authorized. No unit test uses the real library, model cache or network.

## Stage 1: Stable training assets

**Files:** `backend/app/module_music_models.py`, `backend/app/assets/music-models.json`, `backend/app/stable_lora_contracts.py`, `backend/app/stable_lora_runtime.py`, `backend/app/api/routes_stable_lora.py`, new focused training-preparation helper/tests; `frontend/src/api/musicLora.ts`, `frontend/src/views/music/MusicLoraPage.vue`, `frontend/src/locales/musicLora.ts`, generated contracts; related test files.

- [x] Add regression tests for independently selected Small/Medium training assets, valid-file reuse, partial downloads, hash failure, and existing-file preservation. Use temporary environments and fake artifact bytes.
- [x] Add reviewed pinned training bundles, including the shared conditioner and matching base/encoder. Inspect Medium metadata at the same publisher revision before adding it.
- [x] Expose bounded preparation request/status/job endpoints using existing module job ownership and admission. Preparation cannot reinstall or delete an existing engine and cannot run alongside active work. Polling reattaches after reload; cancellation drains workers.
- [x] Show a clear Prepare training action, actual download size, variant and terms, installed/missing status, preparation stages and stable errors. Keep advanced training settings collapsed.
- [x] Run focused backend and frontend tests, regenerate contracts, and build.
- [x] Run a short isolated real Small Music encode/train/load/preview test using synthetic audio; record finite loss and changed adapter tensors. Record quality as unevaluated.

## Stage 2: HeartMuLa compatibility and training

**Files:** new `backend/app/heart_lora_*.py` helpers/contracts/tests and `backend/scripts/heart_lora_worker.py` as justified by the compatibility result; existing `music_lora*.py`, `local_music*.py`, `resource_admission.py`, startup/shutdown integration, model tab/API/locales/tests.

- [x] Check out pinned trainer sources outside the repository, inspect dependencies, and run a tiny fixed-input base comparison for the Q/K rotary layout. Keep all installed engine weights unchanged.
- [x] Test the real official encoder and matching codec rather than an approximate token maker. Verify token shape, range and dataset provenance before training.
- [x] Prove a finite-loss MPS optimizer step with changed plain-LoRA tensors; inspect checkpoint save/load and inference parity. If the external trainer has an incompatibility, fix the narrow conversion boundary or choose a verified original-model trainer without hiding the failure.
- [x] Add strict adapter/config validation and numerical conversion regression tests. Reject unsupported modules, wrong bases, incomplete factors, nonfinite data, unsafe files and destinations.
- [x] Connect successful training to existing saved/library dataset selection, supervised persistent jobs, progress, cancellation, reload recovery, validated publication and generator loading. Add a portable NVIDIA package if Mac execution cannot be verified.
- [x] Run a real short train/load/generate check and save exact version/hardware evidence. Do not advertise full-state resume without a restore test.

## Stage 3: YuE2 acoustic training

**Files:** new `backend/app/yue_lora_*.py` helpers/contracts/tests and `backend/scripts/yue_lora_worker.py` as justified by validation; existing adapter registry/native generation and common job/UI integration.

- [x] Pin compatible ComfyUI source and trainer. Exercise compact conditioning, semantic tokens disabled, acoustic-head training disabled, ordinary AdamW and BF16 MPS.
- [x] Validate VAE encoding, cached style prefix, backward, changed adapter tensors and checkpoint loading in a short isolated run. This trains acoustic style; AR composition stays frozen.
- [x] Implement a strict NAR-only conversion test: split fused QKV B rows at 2048/1024/1024 and gate/up at 6144/6144, retain A, fold alpha/rank into B. Assert the converted matrix delta equals the original fused delta numerically.
- [x] Validate exact base identity, layer/projection dimensions, paired factors and supported metadata; reject AR and acoustic-head replacements in the first workflow.
- [x] Integrate owned jobs, model-specific preprocessing, preparation, dataset selection, portable NVIDIA package, validated registration and generator preview.
- [x] Run a real short train/load/preview test; keep any unproven platform or quantized compatibility explicitly experimental.

## Final verification and review

- [x] Review implementation against the approved spec, then review connected code for security, data preservation, type boundaries and cancellation ownership; independent review findings resolved, including cancellation telemetry, publication, archive cleanup, installed licenses and Heart generator integration.
- [x] Run `npm test` and `npm run build` from `frontend` (1,633 tests passed; strict build passed).
- [x] With temporary `OPENFABRIC_CONFIG`, `OPENFABRIC_DATA_DIR`, `SEED_VC_DIR`, `OPENFABRIC_MODULE_ROOT`, run backend `python -m unittest discover -s app -p '*_test.py' -v` (2,264 tests, OK; 15 optional/platform skips).
- [x] Run `python backend/scripts/generate_contracts.py --check` and the strict mypy CI loop for Linux, macOS and Windows (21 invocations passed; optional Heart worker checked against real Torch/pinned source).
- [x] Run desktop tests if startup, packaging or runtime layout changes (87 passed, one platform skip).
- [ ] Inspect the UI with the computer-use browser; save a screenshot and verify reconnect/progress/error states. **Unavailable:** browser inventory was empty; component tests cover behavior, but visual inspection is not claimed.
- [x] Record actual passes and remaining hardware/quality limitations in `docs/music-lora-training-proof.md` and `docs/heartmula-training-proof.md`. All changes remain uncommitted.
