# Optional reference preparation

Reference preparation retains sources in the same library and upload catalog. A local upload uses the existing track upload API, then `/api/references/prepare`; by default it reads the retained original audio, not the current voice result or lossy export. The optional `source_version_id` selects an available immutable version explicitly. Prepared lyrics and ABC stay in the preparation record. Review them before transferring them to a generation form; preparation never replaces the track's existing lyrics or score.

URL imports currently accept only HTTPS YouTube video links (`youtube.com/watch?v=…` or `youtu.be/…`), not playlists, livestreams or arbitrary sites. Maximum source duration is ten minutes and maximum source size is 512 MiB. The importer requests a direct HTTPS audio format and preserves its original container bytes, with provider/video identity, SHA256 and import time in provenance. It never forces MP3 conversion. Encoded YouTube audio remains encoded audio: decoding it does not restore information the provider discarded.

A source-only import is useful without any preparation models. Manual subtitles require a chosen available language; individual cue times and language remain in the result. Automatic subtitles are not silently substituted. Whisper, vocal separation and SheetSage melody are separate optional stages. A missing or failed optional tool leaves a saved source usable and reports a partial result. A separated vocal is retained with the preparation record and can be auditioned independently of the source.

## Install optional tools

Install the pinned small Python packages into the application's existing backend environment, never the system environment:

```sh
uv pip install --python backend/.venv/bin/python -r backend/requirements-reference.txt
```

On Windows use `backend/.venv/Scripts/python.exe`. Install [Deno](https://docs.deno.com/runtime/getting_started/installation/) 2.6.6 or newer separately if URL imports are wanted. The backend's capability endpoint checks installed packages and tool paths without network access or model imports; runtime checks the Deno version. Other yt-dlp or solver package versions fail closed until reviewed. Local preparation and text tools work without the URL dependencies.

FFmpeg and FFprobe are required for source validation and local working audio. Whisper uses an existing [whisper.cpp](https://github.com/ggml-org/whisper.cpp) `whisper-cli` binary and local model: set server-side `REFERENCE_WHISPER_BIN` and `REFERENCE_WHISPER_MODEL` to those existing paths. No model is fetched automatically by this setup. Optional separation reuses existing Demucs/RoFormer configuration; melody needs the SheetSage2 weights and a running YuE server. Each capability includes a setup hint.

## Storage, safety and supported text

Preparation metadata, scores and vocals live under `files/_references/<random-id>` and follow the existing library migration. Unique staging is confined there. Cancel waits for owned workers and descendants before cleanup; retry keeps saved sources and completed stage results. On restart, interrupted workers must be identified by their supervisor token and receipt before termination. An unverifiable worker blocks destructive operations. Deleting a preparation removes its optional artifacts while retaining any saved source track. Deleting that track drains its active preparations.

Metadata probes also retain their confined `.probe-<random-id>` workspace and process receipt if descendant cleanup cannot be verified. New preparation and video work remain blocked until recovery verifies termination; an exited wrapper alone is insufficient evidence. Probe staging is removed only after that verification, including after restart.

Downloader configuration, plugins, external execution and remote solver components are disabled. Provider traffic must cross an owned HTTPS CONNECT proxy; each connection validates every DNS answer and connects to a validated numeric public IP. Off-provider redirects, private addresses and non-HTTPS requests fail. Proxy bypass environment variables are removed. The pinned Deno solver runs without network, filesystem or subprocess grants, with no prompts, remote imports or configuration; its cache is confined to staging. See [Deno permissions](https://docs.deno.com/runtime/fundamentals/security/) and the [pinned yt-dlp provider](https://github.com/yt-dlp/yt-dlp/blob/2026.08.19/yt_dlp/extractor/youtube/jsc/_builtin/deno.py). Media decoding allows only supported audio containers and local file/pipe protocols; duration, bytes, output and execution time are bounded.

ABC transformations support a deliberately narrow single-voice major/minor subset: ordinary notes/rests/chords, durations, balanced slurs, single-note ties and bar lines. They preserve pitches when changing key, write explicit naturals, propagate default accidentals across octaves until each bar, and carry tied pitches across bars. A target range transposes the whole melody without folding individual notes; an interval span that cannot fit is refused. Tuplets, modal/inline keys, voices, semantic directives and chord ties are explicitly unsupported. See the [ABC 2.1 specification](https://abcnotation.com/wiki/abc:standard:v2.1). Lyric tools preserve reviewed section tags, repeated lines and actual subtitle times; plain text receives no invented timestamps.

The public-fork [URL preparation prototype](https://github.com/zeeshanhaque21/remiqora/tree/d056cb912b6f996fd41d87d0a76d36d17fa6b922/backend/app/remix) informed the feature scope. Its pipeline and ComfyUI-YuE2-derived Apache-2.0 text helpers were not copied. The bounded importer and musical/text parser here were independently implemented; the upstream prototype's separate Apache-2.0 attribution still applies to its own source. Installed engines, model weights and source material retain their separate licenses and terms.

Offline tests cover validation, proxy guards, interrupted publication, cancellation, reload, source preservation and musical pitch equivalence. They do not establish actual YouTube availability, Whisper accuracy, GPU performance or SheetSage quality; those require installed tools, models and explicit platform testing.
