# Reviewed upstream integration, 2026-10-01

This fork adapts the reviewed upstream changes through
[`5a2f4a4`](https://github.com/inikolax/remiqora/commit/5a2f4a49d97f470c526f67e0859d593533f5c532),
starting from fork `9391186`, on `sync/upstream-2026-10-01`.
The merge retains both histories. Conflicting implementations were adapted
explicitly around the fork's version catalogs and job ownership rather than
replacing files with upstream copies.

## What to try

- The editor keeps audio channels attached to lane IDs and anchors its playhead
  to the actual scheduled start. Loops queue their next audio/MIDI pass at the
  audio boundary. Silent projects complete using the transport clock.
- Tab to loop, trim and fade handles. Arrow keys change their position; Alt
  selects fine steps, Shift larger steps, and Home/End their bounds. Dialogs
  trap Tab, restore focus on close, and block editor shortcuts while open.
- Track feeds paginate completed matching cards, with saved sizes of 5, 10,
  25 or 50. Favorites, dates and sorting apply before pagination. Playing cards
  remain mounted across pages and filters; active conversions/exports remain
  visible after reload and show their progress in compact view too.
- Cards have style tags, structured lyric sections, copy controls, full-title
  editing and a two-click deletion confirmation. A failed deletion leaves the
  card visible. YuE track titles and generation styles are separate, including
  renamed historical tracks whose original style remains in saved parameters.
- Home shows the five newest saved tracks with original, voice and format
  controls. The header's Help button explains the workflows. The footer links
  project attribution and checked-in license notices.
- In Settings, save an artist name for future metadata downloads. Blank clears
  the artist tag, including an inherited source artist. Audio export profiles
  and data-folder migration stay in the existing settings sections.
- Select the desired original, cloned voice and format, then choose **Download
  with metadata**. The existing plain download remains available beside it.

## Download behavior

The metadata download captures the exact version/export IDs. Missing or modified
files produce a structured error instead of downloading a different source.
Metadata is written to a temporary copy using FFmpeg stream copy; the stored
audio and its export settings do not change. The tags include title, artist,
lyrics, generation/voice provenance and creation time where available. Genre
guessing uses conservative matches in the saved style/prompt; it is a heuristic,
not audio classification. An explicit saved genre takes precedence.

MP3 and FLAC retain richer metadata than WAV's limited RIFF INFO vocabulary.
WAV carries provenance in its comment as well. Complete UTF-8 metadata uses an
escaped FFmetadata file, avoiding command-line limits. Metadata above 1 MiB
fails explicitly. Cancellation, HTTP disconnect, response failures and backend
shutdown drain owned subprocesses and remove temporary copies.

The existing player's recovery when a selected file disappears is retained:
it visibly selects an available source and updates the format and download
labels. This happens before a new download selection; it never changes the
captured source of an already requested download.

## Preservation and verification

Original/clone comparison, additional voices and exports, exact-format playback,
favorites, batch controls and measured queue/stage/elapsed/ETA displays have
regression coverage. Voice preparation/training and saved video implementations,
the draggable generator, library migration, database schema, desktop setup,
fork identity and MIT notices were preserved.

Verification runs use temporary configuration, library and Seed-VC paths.
Checks include the complete frontend/backend/desktop suites, strict frontend
build, generated-contract drift, the CI strict-mypy loop for all 50 checked
modules across Linux/macOS/Windows typing paths, and workflow validation.
Real FFmpeg tests check audio sample equivalence, unchanged source bytes,
Unicode/long lyrics, inherited metadata, exact selected files and process cleanup.
Independent reviews identified and corrected artist inheritance, command-line
length, retained-player transitions, delayed editor activation, stale YuE
history mutations and accessibility edge cases.

A browser smoke test used the production build with synthetic tracks and a
separate loopback fixture server. It exercised Home voice playback, Help focus,
artist saving, card pagination, favorites and generator floating/hiding without
accessing the real library or starting models.

These checks do not establish GPU inference quality, continuous playback in
heavily throttled background tabs, native Windows behavior or installation
support. Loops queue one pass ahead; after a sufficiently long tab stall the
transport reschedules. Model terms must be checked for the exact installed
weights; metadata does not invent historical checkpoint identity or grant rights.

Restart the backend when generation/training jobs are idle to load the new
settings, activity and metadata routes. No library migration or model download
is required for this integration.

## Local verification results

| Check | Result |
| --- | --- |
| Frontend behavioral tests | 529 passed, 67 files |
| Frontend strict types and production build | Passed |
| Backend regression suite | 495 ran; 491 passed, 4 optional vendor-engine tests skipped |
| Desktop unit tests | 27 passed |
| Generated-contract drift | Passed |
| CI strict Python module scope | 50 modules passed for Linux, macOS and Windows typing paths |
| Workflow validation and diff whitespace | Passed |
| Production-build browser smoke | Passed on isolated synthetic fixtures; no page errors detected |

Native engine/GPU and Windows installation behavior remain unverified.
