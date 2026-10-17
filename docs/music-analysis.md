# Saved music analysis

Open a saved track's **Music analysis** panel beside its audio player. The panel uses the selected saved audio version before export processing. Expanding it loads history and a disk forecast; only **Start analysis** starts processing.

This first implementation measures loudness and peaks, window energy, spectral balance, cautious major/minor key candidates and generic audio changes. It uses CPU, NumPy and the existing FFmpeg tools. It does not install models or require a running music engine.

## Reading the result

- Key candidates use template similarity scores, not calibrated probabilities. Silence, unusual tuning, competing channels or weak evidence can leave the key unknown.
- A/B section labels indicate changes in audio. They do not establish verses, choruses or musical form.
- Tempo, beats, downbeats, meter and vocal activity detection are unavailable in this implementation. Their unknown states are intentional.
- Tracks longer than 120 seconds use overlapping bounded windows. They retain energy and loudness summaries, while global key, modulation and sections remain unknown rather than extrapolating one excerpt across the song.

**Manual corrections** can save tempo, beats per bar and key. Beats per bar is a count, not a full time signature. Corrections are stored separately from raw estimates. A revision conflict refreshes the saved result so another tab's changes are not silently replaced. Identical audio attached to another track does not share its corrections.

## Recovery and storage

Closing the panel or reloading the browser does not cancel the backend job. Reopen the panel to reconnect. After a server interruption, use **Retry analysis**. A completed private outcome is reused only after its receipt, source bytes and analysis recipe have been verified; otherwise the job analyzes its verified source snapshot again. A worker whose termination cannot be verified blocks conflicting work.

The forecast includes a retained source copy, temporary decoding space and a 1 GiB reserve. Backend checks run again before processing. Malformed headers cannot authorize unlimited PCM writes. A disk failure preserves source files; analysis never replaces the selected recording.

Snapshots and recovery files remain under the library's `music-analysis/` directory. There is no automatic cleanup or artifact-management UI yet. Do not delete files there while analysis is active. The initial limits are 8 GiB per source, two channels, 8–192 kHz input, two hours per source, one CPU worker, 32 queued/running jobs and 1,000 stored job records. These are safety limits, not performance promises. Analysis scratch and maximum-duration performance have not been calibrated on full-length production material.

Raw analysis reuse is bound to source hash, source role, analyzer code, runtime versions and the FFmpeg recipe. A cache hit still receives its own source-bound result and correction revision. The API accepts retained version IDs, hash-bound stem references and production-asset IDs; it never accepts an arbitrary filesystem path.

## Verification scope

Synthetic fixtures cover silence, chords, detuning, stereo disagreement, malformed input, decode bounds, cancellation, concurrency, interrupted publication, history recovery and correction isolation. A native FFmpeg/browser check on macOS confirmed an eight-second synthetic C-major source could be analyzed and a manual tempo correction recovered after reload. This does not establish musical accuracy on a real song or support for the later alignment, Beat Maker, voice replacement and mashup workflows.
