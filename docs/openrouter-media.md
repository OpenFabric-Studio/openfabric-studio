# Optional OpenRouter media

OpenFabric keeps its local engines and adds a separate cloud provider. Cloud
models do not require a local GPU or a model checkout. Speech normalization and
video conformance/export still use FFmpeg; Settings → Setup & modules installs
and checks the media tools.

## Set up

1. Open **Settings → Cloud providers** (or the cloud icon in the header).
2. Enter your own OpenRouter API key. The key is write-only: app responses never
   return it, and browser storage never stores it.
3. Choose whether to remember it in the native credential store. Supported
   backends are macOS Keychain, Windows Credential Manager, and Linux Secret
   Service. Saving to a missing/locked store falls back to backend session memory. The UI
   reports the actual source. `OPENROUTER_API_KEY` is another startup option.
   A newly entered key wins for the current process; on restart an environment
   key takes precedence over a remembered key.
   Removal of a known remembered key fails if its credential store is unavailable;
   unlock the store and try again. The app does not report that key as removed.
4. Enable OpenRouter, save an estimate limit, then check the connection and
   refresh models. These actions make no generation request. The header's
   “configured” state means a key exists; it does not certify the connection.
5. Set a spending limit on the key in OpenRouter. The app's estimate ceiling
   rejects an expensive quote; it is **not a provider billing cap**.

The catalog is curated and expires after 15 minutes. A quote expires after five
minutes and binds the exact inputs and advertised model capabilities/pricing.
Refresh models and review a new estimate when either becomes stale.

## Use it

- **Music → Cloud music:** select Lyria, describe the song, review the estimate,
  approve the transfer, and generate. Results become normal library tracks,
  usable by the editor and video workflow. This integration is experimental;
  exact quality, lyrics and duration are not guaranteed. Lyria token rates can
  show zero despite a per-generation fee: the app uses the reviewed published
  song rate and labels it an estimate.
- **Voice Clone → Speech:** create a provider voice preset without a recording,
  or configure a supported model to clone an existing consent-backed recording.
  Cloning requires separate permission to transfer that recording and transcript.
  Trials, cast auditions, audiobook narration and repairs require their own cost
  review. Whole-book estimates use the actual spoken sections after pronunciation
  changes and the chosen speaker profiles. Snapshots retain provider, model,
  voice/reference identity and receipts. Local mock mode does not simulate cloud
  output. Supported language choices are conservative; availability is not a
  certification of pronunciation or voice quality.
  Completed cloud trials appear under **Recent paid speech trials**, including
  after reload. Selecting or downloading a saved trial makes no generation
  request.
- **Video:** choose OpenRouter as the picture engine, save the storyboard and
  quote one shot. Controls use advertised native sizes/durations. If the provider
  clip is longer than the timeline slot, explicitly approve trimming: the entire
  provider clip is billed. Cloud pictures are silent; existing song/cast PCM stays
  local and is attached on export. Local character LoRA weights cannot be used
  with cloud video. Generated shots enter the usual preview, approval and export
  workflow. Seed support does not promise deterministic output. Direct video
  talking-line synthesis remains local; download an approved cloud speech trial
  and import it as a soundtrack clip when needed.

Text/prompts and approved references go to OpenRouter and its selected provider.
Cloud voice presets send text only. Reference cloning sends audio and transcript.
Video requires remote retention and is not eligible for zero data retention.
Local model weights are never uploaded. Keep the app on loopback: it has no
built-in user authentication.

## Recovery and charges

The backend writes submission intent before a paid POST and records a returned
remote ID before polling. A quote cannot create multiple paid requests. Timeouts,
malformed paid responses and interrupted streams can mean the provider already
accepted a request; these become **submission outcome unknown** and are never
submitted again automatically. Check OpenRouter activity before deliberately
requesting another generation. A new approved redo is a new paid request.

Video with a known remote ID can resume GET-only tracking/download. Stopping
tracking does not establish remote cancellation, stop billing or promise a
refund. The application does not attach arbitrary remote IDs it cannot verify.
Music can retry saving an already validated download without generating again;
its recorded digest must still match. Completed media survives navigation and
reload. Corrupt retained records stay on disk; history warns rather than silently
replacing them or resending their requests.

Final usage/cost is displayed when the provider reports it; estimates are not
relabeled as actual charges. Configure provider-side key limits, especially for
multi-request narration; the app does not enforce the final billed total.

## Validation limits and primary documentation

Development uses temporary libraries, fake credentials and mocked HTTP, with
real CPU FFmpeg decode/conformance checks. No real key, paid request, native
credential-store write, GPU download or user-library migration is required by
unit tests. Live billed quality/compatibility and native credential stores on
all operating systems need separate maintainer smoke evidence before being
claimed as end-to-end verified.

### Implementation verification — 5 October 2026

- Frontend: 1,020 tests across 126 files; strict TypeScript/policy build passed.
- Backend: isolated full discovery ran 1,109 tests successfully, with 10 expected
  environment/platform skips. New provider/media tests use mocked HTTP and native
  credential stores.
- Contracts: generated artifacts match backend schemas. Strict Python checks
  passed for the 138-file CI scope on Linux, macOS and Windows typing paths.
- Desktop: 77 passed, one Windows-specific skip. The hashed base-wheel install
  dry run passed on macOS.
- Separate loopback browser fixture: provider setup/free connection check, music
  generation and reload, speech preset/trial generation and saved-trial reload,
  video quote/generation/approval/export, and mobile status keyboard visibility.
  Reload made no additional paid submission. Narration/audition/repair behavior
  is covered by isolated domain tests rather than that browser smoke.
- Real CPU FFmpeg tests preserve mixed 16 kHz/local and 24 kHz/cloud cue audio
  through reel export, with waveform correlation above 0.98. Dry sources remain
  byte-for-byte unchanged. Staged normalization cancellation and verified-worker
  recovery tests passed.
- Independent review covered providers/music/UI/contracts and the final dialogue
  lifecycle delta. Demonstrated defects received regressions, including ambiguous
  submit recovery, long speech sections, mixed PCM, unsafe metadata and unavailable
  credential-store removal. Final diff checks passed.

These checks do not certify live model quality, current provider availability,
billed behavior, native credential stores or clean installations on every OS.

Provider documentation: [speech](https://openrouter.ai/docs/guides/overview/multimodal/tts),
[video](https://openrouter.ai/docs/guides/overview/multimodal/video-generation),
[audio](https://openrouter.ai/docs/guides/overview/multimodal/audio),
[privacy](https://openrouter.ai/privacy/),
[Lyria Clip](https://openrouter.ai/google/lyria-3-clip-preview),
[Lyria Pro](https://openrouter.ai/google/lyria-3-pro-preview).
