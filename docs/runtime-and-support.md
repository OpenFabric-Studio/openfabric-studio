# Runtime controls and support reports

Settings → **Runtime & support** manages the two persistent engines that
OpenFabric starts: ACE-Step and YuE. **Stop / free memory** waits for the owned
process tree to exit. It does not delete installed packages, weights, source
media, checkpoints or generated outputs.

Stopping requires a verified idle state. Native request reservations, durable
queued/running music jobs, video jobs, setup and other registered local work
block the action. When ACE-Step is running, its queue and training accounting
must also respond with valid idle data. Unavailable or malformed accounting
does not authorize a stop. Refresh after work finishes or the engine responds.

Each running tree has an opaque instance token. A request referring to an old
run cannot stop a replacement. Process ownership comes from the retained
Windows job handle or the POSIX supervisor's token-bound receipt, not from an
HTTP port or a caller-supplied PID. Native request admission is held until the
stop drains, including when the browser disconnects. Shutdown retains its own
cleanup path. Failure to drain is reported honestly and ownership is retained.

GPT-SoVITS is an external service and is never stopped by these controls. Video,
conversion and other one-shot workers exit with their jobs; they do not have
an idle resident process to unload. These controls do not offload a live model
to CPU memory or promise a measured quantity of freed RAM.

## Report a problem

The **Preview support report** button creates a small JSON document for review.
The report includes only:

- app metadata version, Python version, coarse OS and architecture;
- declared acceleration category and physical RAM where available;
- fixed module identifiers and their readiness/management states;
- fixed engine identifiers, runtime/idle state and verified ownership;
- up to 20 typed actions from this panel and allowlisted stable error codes.

It excludes raw logs and exception messages, file paths, project/profile/model
names, prompts, transcripts, media, URLs, credentials, process IDs and run
tokens. Backend schemas forbid extra fields, and the browser validates the
response before displaying or downloading it. This is an allowlist, not a
best-effort redaction of a log dump.

The downloaded file contains the precise JSON shown in the preview. Downloading
does not upload or send it anywhere; share it yourself after reviewing it.
Actions stay in this panel's memory and are discarded when it is closed.
There is no automatic reporting or telemetry.

If the backend cannot be reached or its report cannot be validated, preview
falls back to a browser-only report. It leaves runtime, modules, chip,
acceleration and RAM unknown, and includes neither the raw browser user agent
nor the failed request's error text. The backend reads the repository desktop
version metadata; an unavailable version remains null. A browser fallback can
use a validated `VITE_OPENFABRIC_VERSION` build value, otherwise it remains null.
Physical RAM is currently detected through POSIX system accounting; Windows
and systems without that accounting leave it null. Unknown values are not
inferred from an engine directory or browser platform string.

## Verification

Focused tests cover busy/unknown admission, stale engine instances, foreign
process identity, cancellation and drained exit, stop/request races, origin
and payload validation, allowlist privacy, exact reviewed downloads and UI
teardown. Platform typing checks cover Linux, macOS and Windows branches.
These tests do not load GPU weights or stop a real user engine.
