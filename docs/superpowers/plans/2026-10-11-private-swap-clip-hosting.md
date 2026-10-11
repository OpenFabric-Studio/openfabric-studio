# Local swap clip HTTPS sharing implementation plan

> For agentic workers: execute the approved scope in this session with exclusive file ownership. Preserve existing uncommitted changes; do not commit, push, create accounts, or make paid requests automatically.

**Goal:** Complete reviewed character swaps through OpenRouter using an approved clip served from the user's Mac over HTTPS.

**Architecture:** A separate loopback FastAPI server serves only unguessable GET/HEAD clip links through an owned Cloudflare Quick Tunnel. Each approved silent clip has a retained local copy and durable grant tied to its OpenRouter receipt and SHA256. Quotes and billing receipts stay bound to the local clip and reviewed frame, while the hosted URL is a transport detail.

**Tech stack:** Existing Python/httpx/Pydantic/FastAPI/uvicorn, native process supervisor, Vue/TypeScript and generated API schemas; external cloudflared CLI. No storage account or additional AI provider.

## Approved decision and evidence

The user chose keeping clips on their Mac with an HTTPS tunnel. This supersedes the researched Supabase option; no Supabase account, credentials or media upload was created.

- The observed OpenRouter rejection requires an HTTPS input_references[0].video_url.url; the existing inline video URI cannot work.
- Runway needs the same clip URL to support GET and HEAD, correct MIME type/length, no redirects, and at most 2,048 characters: https://docs.dev.runwayml.com/assets/inputs/.
- Cloudflare Quick Tunnels provide a temporary HTTPS hostname without an account/domain, have no uptime guarantee, and change hostname each launch: https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/.
- cloudflared 2026.10.0 is installed on this Mac through the official Homebrew formula. Its inspected help supports tunnel --config, --no-autoupdate, --url, --metrics and --management-diagnostics.
- An explicit owned YAML config containing {} and a scrubbed environment are required: inherited ingress can override --url, and TUNNEL_NAME can select a different tunnel. Never forward the main app server, expose docs/listings, log tokens or inherit credentials/debug logging.

## File ownership and tasks

- [x] Hosting backend owner: new backend/app/clip_host_contracts.py, clip_host_settings.py, clip_hosting.py, api/routes_clip_host.py and behavioral tests. Public settings contain only enabled; status adds configured, binary_available, tunnel_active and active_clips. Internal HostedClip contains receipt_id, source_sha256, a constrained HTTPS URL, and timezone-aware expires_at.
- [x] Test before implementation: reject missing binary/disabled sharing; verify isolated config/environment; test loopback GET/HEAD/range, path traversal/symlinks/oversize, wrong tokens and every main API/docs path. Mock public HTTP and cloudflared boundaries; exercise cancellation, process exit, restart, exact grant cleanup and a held request.
- [x] Persist a global owned clip copy/grant before publication. Only a video-step approval can start a tunnel or expose a grant. Do not re-expose old grants on restart; an old Quick Tunnel URL cannot be restored. Own and drain the in-process server, subprocess descendants, output parser, setup requests and maintenance.
- [x] Root: register CLIP_HOST_CLIENT_MODELS and regenerate frontend/src/api/generated.ts; add clip_host_configured to CharacterSwapReadiness. Register API routes and startup/shutdown hooks and expand the existing strict mypy scope.
- [x] Root transport regression: require HostedClip in OpenRouterClient.submit_video_edit; validate receipt/SHA/expiry before the paid intent, send its HTTPS URL on the wire, and preserve local quote fingerprint. Reject mismatches without a POST or paid intent.
- [x] Root workflow regression: require sharing readiness before either estimate/charge, never share during preparation/quote/frame editing, show sharing_clip during the approved video worker, and re-check inputs. A sharing error or cancellation before the paid intent can get a fresh estimate without repeating the frame. A paid POST is never replayed automatically.
- [x] Root cleanup: release grants only after a confirmed provider failure/completion or before any paid POST. Keep accepted/unknown grants when local tracking stops or the saved job is deleted. Remove expired local shares after 24 hours; shutdown always closes the tunnel. Retain every billing receipt.
- [x] Frontend owner: new frontend/src/api/clipHost.ts, views/settings/ClipHostPanel.vue/tests and English locale registration; embed in existing provider settings. Explain setup, clip-only exposure, anyone-with-link access, keep-Mac/app-online requirement, and that disabling prevents new links while active jobs continue. Guard stale responses and clear approvals on changed estimates. Include active share count and local setup check without starting a tunnel.
- [x] Root docs and verification: update docs/video-studio.md and docs/openrouter-video.md with actual setup/limitations; run frontend tests/build, isolated backend tests, generated contract check, all three strict mypy platform paths and affected desktop tests. Review final diff and obtain a UI screenshot without live user-media transfer.

## Regression invariants

```python
parameters = catalog.video_edit_parameters(body)
assert parameters.source_sha256 == hosted.source_sha256
assert hosted.receipt_id == receipt.id
assert sent['input_references'][0]['video_url']['url'] == hosted.url
assert ledger.get(receipt.id).request_fingerprint == catalog.digest(parameters)
```

All tests use temporary configuration, library, module root and SEED_VC_DIR. No unit test starts a real tunnel or makes a paid request.

## Live handoff and limits

Enable clip sharing in provider settings and reuse the existing saved reviewed frame. Request a fresh video estimate, then approve the transfer and video charge in the UI. The Mac and app must remain online until the provider has finished fetching the clip. App exit/restart closes the old URL; GET-only polling can retrieve an already completed output but cannot repair the provider's old input URL. Quick Tunnels are an experimental personal-testing transport without an uptime guarantee. Until a reviewed live generation succeeds, report mocked transport/media evidence separately from actual provider success.

## Verification completed (2026-10-11)

- Frontend: `npm test` passed 1,213 tests; `npm run build` passed strict TypeScript and the production build. The existing 500 kB bundle warning remains.
- Backend: the full isolated regression suite passed 1,513 tests, with 10 platform/optional-engine skips. No user library or GPU model was used.
- Types/contracts: both CI mypy scopes passed for Linux, macOS and Windows typing paths (10 feature files and 163 core files per target); generated contract drift and diff whitespace checks passed. These typing paths are not native Linux/Windows runtime evidence.
- Desktop: 85 tests passed, one platform-specific test skipped.
- Live transport: production code shared a synthetic 27,911-byte silent clip through a real Cloudflare Quick Tunnel. External HTTPS HEAD/GET verified exact bytes, video/mp4 and no-store headers. Release stopped the relay, reaped its worker and retained the original. No OpenRouter AI request occurred.
- Live handoff: restarted the loopback app on port 9000, enabled the approved clip-sharing setup, and used the UI to request a free video estimate for the existing reviewed frame. All 23 existing media/billing-receipt hashes stayed unchanged; no tunnel or active share remained. UI settings checked ready without opening a tunnel.
- Screenshots: `/tmp/openfabric-tunnel-video-review.png` and `/tmp/openfabric-tunnel-settings.png`. The video review retained separate unchecked transfer/cost and rights approvals.

Paid OpenRouter/Runway generation and visual quality remain for the user's separately approved UI test. Cloudflare Quick Tunnel hostname readiness can take time; the backend retries only public clip reads for up to 180 seconds and never repeats an AI POST automatically.
