# OpenRouter-only character swap

This supersedes the provider choice in the earlier character-swap design. The user approved OpenRouter only and an in-app edited-frame preview with a separate cost review before each of two paid operations.

## Approved workflow

Keep the existing short-clip panel, original retention, 2–8 second selection, local 24 fps preparation, selected source audio, saved previews and downloads. Preserve existing uncommitted work and historical media. Remove the fal transport and active fal configuration.

Preparation remains local. Retain the uploaded video and character image, select a silent 480p clip, and extract its first frame. Normalize the character reference without cropping away its body. OpenRouter setup uses the existing Settings → Providers enablement, credential storage, connection controls and estimate limit.

The first paid operation uses `bytedance-seed/seedream-4.5`, one image at 2K, with two inline image references: the selected original frame and the character image. Quote from live image endpoint metadata, which currently lists $0.04/output image and zero input-image charge. Require the rights/transfer confirmations and an explicit frame-generation action. Persist and normalize the returned raster frame to the clip geometry. Show it for review; completing this step does not purchase a video.

After the user reviews the edited frame, obtain a fresh quote for `runway/aleph-2`. Submit the selected silent video as a video reference and the reviewed frame as the Runway keyframe at zero seconds. Aleph's published native limits cover the approved 2–8 seconds and 24 fps. Its current live catalog price is $0.28/output second with a $0.56 minimum. Preserve the user's existing per-request estimate limit; do not increase it automatically. Default the selected interval to two seconds so the ordinary $1 limit can admit the first video quote.

Each quote is bound to model fingerprint, request fingerprint, input hashes, duration, geometry and stage. Renew expired quotes against the retained inputs, without requiring another upload. Separate stages reset confirmations and never purchase the next stage automatically. Estimates remain estimates, not billing caps.

## Ownership and recovery

Reuse `OpenRouterClient`, existing provider quotes and durable request receipts. Persist the stage's receipt ID and submission intent before the one paid POST. Both stages prohibit paid retries after ambiguous outcomes. Recover a known video ID through GET requests only. A synchronous image response cannot be replayed after an unknown outcome; a retained completed raster may be recovered locally.

Keep owned preparation, quotation, provider and encoding tasks in the existing character-swap registry. Cancellation drains local workers. Stop tracking does not promise provider cancellation or refunds. Verify receipt ownership before recovering a provider ID. Publish edited frames and final videos atomically with hashes; recover validated pending publications without credentials.

Original audio stays local. Decode and validate the delivered video, discard provider audio, and attach the selected original AAC stream. Retain saved before/after playback after navigation and restart. Existing fal-era records remain readable for local media only; they cannot make provider calls or be submitted to OpenRouter under an old request ID.

## Boundaries and limitations

Use fixed model IDs and typed request/response schemas. No new packages, provider picker, cloud storage, queues or servers are required. Decode exactly one bounded PNG/JPEG/WebP response for the frame stage; constrain its dimensions/pixels before publication. Restrict video requests and authenticated downloads to OpenRouter's fixed origin. Aleph data URIs use the conservative native five-million-byte encoded limit until the contradictory generic video limit is resolved.

OpenRouter's schema accepts video URL strings and native Runway accepts data URIs; forwarding inline video through OpenRouter is an inference that remains unverified without a live request. Do not advertise successful end-to-end character fidelity or scene preservation from mocked tests. The edited-frame review is essential.

## Verification

Provider HTTP tests cover exact payloads, live quote units, input hashes, request ownership, single POST, malformed/oversized results, unknown outcomes, relative local polling paths and expired jobs. Domain tests cover the two separate approvals, stage transitions, quote renewal, original retention, audio, cancellation, shutdown, copied-library receipts and interrupted frame/video publication. Frontend tests cover provider setup, both quote gates, reviewed-frame preview and stale asynchronous responses. Run the required frontend, backend, desktop, generated-contract and three-platform strict Python checks.

Primary sources checked 2026-10-11: [OpenRouter OpenAPI](https://openrouter.ai/docs/openapi/openapi.yaml), [image API](https://openrouter.ai/docs/guides/overview/multimodal/image-generation), [Seedream endpoint metadata](https://openrouter.ai/api/v1/images/models/bytedance-seed/seedream-4.5/endpoints), [video model catalog](https://openrouter.ai/api/v1/videos/models), [Runway Edit Studio workflow](https://help.runwayml.com/hc/en-us/articles/51683104370451-Creating-with-Edit-Studio), [Runway inputs](https://docs.dev.runwayml.com/assets/inputs/), [native Seedream inputs](https://docs.byteplus.com/en/docs/ModelArk/image-generation-api).
