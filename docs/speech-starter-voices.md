# Speech starter voices

Open **Tools → Voice Clone → Speech**, then browse **Starter voices**. Preview a reference recording and select **Add to my voices** to save an independent copy in your configured library. The saved profile includes the exact reference transcript used by the speech engine and is also available in **Create → Audiobook**.

The clips are bundled with OpenFabric Studio. Previewing and adding them works offline and does not require downloading a speech model. Generating new speech still requires a working GPT-SoVITS API. The workspace shows the engine status; mock mode only creates silent placeholder output.

These are reference recordings from CSTR VCTK Corpus version 0.92, not pre-trained voice checkpoints. The corpus contains recordings made in a hemi-anechoic chamber; the selected files retain their original dry signal, with lossless conversion to browser-compatible PCM WAV. Individual recordings are identified by their anonymous source speaker IDs and documented accent labels. Measured reference properties and the original file hashes are retained in [the asset catalog](../backend/assets/starter-voices/catalog.json).

Reference recording quality alone does not establish generated voice quality. Audition the generated trial before using a voice for a full audiobook. No real-engine output quality is certified by the bundled references or the unit tests.

Repeated imports reuse the saved profile, including any edits to its name, transcript or permission setting. Deleting the profile removes its library copy; the bundled reference remains available to add again. Existing profiles are preserved during the database upgrade.

## Uploading your own reference

In **Voice Clone → Speech → New profile**, upload a complete WAV or FLAC and
confirm permission to use it. New uploads are limited to 32 MiB, 120 seconds,
8 channels and 192 kHz; the multipart request is capped at 33 MiB before parsing.
The backend decodes the recording in bounded chunks and rejects incomplete WAV
chunks, decoder failures and nonfinite samples before saving a profile. Valid
source bytes are retained unchanged. Existing profiles are not revalidated or
rewritten by this change.

FLAC uploads must declare their sample count. Re-export streaming FLAC files
with an unknown count as WAV or FLAC before uploading.

Prefer a short, clean reference with an exact matching transcript. These storage
limits do not establish that every accepted recording is suitable for a speech
engine. The baseline environment includes SoundFile for local validation; no
speech model or GPU is needed. Mutating profile requests require the existing
trusted local-origin checks; documented loopback CLI requests remain supported.

## Attribution

Source: [CSTR VCTK Corpus, version 0.92](https://datashare.ed.ac.uk/handle/10283/3443), University of Edinburgh, Centre for Speech Technology Research. Junichi Yamagishi, Christophe Veaux and Kirsten MacDonald, 2019. DOI: [10.7488/ds/2645](https://doi.org/10.7488/ds/2645).

The recordings and transcripts are provided under [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/), separately from OpenFabric Studio's AGPL code license. Preserve attribution when redistributing the source clips. The full source license and per-recording provenance are included in `backend/assets/starter-voices/`.
