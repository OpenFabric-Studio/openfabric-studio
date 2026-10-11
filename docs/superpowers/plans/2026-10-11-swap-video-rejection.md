# Diagnose rejected character-video submissions

Verified: the retained video receipt has HTTP 400, no remote ID, and a successful saved character frame. The error body was discarded. Current OpenRouter and Runway schemas do not establish a payload mismatch; absence from the Generations tab does not establish that no HTTP request reached OpenRouter.

Implement the smallest useful recovery: bounded, redacted backend rejection diagnostics and a fresh video quote using the existing frame only after an owned receipt proves a definitive rejection without a remote ID. Preserve the previous receipt. New submission still requires a fresh estimate and explicit transfer/cost/rights review. Never retry unknown outcomes or submit automatically.

Scope: character-swap schema/service/generated client contract, existing video-review UI/translations/tests, OpenRouter transport diagnostics/tests, and video documentation. No provider switch, speculative payload change, paid request, existing media deletion, or historical receipt rewrite.

Verification: regressions first; include uncertain/corrupt/foreign receipts, changed inputs, stale revisions, reload, cancellation, diagnostic redaction and bounded/erroring bodies. Run required frontend/backend/contracts and strict Python platform checks. Rebuild/restart the idle loopback app and inspect the review controls without approving generation. Successful paid video generation remains unverified until the actual rejection explanation is available and acceptance is demonstrated.

## Implemented and verified

- The fresh-quote control reuses the reviewed frame and verifies the existing receipt before changing the saved job to prepared. Previous billing receipts remain unchanged; no submission occurs on quote or restart.
- Received rejection status survives cancellation both in transport and the character-swap service. Regression first reproduced a confirmed HTTP 400 incorrectly becoming submission unknown, then passed after the fix. Existing genuinely uncertain cancellation behavior still passes.
- Private transport logs bound reads to 16 KiB/five seconds and redact known request/credential echoes, raw and JSON-escaped scalar spellings, URI/token patterns and control characters. Malformed, oversized and interrupted diagnostics cannot change the confirmed HTTP outcome. Arbitrarily transformed excerpts cannot be guaranteed; raw error bodies/metadata are not logged or returned through the API.
- Frontend: 1,182 tests in 138 files passed; strict production build passed. Backend: full isolated suite ran 1,476 tests with ten skips; the latest diagnostic refinements then passed all 105 affected OpenRouter/character-swap tests. Both exact CI mypy scopes (6 feature/163 core files) passed again for Linux, macOS and Windows. Generated-contract drift and diff whitespace checks passed. Desktop startup/layout code was not changed.
- The actual idle app was rebuilt/restarted on loopback port 9000. The retained eight-second job is prepared with a fresh $2.24 estimate and unchecked transfer/rights approvals. Only its quote/status metadata changed: SHA-256 comparisons confirmed all 21 other retained media/receipt files remained unchanged. Proof: `/tmp/openfabric-swap-video-review.png`.

Logs: `/tmp/openfabric-requote-{frontend-full,build,backend-final,final-focused,types-last}.log`. No live paid request, model/provider switch, speculative payload-field change, user-media deletion, historical receipt rewrite, commit or push was performed. The exact original HTTP 400 explanation and successful cloud video output remain unverified; the user can submit a separately approved attempt in the UI to collect the rejection explanation.

## Confirmed live transport cause

Subsequent user submissions produced retained diagnostic messages: `Invalid reference URL: input_references[0].video_url.url: Only HTTPS URLs are allowed`. The latest receipt remains failed with HTTP 400 and no remote ID; the saved reviewed frame is present. This identifies the inline video reference as the rejected field, rather than a missing API key or local media render.

The official OpenRouter Files guide excludes video and confines uploaded files to sandboxes, with no downloadable uploaded-file URL. It cannot supply the provider-fetchable HTTPS input. The user subsequently chose to retain OpenRouter for AI and keep clips on the Mac through a clip-only HTTPS tunnel. The approved implementation is tracked in [local clip sharing](2026-10-11-private-swap-clip-hosting.md); no paid attempt is authorized without the existing per-stage review.
