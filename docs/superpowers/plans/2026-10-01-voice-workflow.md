# Voice workflow and measured coverage

Implement the user's requested improvements in the current checkout. Preserve existing work; no commits or deployment.

## Decisions

- Use Files, Samples, Coverage, Build, Compare tabs with contextual Next actions. Tabs remain freely accessible. Page-owned preparation/comparison sessions preserve drafts and one poller across tab switches; audition players unmount when hidden.
- Reuse existing Vue components, tokens, generated contracts, backend job ownership and error codes. No new frontend dependencies or global workflow store.
- Fix verified playback failures: busy fieldsets disable play controls; rejected play promises give no feedback; source changes and unmount leave old playback/async waveform state alive.
- Expose source/sample search, filters, sorting and visible-only bulk actions. Rejected clips remain auditionable but cannot enter the training selection. Original/cleaned variants stay separate.
- Replace the app's arbitrary 900-second hard cap with a validated configurable 60–3600-second selection budget. Retain 900 seconds by default, with 15/30/60 minute presets. Bound selection to 1000 clips. Explain that clean variety, clip count and training steps matter; minutes do not guarantee quality.
- Measure observed pitch using pYIN with explicitly documented bounds and confidence gate, per real passage. Cache original/cleaned measurements and aggregate only the saved selected variants. Show reliable voiced time, observed pitch occupancy/central range and recording spectrum proxies. Never infer full singer range, phoneme completeness, chest/head register, speaker identity or a quality score.
- Analyze initial selected clips during preparation. Add an owned, cancellable coverage job for existing preparations or newly selected uncached clips, without repeating source separation. Keep unavailable measurements visible.
- Install pinned MSST inference dependencies and an author-hosted checkpoint with its matching config and verified checksum. Configure this app and run real isolated RoFormer inference. Synthetic smoke verifies execution, not real-song separation quality.

## Ownership and sequence

1. Root: playback regression tests and shared player fixes; backend coverage/duration contracts, generated client, selection/analysis integration and regression tests.
2. Frontend agent: voice tabs, persistent session, panels, filtering/bulk review, coverage UI, API/locales, mounted workflow regression tests.
3. Audio agent: lazy-import typed CPU measurement/batch CLI, pure aggregation, synthetic DSP tests, test/setup dependencies.
4. Separation agent: pinned installation helper, inference dependencies, runtime adapter and tests, local model/config/device setup, real smoke.

## Verification

- Run focused regressions red then green for playback, duration/coverage and workflow behavior.
- Run full frontend tests and strict production build; backend unittest discovery in isolated configuration/data roots; generated-client drift and strict mypy over the established scope plus new typed helpers; relevant shell syntax and desktop startup tests.
- Verify browser interaction at desktop and narrow viewport against isolated app data: keyboard tabs, drafts, filters, bulk scope, sample playback and errors, spectrum labels and busy controls. Do not write to user recordings.
- Review final diffs and actual RoFormer output/logs. Document provenance, data guidance, launch steps and measured limitations. Report failures honestly; do not claim a quality benchmark.
