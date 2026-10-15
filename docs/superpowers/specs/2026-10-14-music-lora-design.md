# Music LoRA support

The user requested both adapter loading and training where supported. Reuse ACE-Step's existing loading and dataset/training/export workflow. Add native Stable Audio 3 MLX training and inference, and YuE2 audio.cpp AR/NAR adapter inference. HeartMuLa's pinned MLX port has no adapter API or trainer; show this limitation. Do not adopt the experimental CUDA HeartMuLa trainer or add app-owned model surgery.

## Boundary and storage

A backend-managed immutable adapter library imports bounded `.safetensors` files, validates the header and native format/target, copies into private UUID directories atomically, and records a checksum and compatibility manifest. Native Stable adapters require `lora_config` metadata and a supported base variant. YuE adapters must match its unfused AR or NAR naming/layout. No pickle formats or user-provided runtime paths. Generation references adapter IDs plus bounded strengths; backend resolution checks containment, symlinks, target and checksum. Saved generation parameters retain adapter provenance. Provide safe adapter downloads for export.

## Native flows

Stable CLI receives its documented `--lora <path> strength=<value>` arguments. Training takes tracks selected from the existing library with per-track captions; it creates a private dataset copy, pre-encodes with the real matching SAME encoder, trains the matching rectified-flow base model, then imports the final native checkpoint. Bounds cover sample count, duration, rank, learning rate, steps and seed. Require preinstalled source, environment and all weights, with offline execution. No implicit model downloads. Each run has a fresh directory and dataset/settings snapshot; no resume option because native resume is weights-only.

Training jobs are backend-owned, durable and cancelable. Reuse supervised subprocesses, GPU admission, verified crash recovery and shutdown drainage. Interrupted jobs do not restart or silently register partial checkpoints. Preserve original tracks and retained checkpoints. Final publication can be retried after interruption when a verified completed checkpoint exists.

YuE selections become session-load options with independent AR/NAR strengths. Compare and explicitly clear every adapter option when disabled so a resident adapter cannot affect later default jobs. Preserve owned YuE serialization, persistence and cancellation.

## UI and compatibility

A `/music/lora` page remains available regardless of ACE status. It explains loading/training capability per engine, links ACE's existing workflow, imports native adapters, and provides Stable training and durable history. Reuse existing styling, library picker, polling and cancellation guards. Add compatible adapter/strength selectors to Stable and YuE generation; retain existing bookmarks and old requests without adapters. Generated Pydantic client contracts remain the source of truth. Never label source-only checks as hardware verification.

## Verification

Use temporary configuration, library and engine locations, mocked GPU boundaries and small synthetic safetensors fixtures. Test format/target/size/path validation, corrupt manifests, checksum changes, adapter-off reload, provenance, training admission/cancellation/recovery and navigation polling. Run frontend tests/build, isolated backend regressions, generated contract checks and CI's strict platform typing loop; desktop tests if startup integration changes. No models or training audio are downloaded by tests.
