# Optional cloud video

Select **OpenRouter cloud** in a video's Direction step after configuring the provider in Settings. The model and native picture size come from the current curated catalog. Local LTX settings remain stored separately. A local character LoRA must be explicitly detached before choosing cloud; its checkpoint and still remain in the library, and Undo restores the binding.

In Preview, select one storyboard shot and a supported provider duration long enough to cover it. Review the estimate and the listed transfer, then approve generation. Each variant requires its own single-use quote. If a provider's minimum duration exceeds the saved slot, explicitly approve retaining the first portion of the native clip; the full provider clip is billed. Source cast cues and their timing are unchanged. Estimates are not billing caps; use an OpenRouter key spending limit.

The unquoted saved-voice action lists local voices only. Create a cloud speech trial with its own approval, then upload the completed audio, or use completed cloud audiobook passages in a dialogue reel. No paid speech request is hidden inside video setup.

Dialogue reels can combine accepted local and cloud passages with different PCM rates. The backend normalizes owned copies to a common format and preserves source recordings, hashes and selected bounds. Preparation remains hidden until publication; shutdown drains its media processes and startup removes only verified abandoned staging.

Only the combined direction/shot prompt and selected reference still are transferred. Song/cast audio and local adapters are never sent. Cloud generation requests silent pictures. Delivered audio is removed, native geometry and full delivered video are checked, and the approved portion conforms to 24 fps before entering the existing immutable variant/approval workflow. Export assembles approved shots locally and uses the original song or cast soundtrack. It does not require local LTX models or a cloud credential after successful picture generation.

The backend persists intent before the single paid POST and the remote job ID before polling. A normal restart follows the saved job with GET requests only. A checked local clip interrupted during optional poster work is adopted without accessing the provider again. A failed poll can be resumed manually; it does not submit another paid request. An interrupted/ambiguous POST without a verified remote ID is shown as unknown. Check OpenRouter activity before deliberately starting a fresh quote; the previous request may have been billed. Arbitrary remote-job attachment is unavailable because job ownership cannot safely be proved.

**Stop tracking** cancels and drains local network/media work. No remote cancellation endpoint or refund guarantee is claimed. Explicitly stopped jobs are not automatically resumed at startup. A stopped known provider job can be followed again using its persisted ID. Receipt provenance retains the model, remote ID, requested/native/final durations, output hash and estimated/provider-reported costs; final cost may be absent.

Video is not eligible for zero data retention, and supported seeds do not guarantee deterministic output. Initial catalog curation includes Veo 3.1 and Veo 3.1 Fast; availability, durations, geometry, references and prices are validated against the fresh provider catalog rather than assumed from those names.

## Verification boundary

Automated tests use temporary libraries, fake credentials and mocked provider HTTP. They exercise durable intent, unknown submission outcomes, GET-only recovery, cancellation before worker startup, cross-origin rejection, and real CPU FFmpeg download conformance/approval/export. The original cast soundtrack is checked against exported PCM. These checks prove integration and ownership behavior; they do not establish remote service availability or generated picture quality. No paid generation was used for this implementation.

Official API reference: [OpenRouter video generation](https://openrouter.ai/docs/guides/overview/multimodal/video-generation). Content is fetched through the backend from OpenRouter's authenticated fixed-origin endpoint; credentials and provider content URLs are never exposed to the browser.
