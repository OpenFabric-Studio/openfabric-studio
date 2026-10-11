# Video scene cutter implementation plan

Approved scope: local AVI/MP4/MOV/WebM scene selection, MP4 exports up to ten minutes, and owned-cut handoff to the existing 2–8 second character swap. No commits or paid requests.

## Backend scene service (exclusive backend ownership)

- [x] Define public/private Pydantic source, preview and cut contracts in `backend/app/video_scene_contracts.py`.
- [x] Add real CPU AVI regressions first in `backend/app/video_scenes_test.py`, with temporary paths and a mocked native picker.
- [x] Implement `backend/app/video_scenes.py`: native macOS source selection, bounded upload fallback, source signature validation, safe explicit demuxing, short-window preview, accurate in/out cuts, durable assets, cancellation and recovery. Reuse owned media primitives.
- [x] Add `backend/app/api/routes_video_scenes.py`: readiness, source list/import/pick/get, preview, cut list/create/get/cancel and fixed-owned-media serving. Reject cross-origin mutations and untrusted paths.
- [x] Public functions include `recover`, `shutdown`, `work_busy`, and async `resolve_cut(source_id,cut_id)` returning a verified owned cut path and its contract.

## Interface (exclusive frontend ownership)

- [x] Add generated-contract client wrapper `frontend/src/api/videoScenes.ts`.
- [x] Add `VideoSceneCutter.vue` and tests: source selection, short preview, movie timeline/time input, in/out markers, bounded selection, saved-cut polling/download/cancel and `useCut` emit.
- [x] Add locale strings in `frontend/src/locales/videoScenes.ts` and register in en.ts.
- [x] Add Scene cutter toggle and ready-cut handoff in VideoPage.vue/test.ts.
- [x] Extend VideoCharacterSwap.vue/test.ts and characterSwaps.ts with a chosen-cut prop and multipart from-scene action; reset approvals and protect awaited work against teardown.

## Integration (exclusive root ownership)

- [x] Extend safe video probing to AVI and verify playlist rejection.
- [x] Add app-owned character-swap from-scene multipart route, validating IDs, selected interval and retained-cut identity before calling existing local preparation.
- [x] Register scene contracts for generation and scene router/lifecycle/work-busy in main.py/work_busy.py; expand CI strict scope.
- [x] Regenerate frontend contracts after backend models settle; never hand-edit generated.ts.
- [x] Update video-studio.md with native large-movie selection, previews, in/out, local exports, platform/upload bounds and the swap handoff.
- [x] Run meaningful focused regressions, frontend test/build, isolated backend discovery, contract drift, exact CI mypy loop for Linux/macOS/Windows, and desktop tests.
- [x] Review final diff and independent review findings; verify synthetic UI and preserve the user's current test instance/media/settings. No paid cloud call or original movie modification.

## Verification evidence

- Frontend: `npm test` — 1,124 tests passed; `npm run build` passed strict typing and production compilation.
- Backend: isolated `python -m unittest discover -s app -p '*_test.py' -v` — 1,371 tests, 10 skipped, successful. Temporary config/data/modules/SEED paths; no model downloads or user library changes.
- Contracts: regenerated from Pydantic; `--check` passed. Exact two CI mypy commands passed for Linux, macOS and Windows typing paths.
- Desktop: `npm test` — 78 passed, one skipped. Final diff/whitespace check passed.
- Independent review: resolved owned URL boundaries, corrupt-record worker recovery, unsafe-root startup, native display geometry, upload receive limits, swap size/default duration and field labels. Eleven final targeted regressions independently passed.
- Browser: isolated app at `http://127.0.0.1:9018/video`; real synthetic AVI preview decoded in Chrome, sought to 30 seconds, marked in, exported 30–34 seconds. Saved cut survived browser reload and backend restart, downloaded with audio, served byte ranges, and opened character swap with a two-second default. Screenshot `/tmp/of-scene-cutter-ready.png`.
- Unverified: real native dialog interaction, the user's AVI codecs, large 4K throughput and paid OpenRouter generation. Native picker script compiled; automated picker tests used a mock. Existing 9000/9017 instances remain intact; the 9018 demo uses a separate temporary library.

## Approved MKV follow-up

- Added MKV to native selection, scene uploads and direct character-swap preparation using the existing Matroska demuxer. Updated chooser filters, validation, messages and user documentation; no new dependencies or contract changes.
- Real H.264/FLAC MKV tests cover native-source and uploaded-source previews, MP4 cuts with audio, unchanged originals and playlist rejection. Direct MKV swap preparation remains local.
- Frontend: 1,126 tests passed and strict build passed. Backend: 1,375 tests completed successfully, 10 skipped. Contract drift and both exact CI mypy commands passed for all three platforms. Native picker script compiled; dialog selection remained mocked.
- Updated the idle 9018 test backend while retaining its library/configuration. Imported a synthetic MKV through the API, saved seconds 2–6 through the browser UI, and verified the downloaded MP4 has four seconds of H.264 video and AAC audio. Screenshot `/tmp/of-mkv-scene-ready.png`.
- Desktop checks were not repeated for this format-only extension. The user's actual MKV codecs and large-file throughput remain unverified.

## Preparation geometry fix

- Reproduced the reported local 400 with a 1280×718 saved scene: short-side scaling produced 856×480, outside the strict model aspect-ratio tolerance.
- Fit sources within 3% of a supported aspect onto an even-pixel 480p canvas with a small border. Preserve the full picture, source bytes and audio; use display dimensions for anamorphic pixels and right-angle rotation. Model/quote checks stay strict.
- Added real scene-to-swap API regression coverage for 1280×718, border/audio retention and free validation of both paid-stage parameters, plus anamorphic and unsupported-aspect cases. Frontend: 1,126 passed and strict build passed. Backend: 1,378 tests successful, 10 skipped. Contract drift and both exact CI mypy commands passed on all three platforms.
- Verified eight seconds of the retained `Scene-test-1` using a synthetic reference in an isolated temporary library: 854×480, eight-second video and audio, no cloud request. Updated the idle 9018 backend with its existing library/configuration; confirmed provider readiness and retained scene availability. Paid generation and desktop packaging were not repeated.
