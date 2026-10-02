# Generation workspace and fork improvements

The user approved adopting the useful fork ideas from the public-fork audit and requested configurable generation history with reusable presets. Implement selected behavior on current contracts; do not wholesale import old stores, lossy source handling, third-party binaries or experimental acoustic chunking.

## History and presets

Store immutable generation records in the existing SQLite library, independently of audio lifetime. Retain the newest 100 records by default, configurable from Settings with validated integer bounds. Reducing the limit prunes history metadata only, never tracks, audio, presets or active jobs. Seed existing generated tracks once when the new schema is introduced. Capture lyrics, engine, title, creation time, effective generator parameters, source metadata, seed and nullable track identity. Presets are independent named editable snapshots saved from history or the current form. Keep presets permanently until explicitly deleted. Duplicate names must not silently overwrite another preset. Provide search/engine filtering, preview, reuse, save, rename/update, duplicate and confirmed deletion. Migrate valid legacy browser presets idempotently without clearing local data until the server acknowledges persistence. Settings and presets live in the selected library rather than only one browser.

Reuse our backend schemas and deterministic TypeScript generator; validate all boundaries. Keep engine-native JSON opaque only where the current system already treats it that way. Exclude internal paths and secrets from public saved settings. A missing reference upload cannot silently imply reproducible audio: show that the user must reselect the file, or resolve a retained app-owned asset.

## Generator reliability and controls

Retain YuE drafts through engine transitions, commit unfinished style tags on blur without breaking suggestions/IME/keyboard controls, and reset omitted copied settings to defaults. Add separate ACE style-reference upload and caption enhancement control through owned ACE jobs. Keep lossless masters and independent voice/output versions. Add individual stem MP3 exports using global encoding settings and original stem sources.

Cancellation must be backend-owned: track run/session identity, stop and await the owned native process, coordinate model admission, and hold subsequent queued work until restart is ready. Expose validated nullable native progress with run identity, elapsed time and real phase counts. Token maxima remain upper bounds; percentages are only available where total work is known. ETA must reflect compatible observed measurements, remain unknown when insufficient, and never claim a calibrated confidence range without evidence.

Notifications are optional, controlled in Settings, and emitted after durable completion including requested voice processing. Deduplicate notifications across polling/history refresh and handle browser/Electron support and denial. Preserve selected playback across persistence and history refresh; sliced base64 decoding is an allocation optimization, not streaming.

## Installation and native changes

Preserve ACE checkpoints across source replacement and interrupted recovery, including conflicts where both source trees contain models. Resume YuE model files only with immutable revision/content identity and verified completion; use compatible cleanup naming and serialized atomic publication. Enforce disk/writability checks on every installation path. Report progress from pending components. Keep current branding, manifest pins, storage migration and download protections. Version the desktop document URL on upgrades without changing backend origin.

Use existing supported YuE arena options with capacity validation and explicit compatibility. Provide reproducible clean-source patch/build support for progress/workspace release across target backends; do not copy the fork's Windows executable. Keep native quality/performance claims explicitly unverified until actual platform measurements exist. Fix inherited Linux repeat-setup path capture and environment overwrite without modifying current private configuration.

## Reference preparation

Add optional provider-restricted URL ingestion with original source retention, bounded network/subprocess execution, private-address and redirect protection, unique confined staging, owned cancellation and durable publication. Use existing separation services. Make subtitle/Whisper lyrics and SheetSage melody preparation explicit optional stages with reviewable output and missing-tool states. Provide bounded typed lyrics/ABC editing APIs; correct accidental/key/bar semantics and retain Apache-2.0 third-party attribution where code is adapted. No new model download is performed as part of unit testing.

## Verification and delivery

Work in the isolated feat/generation-workspace worktree. Use focused independent agents with exclusive ownership. Add failing behavioral regressions before fixes where practical; test pruning versus saved audio/presets, duplicate/migration behavior, stale async responses, cancellation/restart, interrupted installation and malformed native/network output. Run frontend tests/build, backend regressions, contracts, strict Python platform checks and desktop tests. Review each integrated subsystem and final diff. Do not commit, push or alter the active library without an explicit request. Report native/model/platform limitations honestly.
