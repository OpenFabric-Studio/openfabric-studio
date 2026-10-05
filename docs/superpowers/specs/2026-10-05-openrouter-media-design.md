# Optional OpenRouter media provider

The approved integration adds cloud video, speech and experimental music while preserving local engines, private libraries and the existing review/publication workflows. Users supply their own OpenRouter key. No paid calls or real user-library migrations occur during development.

## Provider boundary

The backend owns credentials, validated model capabilities, quotes, outbound requests and durable request receipts. Settings adds a Providers tab with key setup, a free connection check, model refresh, key spending information and an estimated-cost ceiling. Secrets are never returned by GET routes, stored in browser storage, logged or forwarded to untrusted download hosts. Native credential storage is preferred; session/environment credentials remain available when secure persistence is unavailable. There is no plaintext persistence fallback.

Discovery uses the dedicated video model endpoint and modality-filtered audio catalogs. A small supported model selection is intersected with live provider capabilities; unsupported controls and unsupported endpoints are rejected before a paid request. Prices remain estimates and are labeled accordingly. Users configure spending controls on their OpenRouter key; the studio does not claim to cap charges it cannot control.

Every paid submission records its intent before the network call. A timeout or crash with an unknown submission outcome becomes a recoverable, explicitly uncertain state, never an automatic paid retry. Remote video IDs are persisted before polling and outputs are downloaded, decoded and saved locally before publication. Polling uses loopback-compatible backend requests, not public webhooks. Cancellation must distinguish stopping local tracking from confirmed remote cancellation and never promise a refund.

## Video

Video projects can select Local or OpenRouter with distinct provider parameters. Existing LTX settings and adapters stay local. Supported remote duration, geometry, reference input and audio controls are validated from the selected model. Cloud variants feed existing previews, approvals, history and export. Dialogue reels retain accepted local cast audio; cloud picture audio is disabled or removed and exports preserve the soundtrack. Model/provider/settings/cost/remote job provenance is persisted. Existing local projects load unchanged.

## Speech

Speech profiles select local GPT-SoVITS or a supported OpenRouter speech model, with a provider voice or separately consented sample-based cloning. Local trained checkpoints are not sent. Reference recordings/transcripts are sent only for an explicitly configured cloud clone. Cloud built-in voices do not require a fabricated reference transcript. Immutable render snapshots include provider/model/voice/reference identity. Auditions, saved narration and fresh passage repair use the same renderer and continue to produce validated local PCM for atomic publication. The UI shows transfer details and estimates before generation; whole-book generation must make its cloud use visible.

## Music

Music adds an experimental OpenRouter option for supported Lyria models. Requests use the documented audio-output API rather than treating all audio models as music models. Saved jobs expose status, uncertain submissions and safe retry choices. Completed decoded audio becomes a normal library track with cloud provenance and reported cost; a browser reload does not lose completed results.

## Acceptance

Meaningful tests cover secret leakage, secure-store failure, origin rejection, catalog drift, price validation, no paid retry after ambiguity, cancellation/reload/recovery, malicious provider URLs, bounded media decode, stale UI actions and preservation of existing local workflows. Required frontend/backend/contracts/strict typing/desktop checks run in isolated environments. Browser verification uses fixtures and mocked HTTP boundaries. Real cloud quality, provider cancellation/refund semantics and billed end-to-end operation remain unverified until a configured-key smoke test.
