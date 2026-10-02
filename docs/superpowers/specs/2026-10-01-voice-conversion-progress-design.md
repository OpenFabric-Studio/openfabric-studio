# Voice conversion progress

The user approved visible queue reasons, processing stages, elapsed time and measured progress/ETA after a live diagnosis found a conversion waiting sixteen minutes behind voice training. The application shares one accelerator lock across these operations. Keep this serialization and all existing quality settings.

Extend the existing typed voice progress contract and tracker to voice applications. Publish this optional progress through application status and audio-version responses, with backward-compatible defaults. Persist timing in the application receipt so completed, failed, cancelled and interrupted jobs retain a frozen clock after reload. A retried job starts a new clock.

Track the accelerator's actual owner while its existing lock is held. Waiting applications report the owner's operation and a bounded display label, with a generic busy fallback. Do not identify owners by guessing from queued job registries. Cancellation and exceptions must release both ownership and the lock.

Instrument the inspected Seed-VC source through the existing idempotent compatibility patch mechanism. Emit structured phase and completed-chunk events without changing inference parameters or audio output. Read events incrementally with bounded validation; duplicated, malformed and regressive events must not corrupt counts or timing. Estimates describe the current phase, use measured completions, exclude warm-up, and remain unavailable until enough measurements exist. Repeated diffusion bars are not whole-song progress.

Show active voice applications in the track's audio-version area even while the user listens to Original or another completed version. Use the same progress display for initial voice application and uploaded-track replacement. Show translated queue explanations, stage, elapsed time, chunk counts and phase ETA; use indeterminate progress during unmeasured work. Announce stage changes without reading every timer tick aloud. Preserve playback selection, polling cancellation and action ownership.

Verification covers actual lock ownership, cancellation, malformed progress, phase transitions, measured estimates, persisted terminal clocks, interruption/retry, generated contracts, independent active-version visibility and frontend disposal. Unit tests use isolated library/config/Seed-VC paths and mocked model execution. No test may mutate the installed engine or real library.

Work is isolated on `feat/voice-conversion-progress`, based on fork `master`. Publish the verified branch to `origin` (`mchosc/remiqora`); do not merge shared master or push to upstream as part of this task.
