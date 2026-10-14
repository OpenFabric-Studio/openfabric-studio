# Cloud polling and frame checks implementation plan

**Goal:** Recover from transient cloud read failures without repeating paid
submissions, and verify exact video frames at overlay and storyboard boundaries.

**Architecture:** Keep retries in the shared OpenRouter JSON client, under its
existing request timeout and owned task registry. Use at most three GET attempts;
retry transient HTTP/network failures after closing the previous response. Keep
POSTs, invalid responses and permanent rejections single-attempt. Accept bounded
HTTP `Retry-After` seconds/dates; stop rather than retry before a server-requested
delay longer than 30 seconds. No API schema, storage or dependency changes.

**Tech stack:** Python, HTTPX MockTransport, unittest, FFmpeg/Pillow CPU fixtures.

- [x] Add transport regressions in `backend/app/openrouter_transport_test.py` for
  transient GET recovery, request identity, response closure, bounded attempts,
  Retry-After parsing, cancellation/shutdown during backoff, total request
  timeout, permanent/malformed responses and single-attempt paid submissions.
  Run the recovery cases before changing production and record the failure.
- [x] Implement the minimum retry loop and bounded delay parser in
  `backend/app/openrouter_client.py`. Run the focused transport regressions and
  editing/cloud/character-swap callers; retain stable local error codes.
- [x] Extend existing real media fixtures in `backend/app/video_render_test.py`
  using decoded frame indices immediately before/at/after overlay and cut
  boundaries in landscape, portrait and square. Preserve inclusive overlay
  endpoints. Save failure-only frame diagnostics outside Git. Check assertions
  with deliberately incorrect rendering supplied through in-process patches.
- [x] Update `docs/openrouter-media.md`, `docs/video-studio.md` and `NOTICE` with
  the actual behavior, limits and inspiration. Do not import upstream assets or
  source, install another renderer, change engines or add video presets here.
- [x] Run the full isolated backend suite, generated-contract drift check, both
  strict CI mypy commands for Linux/macOS/Windows, frontend tests/build and final
  diff checks. All test configuration/library/engine paths must be temporary.
  Review integrated changes independently. Commit/push require an explicit
  maintainer request; deployment remains outside this task.

The first two recommendations are the accepted scope for this implementation.
Controlled benchmarks, new motion presets and scene suggestions remain separate
work; this patch does not claim live-provider or GPU quality/performance evidence.

## Regression evidence

Before implementation, transient HTTP/network polling cases stopped on the first
failure. The final transport module passes 40 tests, including partial-body
discard, response closure, retry exhaustion, unchanged remote identity, one total
deadline and shutdown during backoff. Review exposed an overlength numeric
`Retry-After` fallback that could retry early; its new regression failed with
three requests instead of one before the parser was corrected. Date assertions
now use a fixed clock.

The render module passes 48 tests. In-process mutations for always-visible text,
exclusive end timing, reversed shots and a one-frame-delayed cut each triggered
the intended pixel assertions across all three export aspects. These were test
probes without production edits. Independent final review found no remaining
concrete issue.

## Final verification

- Installed/checked `backend/requirements-test.txt` with uv; dependencies were
  already satisfied, with no package changes.
- Final isolated backend discovery: 1,539 tests, OK with 10 skips, 259.136 seconds.
  Skips cover four optional installed-engine checks and six Windows native process
  checks. Temporary configuration, library, Seed-VC and module paths were used.
- Both strict CI mypy commands passed for `linux`, `darwin` and `win32` paths
  after the review correction; this is typing evidence, not native platform runs.
- Generated-contract `--check` passed; no shared schema or generated file changed.
- Frontend `npm test`: 140 files and 1,256 tests passed. `npm run build`: strict
  type policy, TypeScript and Vite production build passed.
- Final diff reviewed and whitespace checked.

Live paid-provider calls, GPU inference and the unaffected desktop suite were not
run. Long server-requested delays or exhausted retries still end local tracking;
known remote jobs retain their existing manual resume path.
