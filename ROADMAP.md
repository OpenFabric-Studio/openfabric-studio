# OpenFabric Studio maintained fork roadmap

[OpenFabric-Studio/openfabric-studio](https://github.com/OpenFabric-Studio/openfabric-studio) is a maintained fork of
[inikolax/remiqora](https://github.com/inikolax/remiqora), originally created by
Nikolay Cherkashin. The OpenFabric Studio name and upstream attribution remain. The fork's
development snapshot is **0.3.0-dev.0**, unreleased; this is not a stable release
or a promise of available installers.

The direction is a local music, singing-voice and video creation workspace that
keeps source material and completed work recoverable. Reliability comes before
adding another model selector. Priorities below are ordered, without release dates.

## Implemented, still experimental

| Area | Present behavior | Evidence and limits |
| --- | --- | --- |
| Library and jobs | Shared tracks, per-track favorites, saved jobs, cancellation and reload recovery. | Behavioral regressions cover persistence, failure and concurrency. Fresh installation and upgrade paths still need a recorded platform matrix. |
| Voice preparation | Reviewed samples with original source/time provenance, singer confirmation, optional cleanup, coverage measurements, explicit build/resume and separate listening trials. | Synthetic audio and mocked model boundaries verify workflow rules. They do not establish singer identity, separation quality or a universal training budget. See [voice preparation](docs/voice-quality.md). |
| Audio versions and formats | Retained originals, separate cloned versions, explicit MP3/WAV/FLAC profiles and playback of the selected exported file. | CPU media tests verify encoding and provenance. Missing historical originals cannot be reconstructed; higher export resolution cannot recover lost detail. See [audio export quality](docs/audio-export-quality.md). |
| Quality and provenance | Optional local speaker screening, measured audio/loudness targets, export manifests and scoped module license cards. | Human review remains required. Unknown renderer identities and source rights stay unknown; metadata is not legal certification. See [quality and provenance](docs/quality-and-provenance.md). |
| Video studio | Saved storyboards, cover/visualizer modes, estimated musical markers, reviewed previews, timed text, framing and export. Generated scenes use the pinned Apple MLX engine. | Real CPU media tests and selected local smoke checks exist. Generated-scene perceptual quality, full-song GPU time and peak memory remain unestablished. See [video studio](docs/video-studio.md). |
| Desktop shell | Setup, process lifecycle and installer tooling inherited from upstream, with fork integration changes. | Packaging tests are useful evidence, but do not prove a clean installation or GPU inference on Windows/macOS. Installers remain experimental. |

## Priority 1: establish a dependable baseline

- Verify installation from a clean checkout/environment, with explicit dependency
  and engine/model checks. Missing tools should produce an actionable error.
- Exercise upgrades on copied libraries: preserve IDs, originals, favorites,
  projects, voice artifacts and export catalogs; document backup and recovery.
- Expand failure checks around interrupted writes, cancellation, shutdown,
  browser navigation/reload and competing jobs. Completed work must survive.
- Keep strict types and generated API contracts checked in CI. Document exactly
  which platform and engine combination each real smoke test covered.

Completion means reproducible checks and recorded recovery instructions, including
unsuccessful cases; a successful build alone is insufficient.

## Priority 2: make quality and provenance inspectable

- Make original, cloned and encoded selections clear across preview, comparison,
  analysis and download; retain exact source/settings provenance.
- Use representative, held-out recordings for singer comparisons. Report audible
  identity, pitch, intelligibility and artifacts separately from numerical screening.
- Evaluate fixed-seed short generated video shots before claiming quality or
  recommending full-song renders. Record device, engine/model revisions, time
  and memory; retain the comparison artifacts.
- Keep translated, accessible controls and honest loading/error/progress states.

## Priority 3: release and maintain deliberately

- Produce a reviewed prerelease only after the applicable clean-install, upgrade,
  recovery and real-platform checks in [fork maintenance](docs/fork-maintenance.md).
- Integrate upstream changes through reviewed sync branches; send focused,
  independently useful fixes upstream.
- Consider new engines only when a concrete workflow need, compatibility evidence,
  licensing review and ongoing maintenance plan justify them.

Optional OpenRouter requests use explicit credentials and cost review. Hosted
accounts, queues and microservices remain outside the plan. The application
remains local and bound to loopback by default.
