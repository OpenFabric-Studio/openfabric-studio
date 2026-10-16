# YuE paired LoRA creation

> Historical record — HeartMuLa retired on 16 October 2026. Its generation, training and installation instructions below no longer describe supported functionality. See [current music documentation](../../local-music.md).

The requested flow is one dataset, one name and one Start training action that
creates an AR composition adapter and a NAR acoustic adapter. Music generation
continues to expose its two independent adapter selectors.

## Recommended flow

1. Validate the selected recordings, captions, optional matching clip lyrics,
   installed training runtime and existing SheetSage2 score extractor.
2. Copy and prepare the bounded excerpts once. Preserve original recordings and
   saved datasets. Extract a musical score automatically for each excerpt using
   the existing native SheetSage2 model in an owned CLI subprocess.
3. Train AR, release that trainer, then train NAR. Both belong to one persisted
   job; they do not compete for GPU memory. Use separate conservative defaults
   for planner and acoustic training, visible under advanced settings: AR starts
   at 100 steps and learning rate 0.00005; NAR retains 1000 and 0.0001. Short
   excerpts teach local musical patterns; they do not cover an entire song's
   structure.
4. Validate and convert both checkpoints to the native formats. Register two
   distinguishable adapters with the same source job and their respective target.
   The job is complete only when both are registered. Existing completed stage
   files remain on disk if another stage fails; recovery never silently reruns
   an optimizer.
5. History shows score preparation, AR progress and NAR progress, elapsed time,
   an honest current-stage ETA, and separate exports. Preview with the pair fills
   both generator selectors; base comparison clears both. Neither starts music
   generation automatically.

## Existing behavior and compatibility

The current pinned trainer exposes both `planner` and `acoustic` commands. The
app currently invokes only acoustic training and publishes one NAR artifact.
Native generation already supports both targets. The app already includes a
SheetSage2 GGUF model and a native CLI, so no additional transcription-model
download is the default approach.

The installer currently marks only the server executable; repair the CLI's
executable mode in both fresh setup and existing owned installations. Validate
the bounded `score.abc` output and reject empty/all-rest transcriptions before
either trainer starts. The native extractor can return an all-rest fallback for
insufficient beat information, so a nonempty file alone is not success.

Keep old single-NAR and HeartMuLa jobs, receipts, adapters and request fingerprints
valid. Paired requests need an explicit optional settings/version field rather
than a new non-null default that changes historical fingerprints. New completion
receipts attest exactly one AR and one NAR artifact. Registry publication must
deduplicate by source job and target, survive interruption between registrations,
and never call a partial pair complete. Portable NVIDIA packages must preserve
the same prepared scores and verify both resulting artifacts.

## Alternatives considered

- Running both trainers concurrently increases memory pressure without improving
  the user flow. Use sequential stages within one action.
- Submitting two unrelated jobs complicates cancellation, progress and matching
  outputs. Use the existing durable job infrastructure with explicit stages.
- Reusing the shared native server's score extraction from an active training job
  conflicts with its own admission guard. Use an owned native CLI child instead.

## Validation before claiming support

Verify the pinned CLI arguments and output schema, its executable readiness,
planner tensor conversion and MPS numerical behavior. Run a bounded synthetic
Mac smoke test that makes finite, nonzero AR and NAR weights and loads both in
native generation. Never use the user's music for unit tests or a long training
run. Test failure and cancellation between stages, publication interruption,
reload, legacy recovery, phase progress and paired preview. Run the repository's
frontend, backend, generated-contract and strict platform typing checks.

Automatic score extraction can make musical errors; successful training does
not establish musical quality. This work does not add exact optimizer-state
resume or claim NVIDIA hardware validation.
