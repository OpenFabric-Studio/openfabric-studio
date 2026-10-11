# Local AVI scene cutter

The user approved MP4 scene exports up to ten minutes plus direct use of 2–8 second cuts for character swapping. This extends the current Video studio and preserves the OpenRouter two-stage approval flow.

## Workflow

Add **Scene cutter** beside Character swap. On macOS, **Open local movie** uses a native picker to capture a specific AVI/MP4/MOV/WebM file. Large movies are read in place without copying or converting the full movie. An upload fallback retains an owned original, bounded to 2 GiB. Native sources are bounded to 8 GiB, six hours and 4096 pixels per side. Source selection, preview and cutting use local FFmpeg only.

After probing the source, display its duration and a short browser-compatible preview. Seek with a whole-movie timeline and a time input; the backend converts only a short window around that position with fast accurate seeking. Mark in/out from playback or enter times. Validate a positive selected range up to 600 seconds within the source. Save an MP4 scene with audio as a durable owned cut. Show cutting/failure/cancellation honestly. Completed cuts survive reload and can be downloaded.

For ready cuts of at least two seconds and at most 100 MiB, **Use for character swap** opens the existing panel with the retained cut selected. The user selects a character image and a 2–8 second whole-number subsection, defaulting to two seconds. A backend multipart route resolves the owned cut and prepares the swap locally without a browser download/re-upload. Each cloud operation retains its separate quote and approvals. Oversized cuts remain downloadable; the interface asks for a shorter cut before swapping.

## Boundaries

No raw filesystem path is accepted from HTTP. The native dialog selects the source on the backend; stored private source metadata is validated, contains a source file signature, and is never returned publicly. Operations reading the original check that source identity is unchanged; retained cuts remain usable independently. Serving routes expose only owned preview/cut MP4 files, under validated identifiers with no symlink escapes. Explicit demuxers and the file/pipe protocol allowlist prevent AVI-labelled playlists or network access. A missing/moved/changed native source fails safely and never changes the original. Display geometry accounts for sample aspect ratio and right-angle rotation; arbitrary rotation is rejected explicitly.

Own native-picker, import, preview and cut tasks/processes; cancel and await descendants, retain unresolved ownership, and drain on shutdown. Publish source/cut metadata and completed assets atomically. Keep a small bounded number of previews. Recovery adopts checked completed cuts or records interruption; it never repeats a cloud operation. Single backend worker and loopback binding remain the supported setup.

Reuse existing FFmpeg/process supervision, atomic metadata, generated Pydantic contracts, design styles, polling, playback coordination and structured errors. No new dependencies, cloud storage, scene detection or full movie transcoding.

## Verification

Real synthetic AVI fixtures cover late seeking, audio/duration, unsupported media, unchanged originals, range boundaries, source mutation, cancellation, retained cuts and reload/recovery. Mock the native picker. API tests exercise serving roots and the swap bridge. UI tests cover timeline/in/out state, stale responses, readiness, reload, cut polling/cancellation and selecting a ready cut. Run required frontend/backend/contracts/strict platform typing/desktop checks. No user movies or paid generation are used in automated tests.
