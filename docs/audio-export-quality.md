# Audio versions, exports and quality

Open **Settings** in the header for audio encoding defaults and the existing Data folder controls. On a saved track, **Audio versions** selects the original or a cloned voice. Choose its source file or a completed format button to switch and play that exact file in the main waveform player; its active label and download follow the selection. **Voices & exports** adds another ready voice or creates an export for the selected version. Queued and failed files are unavailable for playback, and inspecting them preserves the current playable selection.

Each new voice uses the immutable original, not a previous conversion. Voice versions and exports persist independently of the browser. New ACE submissions request a WAV source from the installed engine, retain it, and create the generation form's requested-format export with the settings captured at submission. Voice conversion retains its separate WAV output; create MP3/FLAC/WAV exports for that version from its track card.

## Explicit export profiles

| Format | Default | Available controls |
| --- | --- | --- |
| MP3 | 320 kbps CBR, 48 kHz stereo | CBR 128/192/256/320 kbps, or LAME VBR quality 0–9 (0 highest); 44.1/48 kHz, mono/stereo |
| WAV | 24-bit PCM, 48 kHz stereo | PCM 16/24-bit or 32-bit float; 44.1/48 kHz, mono/stereo |
| FLAC | 24-bit, compression level 5, 48 kHz stereo | 16/24-bit; compression 0–8; 44.1/48 kHz, mono/stereo |

These are explicit FFmpeg arguments, validated on the backend. Each export captures its profile, verifies the source digest, writes an owned partial file, validates codec/rate/channels/bit depth/duration and decodability, then publishes the download. Changing Settings affects future submissions/exports. It does not alter queued or completed downloads. Matching completed exports are reused; a changed profile produces another file.

FLAC compression changes storage and encoding effort, not fidelity. Higher bit depth/rate and re-encoding to WAV do not recover lost source detail. In particular, the installed ACE SoundFile fallback currently produces 16-bit WAV source audio: a 24-bit export does not manufacture additional source resolution.

## Existing tracks

The older native ACE defaults inspected on **2026-10-01** were MP3 128 kbps CBR at 48 kHz stereo and WAV/FLAC 16-bit at 48 kHz stereo on this installation's SoundFile fallback. FLAC used libsndfile's level-5 default. The installed source is `external/ACE-Step-1.5/acestep/audio_utils.py`, commit `ca1e85fe9430179831e6bc6be790c332190a3866`.

Older voice conversion produced a 44.1 kHz stereo WAV mix. The new catalog can recover an original when its exact sibling or replacement-source provenance remains available; it does not reconstruct overwritten or missing historical audio. If the only retained original is MP3, WAV and FLAC exports inherit its lost detail. Such tracks show an unavailable-original notice when no original can be recovered and keep existing voice playback/export available. Snapshots consume additional disk space to protect originals independently of default-file changes.

Data-folder changes retain the existing restart/migration workflow. Migration includes the version catalog, exports and encoding settings. Deleting a track drains its conversion/export workers before removing their owned files.

## Favorites

The star marks an individual saved track and persists in the library catalog. **Favorites only** combines with date filters and sorting in both music feeds; **Reset** clears the filter without removing saved stars. In an ACE batch, each saved candidate has its own star, and favorites filtering displays only the matching candidates. Voice versions and format exports remain part of their parent track.

Verification uses synthetic CPU audio and real FFmpeg/ffprobe for CBR/VBR MP3, PCM16/24/float WAV and FLAC16/24 profiles. Model conversion boundaries are mocked in these regressions; they do not establish voice-model quality or GPU inference performance.
