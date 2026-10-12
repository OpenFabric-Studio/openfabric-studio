# Trend reliability implementation plan

**Goal:** Correct local date presets and prevent pending audio warps from producing
incorrect playback, waveform sampling or exports.

**Architecture:** Retain the Vue composables and editor's existing session guards.
Resolve buffers and their time factor together in `audio/timelineTypes.ts`; share
stretch promises inside the editor. No backend contract or persistence changes.

**Tech stack:** Vue, strict TypeScript, Web Audio, Vitest.

- [x] Add date regressions in `composables/useDateFilterSort.test.ts`: midnight in
  positive/negative UTC offsets, inclusive bounds, DST and month/year boundaries.
  Run `npm test -- src/composables/useDateFilterSort.test.ts` before fixing UTC
  formatting in `useDateFilterSort.ts` with local year/month/day fields.
- [x] Add scheduling regressions in `audio/useTimelineEngine.test.ts`: original
  fallback offsets, ready warps and MIDI. Add editor regressions for waiting,
  failed stretches, rescheduling, duplicate work and stale sessions. Run focused
  tests before changing production code.
- [x] Add `clipBuffer` in `audio/timelineTypes.ts` and reuse it in the engine and
  `TimelineLane.vue`. Give `TimelineClip.vue` the selected buffer's time factor
  for waveform sampling; preserve timeline sizing and original trim bounds.
- [x] Share pending stretches by original buffer and tempo ratio in
  `views/editor/EditorPage.vue`; return readiness, wait before export, reschedule
  current playback on completion, and add the safe error in `locales/en.ts`.
- [x] Record inspiration in `NOTICE`; run focused regressions, `npm test`,
  `npm run build`, and isolated `generate_contracts.py --check`. Review the diff
  and record actual verification and remaining limitations. Keep work on the topic
  branch; commit and push only after explicit maintainer authorization.

Review regressions also cover each ready stretch independently, Warp-disable and
cached-tempo transitions, excluded clips/lanes, waveform PCM ranges, and exactly
one playback restart for mute/solo clicks. Final frontend run: 1,256 tests passed
across 140 files; strict build passed. Contract drift and diff checks passed.

## Verification error cleanup

- [x] Trace localhost requests to missing child-component API mocks in
  `VoiceStudio.test.ts` and `pollingControls.test.ts`. Reproduce failures using
  a default fetch guard, add the missing mocks, and retain the guard so caught
  request failures cannot silently pass tests.
- [x] Measure the entry chunk. Remove unused title/description/default annotations
  from schema nodes in the contract generator, retaining named domain properties,
  literal values and validation constraints. Regenerate contracts and add tests.
- [x] Run frontend tests/build, contract drift checking, the full isolated backend
  suite and both CI mypy commands for Linux, macOS and Windows typing paths.
  Review the diff independently and record results in the design document.
