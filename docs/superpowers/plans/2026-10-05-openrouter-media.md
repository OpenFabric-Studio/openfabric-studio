# OpenRouter Media Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for exclusive implementation domains, followed by independent spec and code reviews. Steps use checkbox tracking. The approved user instruction is to implement; do not introduce another approval gate or commit until requested.

**Goal:** Make optional cloud video, speech and experimental music usable inside existing studio workflows with honest cost, transfer, recovery and credential behavior.

**Architecture:** Backend-only OpenRouter transport, typed catalog/settings/quote contracts and durable submission receipts. Video and speech integrate through separate domain adapters; music writes ordinary library tracks. Local engines and existing media ownership remain intact.

**Tech Stack:** Existing Python/FastAPI/Pydantic/httpx/SQLite/owned FFmpeg and Vue/TypeScript/generated client contracts. Secure credential persistence uses a reviewed native credential backend if available; environment/session operation always remains possible.

## 1. Provider foundation (exclusive backend provider modules and dependency files)

Files: new `backend/app/openrouter_contracts.py`, `openrouter_settings.py`, `openrouter_catalog.py`, `openrouter_client.py`, `openrouter_requests.py`, `api/routes_openrouter.py` and focused tests. Dependencies change only when secure credential persistence requires them.

- [x] Write failing behavior tests: secret never appears in status/errors/receipts; untrusted Origin rejected; malformed/nonfinite catalog/pricing rejected; unknown model excluded; quote mismatches/staleness refused; ambiguous POST never resubmitted by recovery.
- [x] Run isolated unit tests and record the expected failures before implementation.
- [x] Implement explicit bounded Pydantic domain contracts; safe free connection/model requests; secure/session key handling; model capability normalization; estimates; bounded no-redirect authenticated HTTP calls; durable request records with atomic publication and restart behavior.
- [x] Keep transport methods domain-specific: video submission/poll/download, speech bytes, music audio completion. Verify external field names against official source/live catalog. Publish exact internal interfaces to other workers before they depend on them.
- [x] Verify tests and strict typing. The request ledger must represent `submission_unknown` separately from ordinary failed jobs, and sensitive payloads must not be recorded.

## 2. Cloud video (exclusive video backend and frontend files)

Files: `backend/app/video_contracts.py`, `video_projects.py`, `video_render.py`, `video_jobs.py`, new `video_cloud.py`; affected video APIs/helpers/tests; `frontend/src/views/video/`, `frontend/src/api/videos.ts`, isolated cloud video locale module.

- [x] Write regression tests for loading old local projects, provider-specific capability checks, rejected local adapters on cloud projects, stored remote ID before polling, uncertain submit recovery, verified local media publication and original dialogue soundtrack preservation.
- [x] Observe expected failures, then introduce a discriminated provider configuration and a bounded cloud variant worker using the shared provider foundation.
- [x] Add Local/Cloud selection and only supported duration/resolution/reference controls. Present quote/transfer information before paid generation. Persist provenance and cost in previews/export history.
- [x] Keep shutdown and cancellation owned. Resume polling existing remote jobs; never submit again on recovery. Reject provider content URLs outside the expected origin and do not expose credentials through media players.
- [x] Run video behavioral tests and strict affected-module checks. Send exact lifecycle hooks to the root integrator.

## 3. Cloud speech (exclusive speech/profile/audiobook files)

Files: `backend/app/voice_profile_contracts.py`, `voice_profiles.py`, `speech_clone.py`, `speech_references.py`, relevant audiobook preparation/workflow/API contracts and tests; corresponding voice UI/API files, separate cloud speech locale module.

- [x] Write failing tests for old local profile compatibility, cloud preset voices without fake transcripts, explicit clone-transfer consent, immutable model/voice inputs, no cloud mock success, no automatic uncertain retry and decoded PCM publication through audition/narration/repair.
- [x] Implement typed profile renderer options with a transactional compatible migration. Make built-in voices and reference cloning distinct configurations. Preserve local checkpoint/ref data and live voice consent checks.
- [x] Reuse the current saved audition/book/repair registries. Convert bounded cloud speech output to validated PCM using owned media helpers. Save request/model/cost provenance without secrets.
- [x] Add cloud voice selection, capability-aware fields, transfer disclosure and cost previews to profile/trial/audiobook workflows. Context switches must invalidate late quotes and responses.
- [x] Run focused regression/type tests and send source freeze/interfaces to root.

## 4. Settings, music and shared integration (root ownership)

Files: new `frontend/src/api/openrouter.ts`, `views/settings/ProvidersPanel.vue`, shared quote/status components; `SettingsPage.vue`; new `backend/app/cloud_music.py` and API/tests; `frontend/src/views/music/MusicPage.vue`, `CloudMusicPanel.vue`; `backend/app/main.py`, `work_busy.py`, `resource_admission.py`, generator, CI, locale registration and docs.

- [x] Integrate generated contracts, Providers tab and keyboard/hash navigation. Key fields are write-only; abandoned key input and late connection results are cleared safely.
- [x] Write failing music tests for uncertain submission recovery, valid decoding/library persistence, canceled/late responses and malformed audio. Implement the documented Lyria audio completion path with saved provenance and ordinary track registration.
- [x] Add experimental Cloud music mode with curated live models, prompt/lyrics controls where documented, quote/transfer confirmation, durable jobs and library navigation.
- [x] Wire new routers and startup/recovery/shutdown hooks. Extend strict CI scope and regenerate contracts; never hand-edit generated output.

## 5. Review and verification

- [x] Independent spec review: every approved area has a reachable user flow and honest unavailable states; no imported checkpoint or cloud billing/retention guarantees are implied.
- [x] Independent code review: secrets, SSRF/redirects, finite price arithmetic, stale responses, serial writes, cancellation, interrupted submits, decode bounds and schema migration are covered. Fix demonstrated issues with regression tests.
- [x] Run `npm test` and `npm run build` in frontend; isolated backend unittest discovery; generator `--check`; CI strict platform typing loop; desktop tests for dependency/lifecycle/packaging effects.
- [x] Browser verification on a separate loopback fixture backend: provider setup, cost/transfer gates, cloud shots through export, speech trial recovery and music completion/reload. Narration/audition/repair flows use isolated domain verification. No paid calls or model downloads in tests.
- [x] Review final diff and write usage/verification evidence. Leave changes uncommitted unless the user requests commit/integration.

## Final evidence

Frozen source passes 1,109 backend tests (10 environment/platform skips), 1,020 frontend tests, strict frontend build, generated-contract drift check, strict 138-file Python checks for Linux/macOS/Windows, and desktop tests (77 passed, one Windows skip). An independent review and temporary mocked-provider browser smoke passed. Mixed-rate reel export was measured with real CPU FFmpeg; both cues correlate above 0.98 and original recordings are unchanged. See [usage and verification limits](../../openrouter-media.md). Development verification used no paid calls, native credential-store writes or user-library changes. Subsequent integration and live testing follow the maintainer's instructions.
