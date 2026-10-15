# Stable Audio 3 LoRA interface simplification

**Goal:** Put Stable Audio training first, with a short path from naming an adapter to choosing captioned tracks and starting a run.

**Design:** One page with three labeled groups: Name your LoRA, Add your music, Train. Keep base-model choice visible because weights and adapters must match it. Replace the Stable-only capability table with a compact readiness message and an actionable setup link when blocked. Keep job history/cancellation/export visible after the training form. Put steps/rank/learning rate/seed behind the existing `CollapsibleDetails` component, retaining all current defaults and bounds. Put existing adapter import/library tools below training in a closed disclosure; YuE retains expanded adapter tools and HeartMuLa retains its capability explanation.

**Scope:** Frontend layout, local readiness/disabled-button explanations and translated copy only. Existing API contracts, training requests, runtime validation, reconnect/polling, cancellation, saved history and file handling remain intact. No presets, wizard state, automatic training/import, new dependencies, backend/native edits, commits or pushes.

- [x] Add failing tests for training-first order, collapsed advanced/import controls, preserved default/custom requests, clear missing-input/setup blockers, and shared-model behavior.
- [x] Reorder `MusicLoraPage.vue`, reuse `CollapsibleDetails`, add concise readiness/input feedback and `musicLora.ts` copy.
- [x] Run focused LoRA page/workspace regressions, `npm test`, `npm run build` and generated-contract drift check.
- [x] Review final diff and verify the real Stable Audio page in the browser without submitting work or touching training files.

## Evidence

The initial focused regression run failed on the six expected missing-layout/readiness behaviors. Independent review found that an absent capability response could be mislabeled as unsupported training; a further failing test confirmed this, and the UI now distinguishes unknown readiness from confirmed missing/unsupported setup and offers the appropriate Refresh/setup action.

Final verification: all 165 frontend test files / 1,537 tests passed, strict TypeScript and production build passed, generated-contract drift check and final diff whitespace check passed. Existing lifecycle cases cover uncertain submission, reconnecting, polling teardown, cancellation and download availability. Backend/native code and shared API models did not change, so their suites and desktop tests were not repeated.

Browser verification at `/music/lora/stable-audio3` confirmed all three groups, expanded advanced controls and adapter import/library tools, then restored both disclosures to closed. No training, adapter import, upload or setup installation was submitted. The live runtime reports installation/verification is needed; GPU training was not evaluated. Screenshot: `/tmp/openfabric-stable-training-simple.png`. The updated page is left open for review. No commit or push.

**Verification limits:** UI testing does not establish GPU training quality or runtime support. Model training and backend/desktop suites need not be repeated for frontend-only layout changes; the existing API lifecycle tests must continue to pass.
