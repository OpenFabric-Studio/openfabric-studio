# Read-along exports and retained narration in Video

OpenFabric composes presentation around accepted narration. Neither action
regenerates speech, calls a paid API, downloads a model or starts video inference.

## Chapter read-along export

Completed chapters offer a portrait (360×640) or landscape (640×360) MP4 with
reading cards, SRT and WebVTT. The preview includes at most the first 25 seconds
of the same source; the full chapter uses its accepted assembled recording,
including speaker-change and passage pauses. During pauses the previous reading
card stays visible; subtitle cues keep the actual utterance bounds. Video runs at 12 frames per second; audio uses
AAC, so the exported MP4 is an encoded presentation of the retained dry WAV.

Caption boundaries are accepted passage boundaries derived from PCM samples.
There is no forced alignment, word timing, karaoke highlighting or synthetic
word confidence. New narration retains display wording before pronunciation
substitution. Legacy or manually edited takes without a verified mapping use
spoken wording and display a fallback notice; the app does not reverse-engineer
original spelling from a pronunciation dictionary.

Pinned bundled Noto Sans and Noto CJK fonts are used on every platform. Missing
glyphs or a passage that cannot fit at the minimum readable size fail the export
with the original recording preserved. Rasterized text never enters FFmpeg
expressions, command syntax or subtitle paths. Generated narration receives a
visible AI label; silent mock or unknown legacy origins are not presented as
verified generated narration.

Exports have backend-owned persisted metadata and a copied immutable source
under the library's `audiobooks/.readalong` directory (actual root follows the
configured audiobook library). Styling jobs continue when a view closes.
Reopening restores status and download links. Source revision, source hash and
cue mapping are checked before publication. Changed source prevents publication;
completed earlier exports remain independently available subject to current
voice consent. The source manifest binds the MP4 hash to the accepted source,
recorded generation components, font identity and timing/text basis. Output size,
frame rate and audio/video duration are checked and the complete output is decoded
before publication. Downloads verify the artifact against its retained hash.

Cancel drains the owned encoder and preserves the snapshot for an explicit
retry. Startup first recovers exact codec worker receipts, then marks interrupted
read-along jobs failed; it never automatically restarts rendering. Retrying a
saved snapshot requires the current chapter revision, matching hash/mapping,
voice consent and verified codec cleanup. Unsafe symlinks/path traversal and
oversized jobs/documents are rejected. No arbitrary filesystem path is accepted
by the API.

## Use this audio in Video

A completed speech trial or audiobook chapter can be inspected and copied into a
new Video draft. Users explicitly select up to 15 seconds; longer recordings
require a start/end clip. The backend resolves the retained ID, verifies the hash
and current voice consent, and copies sample-bounded PCM without resynthesis.
Trial files without retained provenance/profile ownership cannot use this route;
the existing explicit recording-upload workflow remains available.

The new draft preserves source/clip hashes, the original provenance components
and consent-owning profile IDs through duplication and undo/redo. Later book
edits do not mutate copied PCM. Consent checks still apply to serving, rendering
and exporting that clip. Replacing or clearing its soundtrack clears retained
source ownership in the current draft; historical immutable copies retain theirs.

**The audio is a soundtrack, not lip synchronization.** Video generation remains
silent and starts only when the user chooses Generate. Draft duration is rounded
up to supported two-second shot intervals; the actual recording duration remains
visible. Real recordings already use Video's upload workflow.

## Verification and limits

Behavioral tests cover original/spoken wording, mock-origin honesty, permission
revocation, subtitle escaping, preview clipping, stale source rejection, exact
sample copying, explicit trim requirements, HTTP ID validation, durable recovery
and cancellation of a real owned child. A CPU FFmpeg test produces an MP4 from
synthetic PCM and verifies the source bytes are unchanged. UI tests cover stale
responses, explicit clipping/navigation, persisted exports and hidden-view polling.

This evidence does not establish output on every hardware/platform codec build,
visual support for every writing system or any face/lip-sync model quality.
Chapter-first export deliberately avoids a book-wide two-hour video render as a
default user action. Very long passages may require narration segmentation before
they fit on a reading card.
