# Audiobook pacing and duration guidance

OpenFabric changes composition without modifying accepted dry speech recordings.

## Pacing

The completed-book workspace includes **Narration pacing**. The book defaults are a pause between saved passages plus an additional pause when the speaker label changes. Both default to zero for existing and new libraries. A per-passage pause overrides the total following gap; explicit zero removes it, and **Use book default** restores the default. The last passage receives no default gap, but an explicit final pause is retained.

A passage is a saved synthesis section, not necessarily one script line. Consecutive lines for the same speaker may already share a section. Pacing does not split those recordings or claim to locate individual words.

Saving requires the current revisions of every chapter and a fully completed book. The backend queues recoverable reassembly using existing owned audiobook workers. Dry passage paths, bytes, identities and synthesis cache entries are retained. Reassembly never synthesizes missing accepted audio: missing PCM fails with a stable error. Old assembled files stay on disk. Chapter revisions advance, so old repair candidates, read-along exports and retained-source handoffs must be reviewed against the new timing.

Pause samples are rounded to the output sample rate. Passage start/end times are computed from the cumulative assembled samples, including inserted gaps, rather than summing rounded section milliseconds. WAV/MP3/M4B, chapter audio, cue sheets, pause analysis and repairs use the recomputed assembly. Saved scene auditions use the book gap defaults; passage overrides apply to accepted chapter composition. No model or package installation is required for this feature.

## Duration targets

Speech, the single-narrator draft editor and passage repair offer 15/30/60/90-second script targets. The budget is an approximate forecast, not an audio duration command. It does not stretch or regenerate audio and does not change Video's 15-second timeline limits.

Non-silent completed speech trials, auditions and accepted/generated narration can record bounded pace observations. Observations store counts and duration, not transcript or audio. The key includes the immutable voice profile/reference, identified model/renderer, narration language and synthesis settings. Changing those inputs starts a separate estimate. Repair forecasts resolve the accepted passage’s frozen inputs rather than borrowing today’s profile settings. Duplicate source audio is counted once; at most 100 observations per key are kept.

The estimate counts spoken letters and numerals, which avoids inventing language-independent word counts. Its range includes natural pauses in the observed take and excludes separately added composition gaps. The envelope reflects observed variation and a minimum 25% pace range; it is not a measured accuracy interval. Short, silent, constant, implausible and mock audio are excluded.

The current external GPT-SoVITS API does not report its loaded checkpoint, so local forecasts explicitly remain unavailable rather than pretending that the checkout version identifies the loaded voice model. OpenRouter model observations use the existing frozen provider/model settings. Historical renders are not silently retrofitted into calibration. The UI reports saved output duration separately from forecasts (PCM sample duration for new chapters; decoded metadata for speech previews).

## Display text

New narration records original spelling separately from spoken pronunciation substitutions. A section boundary inside a substituted phrase cannot be mapped precisely and remains unknown. Historical passages without a recorded mapping and repairs that change spoken text keep a null mapping. Read-along exports may clearly declare a spoken-text fallback; they must never reconstruct original wording from today's editable chapter text or invent word alignment.

Verification uses isolated temporary libraries, synthetic PCM and mocked model boundaries. It covers gap placement, dry-cache reuse, cumulative sample rounding, repairs, stale revisions, recovery, unavailable model identity and cancelled/late UI lookups. Real model speaking-rate accuracy is not claimed from these CPU tests.
