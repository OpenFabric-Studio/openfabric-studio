# Complete an existing YuE LoRA and prevent duplicate names

> Historical record — HeartMuLa retired on 16 October 2026. Its generation, training and installation instructions below no longer describe supported functionality. See [current music documentation](../../local-music.md).

The user approved adding “Train missing AR” to an existing completed YuE NAR
run, reusing its saved training source without retraining or replacing NAR.
They also requested preventing duplicate LoRA names.

## Behavior

- Eligible completed NAR history has a “Train missing AR” action. Review shows
  the saved name, source count, excerpt length, rank and seed, plus editable AR
  defaults of 100 steps and learning rate 0.00005. Starting creates a new owned
  job linked to the NAR run, not an optimizer continuation.
- The backend derives the request and verifies the saved request, dataset,
  exact private WAV excerpts and existing NAR adapter. Missing or changed source
  artifacts fail clearly. Original tracks are never silently substituted.
- The worker copies and verifies the saved excerpts, extracts scores, trains
  only AR, validates and converts its checkpoint, then registers AR alongside
  the existing NAR. Existing NAR bytes and identity remain unchanged. Preview
  selects the two matching adapters, while generation selectors stay separate.
- A source run may have at most one running or successful AR completion. Failed
  attempts without a retained completion receipt or registered AR may be retried;
  recovery publishes verified completed output without restarting training.
- New LoRA names are unique within each music engine after Unicode case folding
  and whitespace normalization. Different models may use the same name. A
  linked AR/NAR pair is one LoRA and keeps its original name. Existing duplicate
  history is preserved, and already running legacy jobs may finish normally.
- Enforce names on the backend for training and imports, serializing the name
  check and creation with the existing registry lock. Do not add a new service
  or database just for names. The UI reports a specific conflict and shows the
  model and settings needed to distinguish old same-name runs.

## Compatibility and limits

Optional fields preserve historical request fingerprints using exclude_none.
Existing paired, NAR-only, Stable Audio and HeartMuLa flows keep working.
The missing-AR action is local; do not invent a portable completion mode.
No automatic renaming, deletion, full-state resume, model download or user
training run is included. The currently active Stable Audio training must not
be interrupted to reload backend code. New UI capabilities stay hidden on the
old backend until it can safely restart.

## Verification

Regression tests precede implementation. Cover source tampering, symlinks,
concurrent and repeated submission, cancellation, partial publication, recovery,
legacy requests, normalized name collisions, imports and grandfathered jobs.
Use temporary config/data/module/Seed-VC roots and mocked model boundaries.
Run frontend tests/build, generated contracts, backend regression suite and
strict platform typing. Hardware training is deferred while the user's GPU
job is active; do not claim new end-to-end GPU evidence from mocked tests.
