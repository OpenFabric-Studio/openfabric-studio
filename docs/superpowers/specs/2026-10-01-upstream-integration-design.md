# Upstream integration without fork regressions

The user approved all recommendations from the source comparison of upstream `5a2f4a4` and fork `9391186`. Implement them on `sync/upstream-2026-10-01`, preserving both histories. Resolve the initial merge to the verified fork baseline, then port the reviewed behavior explicitly rather than copying conflicting files wholesale.

## Preservation requirements

Keep immutable originals, independently generated voice versions, exact selected-format playback/download, favorites and filters, durable jobs, measured voice progress, draggable generation panels, existing settings/library migration, voice preparation/build/comparison and saved video workflows. No real library/configuration or model changes during tests. Keep all header routes and the fork's identity/attribution. No model upgrades, dependencies or destructive migrations.

## Playback and accessibility

Anchor the transport to the actual scheduled audio start, stop at the project clock even with muted/empty sources, schedule successive loop passes at audio boundaries and preserve channels by lane ID. Retain cancellation/generation tokens and complete node cleanup. Bound loops and trimming, including stretched clips; tab stalls may require rescheduling rather than promising continuous background playback.

Add keyboard loop/fade/trim controls, visible focus and readable channel labels. Dialogs move and restore focus, trap Tab, close with Escape and block editor shortcuts while active. Recheck state after asynchronous focus changes and support nested dialogs without stealing focus.

## Library and downloads

Keep title and generation style separate for YuE, including rename/reload/reuse. Paginate completed matching jobs after sorting/favorites/date filtering, remember validated page sizes and reset/clamp pages appropriately. Keep playing cards mounted and active jobs visible across pages. Preserve compact/card modes and batch semantics.

Retain version-aware audio controls inside cleaner cards; show style chips, structured lyrics and copy buttons. Require confirmation before deletion without hiding failures. Add recent-track playback to Home with original/clone/format selection. Add global help and license/attribution access without replacing navigation.

Store artist settings on the backend with bounded typed schemas. Add tagged downloads for the chosen original/voice source or chosen export, using FFmpeg stream copy so tagging never re-encodes or modifies stored audio. Constrain paths, own subprocess cancellation/timeouts, clean temporary files on every failure and response completion, and return structured errors. Integrate artist into Settings and generated contracts. A download request captures exact IDs and must never substitute another version or format. Preserve the fork's existing playback-refresh recovery when a selected file disappears: the player visibly selects an available source and updates its format/download labels. Preserve existing provenance and include voice information where available.

## Evidence

Regression tests cover scheduling, loop boundaries, channel ownership, dialog races, keyboard bounds, YuE rename/reload/reuse, favorites/pagination/deletion/playing-card retention, existing voice/version/progress controls, selected-file tagged downloads, unchanged source bytes, malformed input and subprocess cleanup. Run full frontend/backend/desktop suites, generated-contract drift and strict Python platform checks. Review the integrated diff before updating the normal checkout. Real GPU/perceptual or installation quality is outside what these tests establish.
