# Audiobook cast and passage review

## Prepare a voice

In **Voice Clone → Speech**, open **Reference recording** and save the exact words spoken in the reference audio and its language. Notes are a separate field. Starter voices supply their published reference transcript. Existing profiles migrate their old notes into the transcript field once for compatibility; review that text before using it. A missing transcript blocks synthesis with an actionable message.

The current GPT-SoVITS API supports English, Chinese, Japanese, Korean and Cantonese. Regional language tags select the corresponding primary language mode; an unsupported language is rejected instead of being silently rendered as English. The reference language and requested narration language are distinct inputs. Existing completed audio is not retroactively certified as having been rendered in its requested language.

## Review an import and audition the cast

Create an audiobook from text, TXT, EPUB, DRM-free MOBI/AZW/AZW3, DOCX, SRT or VTT. Imports create saved, editable drafts; they do not immediately start narration. Review chapter detection, warnings, text, inclusion choices and pronunciations. Original uploads remain available for download, and reimporting creates a separate draft.

DOCX imports extract supported paragraphs, headings and tables. Complex content carries review warnings; this is not a layout-preserving Word renderer. Subtitle imports preserve source cue identifiers, ordering, times, text and speaker labels. They do not promise dubbing alignment: audiobook narration has its own timing. Map every named speaker to a saved voice and explicitly confirm the cast review. Subtitle chapter boundaries stay fixed; exclude unwanted chapters using their inclusion checkboxes. Edited text does not erase the original cue provenance. A cue containing multiple speakers must be split in the source and reimported.

Use **Audition cast** to hear a line from each appearing voice, or **Preview scene** for a short scene from the selected chapter. The same pronunciation and render preparation is used for narration; speaker labels are not spoken. Assigned speakers with no lines are listed as skipped. Auditions run sequentially, persist in the library, and can be canceled. Save edits to an existing book before auditioning its saved inputs. Mock speech is explicitly labeled as silent placeholder audio.

## Listen and repair

Open a completed chapter and choose **Review passages**. Enter the chapter playback time to find the passage, listen to its accepted audio, and edit the words for a replacement take. **Generate a fresh take** always bypasses cached speech. Listen to the candidate before choosing **Accept this take**. Generating a candidate does not replace the accepted recording.

Acceptance rechecks the chapter revision, voice consent and completed source artifacts. It prepares a new chapter and book export before publishing their paths in one SQLite transaction. WAV, MP3 and M4B are rebuilt from the new accepted PCM. Other passage recordings and prior published files are preserved. A changed chapter requires reloading before accepting another candidate. An interrupted acceptance is recovered from its saved journal; an unverified encoder cleanup blocks further work rather than silently releasing ownership.

Accepted passage text is retained for an explicit chapter redo. Redo bypasses the cache. Resuming an older interrupted book keeps its completed PCM. New synthesis records immutable reference audio, transcript, languages and sampling settings. The external speech API does not attest its loaded checkpoint, so real speech is not reused across books on the assumption that a server URL identifies a model. This trades cache hits for correct provenance.

## Optional analysis

**Pause analysis** saves the silence energy ratio, minimum silence duration and analysis padding. These settings affect energy-derived cue analysis when cues are recomputed; they do not trim silence from accepted audio. Padding can overlap neighboring analysis spans.

**Check this passage with local ASR** is an opt-in local Whisper transcript check. It requires the configured `REFERENCE_WHISPER_BIN`, `REFERENCE_WHISPER_MODEL`, and FFmpeg; the check does not download a model. Settings → Modules exposes installation readiness. Missing configuration is shown as unavailable. Completed checks retain the audio hash and accepted render revision, recognized transcript, and possible omission, repetition or duration flags. These are screening hints: ASR mistakes and language tokenization can produce false positives. Listen and decide yourself; checks never trigger automatic rerenders. Returning to the page restores saved checks rather than creating another one.

## Reuse accepted dialogue

Select up to four accepted passages, specify clip bounds and captions for any trims, and choose **Create dialogue reel**. Open the saved project in Video. The reel copies the original cast audio and preserves source revisions; picture generation does not replace the soundtrack. See [dialogue reels and character review](dialogue-reels-and-character-review.md) for limits, selective repair refresh, undo, character datasets and held-out comparisons.

## Verification boundary

Regression tests use isolated libraries, synthetic recordings, mocked model APIs, real CPU media encoders and a fake local transcription CLI. They establish publication, provenance, parsing, cancellation and recovery behavior. They do not establish real GPT-SoVITS voice quality, Whisper recognition quality, GPU training quality, video identity consistency or native Windows/Linux model inference. Those require separate hardware and listening evidence.
