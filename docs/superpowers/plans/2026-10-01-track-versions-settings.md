# Track audio versions and export settings implementation plan

> For agentic workers: use focused parallel ownership and test-driven implementation. Preserve the dirty workspace and running user services. Do not commit or run models. Root integrates generated contracts and reviews completed domains.

**Goal:** Play original and multiple cloned voices for one track, export each version in additional formats, and configure explicit encoding defaults/data storage on a header-linked Settings page.

**Architecture:** Persist immutable audio-version records attached to tracks, using the existing owned voice-conversion worker for each independently identified result. Original sources are always used for additional voices. Persist asynchronous format exports with captured settings and explicit FFmpeg arguments; settings remain separate from folder migration configuration. Preserve legacy public track/apply contracts and safely recover existing original siblings when verifiable. New ACE generations retain a lossless source and produce the requested download format from it.

**Tech Stack:** Existing FastAPI/Pydantic/SQLite, owned subprocess lifecycle and FFmpeg; Vue/Pinia/generated strict contracts/Vitest. No new infrastructure or dependencies.

## Ownership and steps

- [x] Audio versions backend: `audio_version_contracts.py`, `audio_versions.py`/store, routes and tests; narrow `db.py`, `voice_build.py`, track deletion/startup hooks. Reproduce original/voice retention, independent conversion from original, legacy recovery, missing source, cancellation/reload/deletion and path validation before fixing.
- [x] Encoding/settings backend: `audio_encoding.py`, `audio_exports.py`, dedicated settings/export routes and tests. Explicit MP3 CBR/VBR, rate/channels; WAV PCM16/24/32-float; FLAC16/24 and compression0–8. Validate schemas, atomically persist global defaults, capture per-export settings and source, own/cancel/drain children, preserve completed versions, publish metadata/downloads only after success. CPU fixtures verify actual codecs/rates/bits and cancellation/failure.
- [x] Settings UI: new Settings page and generated typed client, header link and route; move existing LibraryFolder out of Home. Named translated controls, loading/save/reset/error states, dirty draft protection against stale responses and keyboard accessibility. Reproduce routing/move/settings validation and save behavior.
- [x] Root registers/regenerates contracts and integrates routers/lifecycle. Integrate ACE original-WAV retention and requested-format exports with settings captured at submission. Preserve old jobs/paths and no browser ownership of completed results.
- [x] Root adds shared track-version UI/API used in ACE and YuE results: original/voice playback selection, add another ready voice, conversion status/cancel/retry, version-specific exports/downloads. Keep source/generated parameters and existing editor/stems behavior correct. Reproduce switching/reload/late-response ownership and action guards.
- [x] Expand strict checked modules and isolated tests; review spec compliance and final diff. Run full frontend suite/build, backend suite, contract drift, applicable strict Python checks and desktop tests if shared lifecycle/layout changes affect them.
- [x] Browser-check Settings and original/voice/export workflow against isolated fixtures without user-library writes or inference. Document legacy recovery limits, explicit encoding defaults, lossless-source behavior and actual evidence.

## Safety and compatibility

- Tests use temporary config/library **and SEED_VC_DIR**. Mock every model boundary; synthetic CPU audio only.
- Existing files and voice outputs are preserved. Never pretend a missing/overwritten historical version can be reconstructed.
- A re-encode cannot recover detail lost in older MP3/16-bit sources; explain this where users choose export settings.
- Track deletion must drain voice/export work before removing owned files. Recovery marks interrupted work rather than silently resuming model inference.
- Defaults apply to future exports; each queued export snapshots its settings. Folder changes retain the existing restart/migration workflow.

## Verification recorded 2026-10-01

- Full frontend: 301 tests in 43 files; strict type policy, Vue/TypeScript and production build passed.
- Full backend: 432 isolated regression tests passed. Models were mocked; synthetic CPU fixtures verified actual FFmpeg encoding.
- Strict CI Python scope: 43 modules passed; generated contract drift check passed. Desktop: 27 tests passed.
- Private browser: original/voice playback, additional voice creation and reload persistence, Settings save/reload, MP3/FLAC download parameters, desktop and 390 px mobile layouts checked. Long library paths wrap.
- Independent review reproduced and fixed automatic export retry promotion, substituted-export recovery and ACE deletion/save-retry races. Final scoped diff/whitespace checks passed.
- User API/Vite processes and model checkpoint link remained unchanged. New backend routes require a normal backend restart after current work finishes. GPU conversion quality and Windows subprocess behavior were not re-tested in this change.
