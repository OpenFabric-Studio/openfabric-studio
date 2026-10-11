# Character-swap input review

The user approved the first recommendation from the Genjustsu source review: improve the existing character-swap review panel before adding conditioning models or another provider.

## Behavior

Prepare a five-frame strip from the selected clip using the existing owned FFmpeg pattern. Display it alongside full-clip playback, the character reference/edited frame, and the exact backend prompt for the current paid stage. The strip is a sampling aid; it cannot establish that every frame was inspected.

A separate local review action records acceptance of the displayed stage and inputs. It requires the current revision and review fingerprint, verifies retained artifact hashes, and atomically records the accepted fingerprint. Paid submission requires that saved acceptance in addition to the existing quote, transfer and rights confirmations. A new edited frame changes the fingerprint and requires a fresh review for the video stage. A price refresh alone does not change the reviewed media; cost/transfer confirmations still reset.

The fingerprint covers original source, normalized reference, selected audio/video, silent cloud input, first frame, optional frame strip, edited frame, stage, model, dimensions, duration, seed and exact prompt. Hash checks reject changed files before acceptance and submission. Review state survives reload and restart. Existing saved jobs without strips remain playable and reviewable; legacy provider jobs remain read-only. No paid retries, provider changes, prompt editing, new models or dependencies are added.

## Integration and checks

Extend Pydantic contracts and regenerate client types/parsers. Add a local-origin protected review endpoint and constrain the new media URL to its owning job. Use current admission/document locks, atomic metadata writes, worker ownership, cancellation and staging publication. Add backend regressions for changed/stale review, stage transitions, old records, interrupted review publication, strip content, API bypass and cross-origin rejection; frontend tests cover display, gates, saved acceptance and stale async responses.

Run frontend tests/build, isolated backend discovery, generated-contract check and the CI strict mypy loops for all three platform paths. No desktop startup/packaging/file-layout behavior changes. Review the final diff. No commits, model downloads or paid generations.
