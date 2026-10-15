# Training progress and settings help

Add useful feedback to the existing Stable Audio, HeartMuLa and YuE training views without interrupting running jobs.

1. Extend the persisted job contracts with optional start/end timestamps and measured optimizer-step telemetry. Retain compatibility with older jobs. Read bounded native Stable Audio progress output; measure HeartMuLa/YuE optimizer time. Preserve final measurements on all terminal paths.
2. Reuse one training progress card for elapsed time, actual steps/loss, measured training ETA, last report and submitted settings. Hide ETA before sufficient measurements, during other stages, and when disconnected or stale. Stop timers on teardown; completed durations stay fixed.
3. Add an accessible info button next to Train, opening the existing help dialog with plain-language explanations of every training setting and model-specific inputs.
4. Cover parsing, terminal persistence, clock/ETA behavior, and help interactions with regression tests. Run frontend tests/build, backend checks, generated-contract verification and applicable strict typing checks.

The current user training job must keep running. Frontend changes can appear through HMR; the updated backend requires a restart after the job finishes. Do not restart it to demonstrate this change.
