# OpenRouter Character Swap Implementation Plan

> **For agentic workers:** Use focused parallel ownership for existing provider transport, the swap interface, and domain job integration. Follow test-first implementation and review the integrated diff. The user has approved this OpenRouter-only two-stage scope. No commits or paid requests are authorized.

**Goal:** Replace the fal character-swap route with reviewed OpenRouter frame editing and video editing while preserving retained media and audio.

**Architecture:** Extend existing OpenRouter contracts, quotes, credentials, HTTP ownership and receipts. The character-swap domain coordinates two immutable paid operations with a reviewed edited frame between them. Existing fal-era data remains local and read-only.

**Tech Stack:** FastAPI, Pydantic, httpx, supervised FFmpeg/Pillow, Vue, generated contracts, Vitest and unittest.

## Provider transport — exclusive transport ownership

- [x] Add regressions in `backend/app/openrouter_editing_test.py` for Seedream image/video source hashes, current endpoint pricing, two inline image references, one bounded raster response, Aleph video reference plus timed keyframe, no paid retries and constrained GETs.
- [x] Extend `backend/app/openrouter_contracts.py` with `OpenRouterImageEditRequest`, `OpenRouterVideoEditRequest`, image/editing capabilities and explicit quote workflows. Inputs must validate paired hashes and sizes.
- [x] Extend `backend/app/openrouter_catalog.py` with the fixed Seedream/Aleph choices, live endpoint/model normalization, `image_parameters`, `quote_image_edit`, `video_edit_parameters`, and `quote_video_edit`. Keep the configured estimate limit.
- [x] Extend `backend/app/openrouter_client.py` with `submit_image_edit(receipt_id, body, quote_id)` and `submit_video_edit(receipt_id, body, quote_id)`, using existing `_begin`, `_failed`, bounded HTTP and receipts. Accept only reconstructed local relative/absolute polling paths. Handle `expired` explicitly.
- [x] Run isolated provider regressions and strict Python checks; do not regenerate the client contract until root integrates the domain models.

## Domain jobs — exclusive root ownership

- [x] Add regressions in `backend/app/character_swaps_test.py` for local preparation without provider calls, separate frame/video quote and confirmation gates, stage/revision changes, quote renewal, receipt ownership, legacy media retention, unknown outcomes and local interrupted-publication recovery.
- [x] Update `backend/app/character_swap_contracts.py` with stage, optional authoritative quote, source/edited frame URLs, receipt IDs and resumability. Change submit to `quote_id` plus revision and confirmations. Keep fixed local media URL validation.
- [x] Update `backend/app/character_swaps.py` to extract the source frame, retain the full normalized character reference, own quote tasks, reuse OpenRouter settings/client/ledger, publish the edited frame before the video stage, and recover only from owned receipts. Preserve source audio and all existing worker supervision.
- [x] Add `POST /api/videos/character-swaps/{id}/quote` and frame media routes in `backend/app/api/routes_character_swaps.py`. Quote and submit must reject stale revisions and legacy provider requests.
- [x] Remove the superseded `backend/app/fal_video.py` and its tests. Update `.github/workflows/ci.yml` checked scope. Include `expired` in `backend/app/video_cloud.py` terminal handling.

## Interface — exclusive frontend ownership

- [x] Update `frontend/src/api/characterSwaps.ts` to quote retained inputs and submit the selected quote ID.
- [x] Update `VideoCharacterSwap.vue` and `characterSwap.ts` for OpenRouter setup, frame quote/generation, edited-frame review, video quote/generation, renewed quotes and read-only historical provider records. Keep stale-response protection and saved playback.
- [x] Add stage, quote, setup, consent, renewed quote and teardown regressions to `VideoCharacterSwap.test.ts`. Exclude edit-only models from `VideoCloudProvider.vue` ordinary storyboard choices.

## Integration and verification

- [x] Regenerate with `backend/.venv/bin/python backend/scripts/generate_contracts.py`; never hand-edit `frontend/src/api/generated.ts`.
- [x] Update `docs/video-studio.md` and `docs/openrouter-video.md` with the two approved stages, existing settings, live quotes, limits, unknown outcomes and unverified live forwarding/quality.
- [x] Run frontend `npm test` and `npm run build`, backend isolated discovery, desktop tests, contract drift and the exact strict mypy commands from CI for Linux/macOS/Windows.
- [x] Review the integrated diff, preserve unrelated work, shut down isolated previews, and report actual checks and remaining live-provider/platform uncertainties.

Verification completed: frontend 1,102 tests and strict build; backend 1,332 tests (10 skips), plus the final known-receipt/unknown-outcome recovery regressions; desktop 78 passes (1 skip); generated-contract drift and whitespace checks; both CI mypy commands for Linux/macOS/Windows. Independent review findings were resolved and eight regressions rechecked. Browser preview/reload used synthetic media and mocked image output; its isolated server and tab were closed. No paid generation, commit, deployment or user-library mutation.
