# LoRA training recovery and verification

> Historical record — HeartMuLa retired on 16 October 2026. Its generation, training and installation instructions below no longer describe supported functionality. See [current music documentation](../../local-music.md).

User authorization: fix the failed ACE preprocessing and verify the other music models' LoRA training flows. Preserve existing datasets, training runs, adapters, and uncommitted work. Do not commit or push.

1. Reproduce the failed MP3 decode and misleading attempted-sample progress. Add regression tests before changing behavior.
2. Reuse ACE's existing audio reader in both training data paths. Verify actual CPU decoding during setup and invalidate stale verification when the decoder changes.
3. Restore relocated HeartMuLa/YuE training environments only after validating copied sources and packages; preserve previous files and receipts.
4. Apply the maintained ACE patch to the idle owned engine. Run isolated, bounded training and adapter-loading smoke tests for ACE, Stable Audio Small/Medium, HeartMuLa, and YuE acoustic LoRA. No downloads or long training runs.
5. Retry the user's failed dataset preprocessing, check the browser state, and run the required regression/type/build/contract checks. Record exact evidence and remaining limitations; leave the app available for testing.

Live evidence belongs outside Git under `OpenFabricStudioData/live/recovery/`. Synthetic runs prove plumbing and checkpoint compatibility, not musical quality or convergence. Full training-state continuation and NVIDIA execution are outside this verification.

## Completed verification

- Reproduced and fixed ACE's TorchCodec decode failure by reusing its existing audio reader. Setup now decodes real CPU audio before recording verification and rejects an editable checkout pointing elsewhere.
- A real ACE smoke test exposed invalid MPS fp16 gradients and zero-step false success. MPS training now uses float32; non-finite gradients and zero-update epochs fail before publishing an adapter. Regression checks cover full and remainder updates, logging cadence, and both LoRA/LoKr loops.
- A generated preview exposed a second decoder issue: MLX used a separate cached base model and ignored loaded PyTorch adapters. Active adapters now select the existing PyTorch generator; base generation retains MLX.
- ACE XL, Stable Audio Small/Medium, HeartMuLa, and YuE acoustic training each completed a real optimizer step, saved finite nonzero adapter weights, and generated a preview with the adapter. Completed jobs and outputs recovered in fresh backend processes. ACE safe export refused an existing destination.
- Relocated HeartMuLa/YuE training environments were validated and repaired with backups; no model downloads were needed. Synthetic datasets were archived outside the user's dataset list.
- The original user MP3 produced one finite training tensor; saved dataset bytes remained unchanged. Browser verification showed `1 / 1 tensors ready` and enabled Start training. The export parent now follows the user's requested data folder.
- Checks: 1,677 frontend tests and strict build; 2,304 backend tests (16 skips), then 413 final focused LoRA/setup regressions; 21 strict mypy commands across Linux/macOS/Windows typing paths; contracts check; 87 desktop tests (one platform skip); full native patch applicability and final diff checks.

Remaining limits: no long training/convergence or listening evaluation; no NVIDIA hardware run. Full precision training and PyTorch adapter generation can cost more memory/time. Changes remain uncommitted on the existing topic branch.
