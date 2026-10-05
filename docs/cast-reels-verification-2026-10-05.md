# Cast review, passage repair and dialogue reels: verification

Implementation branch: `feat/cast-repair-dialogue-reels`, based on `main` at `5d6f2cce1b446bc86157cff7b2d7298517b63749`. This record covers verification before integration. The running user application and real library were left unchanged during implementation and verification.

## Delivered workflows

- Explicit voice reference transcripts and languages, immutable render inputs, honest cache eligibility and fresh chapter redo.
- Saved cast auditions, scene previews, timestamp passage lookup, fresh replacement takes and explicit atomic acceptance across chapter/book exports.
- Reviewed DOCX and SRT/VTT imports with immutable subtitle cue provenance and mandatory speaker mapping.
- Persisted energy pause settings and optional local Whisper review flags without automatic repairs.
- Saved reels using accepted cast audio, clip bounds, captions, cue waveforms and selective repair refresh.
- Reviewed character captions, training/evaluation splits, model/settings/dataset provenance, compatible adapters and held-out media comparisons.
- Persistent media-aware undo/redo, independent duplicate histories, versioned players/downloads, sampled filmstrips and pinned OFL caption fonts.
- LTX source compatibility investigation and an offline benchmark harness. The reviewed engine pin remains unchanged.

Usage: [audiobook cast and passage review](audiobook-cast-and-passage-review.md), [dialogue reels and character review](dialogue-reels-and-character-review.md), [LTX compatibility and benchmark evidence](ltx-compatibility-and-benchmark.md).

## Final checks run on macOS

| Check | Actual result |
| --- | --- |
| Frontend `npm test` | 989 passed across 114 files |
| Frontend `npm run build` | Passed, including strict TypeScript and unsafe-type guard |
| Backend unittest discovery | 1,008 run; 998 passed and 10 skipped |
| Contract generator `--check` | Passed |
| CI strict mypy scope | 122 production/script files passed for each Linux, macOS and Windows typing path |
| Desktop `npm test` | 78 run; 77 passed and one Windows-specific setup test skipped |
| Final diff whitespace check | Passed |

Backend checks used isolated temporary configuration, libraries, module roots and Seed-VC directories. They did not download GPU models. Tests include real FFmpeg encoding/decoding, a controlled transcription CLI, and subprocess cancellation/drain. Desktop packaging verifies that both caption font files and their licenses are copied byte-for-byte.

Independent review and regressions cover legacy completed PCM retention, changed-consent publication refusal, failed database publication, stale acceptance, interrupted acceptance journals, encoder quarantine, duplicate history isolation, immutable source cues and stale UI responses. Keeping an accepted recording now persistently dismisses a ready candidate; it cannot reappear as an actionable take after reload.

## Browser integration evidence

The built app was served on a separate loopback port with a synthetic three-voice library and mock speech. Browser checks verified:

1. Saved cast auditions restore and expose playable HTTP 200 WAV routes with an explicit silent mock label.
2. Edited passage candidates survive reload without replacing accepted text; explicit acceptance updates chapter/passages to a new revision.
3. Accepted passage text survives navigation and reload. Original and candidate audio remain separate before acceptance.
4. Two accepted passages create a persisted reel that opens in Video with captions, fixed padded timing, cue waveforms and copied cast audio.
5. Accepting another audiobook repair does not silently change the reel. Selective refresh changes Alice's line and immutable soundtrack identity while preserving Bob's cue.
6. Undo restores the old line and old soundtrack URL; redo restores the refreshed line and soundtrack URL.
7. Dismissing a ready candidate persists across browser reload, preserving the accepted recording.
8. Missing local ASR/video installations show unavailable/setup states. No browser runtime errors were recorded.

The isolated backend completed graceful shutdown and the test browser was closed. This is orchestration and persistence evidence with mock speech, not a listening-quality or GPU-rendering evaluation.

## Remaining evidence and compatibility limits

Real speech checkpoint identity and quality, Whisper recognition quality, character training, motion/identity consistency and GPU rendering performance are unmeasured. Native Windows/Linux installation and inference were not run. Platform typing checks and CPU media tests do not establish native GPU support.

LTX 0.16.0 needs a verified port of the maintained A2V tiling/adapter wrapper before upgrading; the benchmark refused a competing GPU launch while the user's app was running. See the linked source hashes and inventory reports.

Voice profile and audiobook catalogs add transactional schema migrations and retain old notes/media. Existing notes copied into the reference transcript need human review. Older application builds that reject newer schema versions require a compatible build or a pre-upgrade database backup for rollback. No real user database was migrated during this work.
