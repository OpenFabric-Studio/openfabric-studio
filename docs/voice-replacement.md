# Replace a song's singing voice

Open ACE-Step, enable **Reference track / remix**, and choose **Voice Replacement**. Select a ready cloned voice, choose the song file, then click **Replace voice**. This workflow is also available while ACE-Step is stopped; it uses the installed Demucs and Seed-VC tools.

The app separates the uploaded song, converts the vocal performance using the selected voice, matches its level, then mixes it with the separated accompaniment. It does not generate a new song. Separation can leave traces of the original singer, change the accompaniment, or struggle with duets and layered vocals. A ready voice does not guarantee a convincing result on every source.

## Files and results

- Accepted uploads: WAV, MP3, FLAC, OGG, OPUS and M4A, up to **256 MiB** and **two hours**. The audio must decode successfully; changing a filename's extension does not make a damaged file valid.
- The original file is saved independently in the library. **Download original** returns those original bytes. The converted result is a separate WAV track.
- The result card shows conversion phases and elapsed time. Any remaining-time estimate is approximate. Converted playback becomes available only after completion.
- You can cancel a running replacement, retry a failed or cancelled one, and reload the page without losing its saved state. Retrying reads the original upload, rather than converting an already converted result again.
- Deleting the replacement stops its worker before deleting result files and keeps the original source track. Deleting that original separately prevents future retries; upload the source again if needed.

Compressed uploads can decode into much larger audio and stem files. Leave sufficient free disk space for the original, WAV result and temporary processing files. The upload limits are acceptance limits, not promises about processing time or hardware capacity.

## Model selector troubleshooting

Advanced settings lists the DiT models reported by the installed ACE server. The native inventory also includes language-model objects with load metadata. The app validates that response and extracts their names without discarding the DiT choices. It shows loading, empty-inventory and retry states when retrieval fails. An ACE process marked **running** can still have its weights unloaded until initialization or a generation request.

After updating this code, restart the OpenFabric API process to load the new replacement routes, then refresh the browser. The frontend development server applies UI changes automatically; a packaged/static frontend needs rebuilding with `npm run build` in `frontend/`.

## Verification limits

Regression tests use temporary libraries, CPU audio fixtures and mocked model boundaries. Browser checks cover uploading, cancellation, retry, history reload, original download and result playback without changing the user's library. They establish workflow behavior, not singing quality; a real Demucs/Seed-VC listening test is still required on the target hardware.
