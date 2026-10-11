# Character swap

Provider choice superseded by [the approved OpenRouter-only two-stage design](2026-10-11-openrouter-character-swap-design.md). This document records the earlier fal implementation.

Approved scope: an optional paid cloud workflow that replaces the whole person in a supplied video, using a replacement image. Start with one prominent person and a selected 2–8 second section at 480p. The model aims to preserve performance and scene; pixel-perfect preservation and identity quality are not established.

## Workflow

Video has a Character swap option alongside the existing workspace. Upload an MP4, MOV or WebM (100 MiB maximum), a PNG/JPEG/WebP image (15 MiB maximum), a name and selected interval. Preparation remains local. Retain both originals and create a bounded 24 fps selected preview, silent cloud input, and normalized reference. Preview the prepared inputs before generation.

Each prepared job carries a 15-minute estimate based on the actual submitted frame count, the published 480p rate ($0.04 per 16 frames), and its review date. Require separate likeness/rights and cloud-transfer confirmations plus an explicit generation action. The estimate is not a spending cap. Only the selected silent video and normalized image leave the computer. The backend reads FAL_KEY from its environment; keys never reach browser state or saved jobs.

Use fal-ai/wan/v2.2-14b/animate/replace through its fixed asynchronous REST queue. Persist submission intent before the single paid POST and the verified request ID before polling. An ambiguous submission remains unknown and cannot be resubmitted automatically. Known requests recover through GET polling only. Stop tracking drains local tasks without promising remote cancellation or a refund. Resume follows the saved remote request without another paid POST.

Validate provider responses and restrict downloads to HTTPS fal.media origins without redirects or credentials. Decode the entire bounded delivered clip, validate duration and geometry, conform its picture to the selected interval, and attach only the original selected audio. Atomically publish the output and its hash. Saved input/output playback and download survive navigation and restart.

## Architecture and scope

Keep a focused job document and owned files under the existing videos library folder; store relative/fixed artifact names so library copying remains safe. Reuse atomic document locks, image normalization, FFmpeg probing, owned process supervision, and task-drain helpers. Add typed Pydantic contracts and regenerate client types. The existing song/storyboard renderer and OpenRouter provider remain independent: their APIs do not accept this model's video input.

The alternatives considered were a new optional cloud adapter and an experimental local MLX Animate port. Cloud is the approved first implementation; local support needs separately measured quality and lifecycle evidence. No training, voice replacement, multi-person selector, longer videos, model downloads, new packages, queues, or servers are included.

## Verification

Mock provider HTTP and use temporary libraries. Cover explicit confirmations, duplicate/concurrent submit, unknown POST outcomes, GET-only recovery/resume, teardown/cancellation, malformed responses, path/symlink escapes, oversized inputs, corrupt delivered tails, original retention, output publication and audio preservation. Run frontend tests/build, backend discovery, generated-contract drift, strict Python platform checks and desktop tests because backend startup changes. A paid generation requires a configured account and sample; automated tests do not establish perceptual quality or live provider availability.

Sources checked 2026-10-11: [fal model API](https://fal.ai/models/fal-ai/wan/v2.2-14b/animate/replace/api), [pricing](https://fal.ai/models/fal-ai/wan/v2.2-14b/animate/replace), [queue](https://fal.ai/docs/documentation/model-apis/inference/queue), [Wan source](https://github.com/Wan-Video/Wan2.2).
