# Music workspaces

Open **Music** for the durable generation queue and links to Beat Maker, voice replacement, the beat/song mixer, mashups and adapter training. Each workflow reviews source versions and storage before accepting work. Leaving a page stops observation; it does not cancel an accepted backend job. Reopen the saved job or plan to reconnect. Use the explicit cancel control to stop work.

These workspaces are experimental. A completed CPU render verifies its artifacts and ownership; it does not establish the musical quality of an untested neural model. Keep the original library and a consistent backup before upgrading. The [execution record](superpowers/plans/2026-10-17-music-execution.md) distinguishes implementation, regression evidence, actual native observations and remaining gates.

## Create and reuse a beat

1. Open **Beat Maker**, create or open a saved beat, and choose a compatible kit. The starter kit supplies procedural drums. Regional presets describe candidate arrangements; missing instruments and unreviewed musical assumptions remain visible.
2. Edit the grid, notes, velocity, chance, ratchets and timing. Save the revision before rendering or applying a transform. MIDI approximates some audio-only articulations; inspect its warnings.
3. Preview a pattern, then render the full arrangement. Completed full renders provide audio, aligned stems, MIDI and authoring JSON. Open the pinned revision in the editor to arrange its lanes.
4. Use a completed render as a Stable audio reference, queue variations, or open **Build a song from audio** for ACE-Step. The handoff chooses a source; it never approves storage or starts generation.

## Generate from audio

**Stable Audio:** select the matching Small Music or Medium model and adapter. Audio-to-audio generates a new full mix and may change the source. Inpainting requires a compatible WAV and matching duration; the app preserves original PCM outside the reviewed region. Prepare the separate generation/encoder bundle when needed, then review the exact source, region/noise setting and storage. Queue acceptance retains one independent source copy for all variations.

**ACE-Step:** choose Complete for several added musical roles or Lego for one. These require the explicitly prepared Base bundle; Turbo/SFT availability does not establish Base task support. Source audio must be 10–300 seconds and at most 512 MiB. Vocal roles require a vocal brief and lyrics. Native padding and model-led endings mean the brief's target duration is not guaranteed. The result is a generated full mix, with no promise of an unchanged beat or an isolated added part. Native GPU generation/listening evidence remains a separate gate.

## Mix a beat with a song

1. Open **Beat + song mixer**. Select and inspect the song version. Select either audio-only beat material or a completed authored beat render.
2. Choose excerpts of at most ten minutes. Authored repeats are recompiled from events; their musical span is separate from the audio tail. Audio-only looping/pitch changes need an explicit approximation acknowledgement.
3. Choose layering or drum replacement. Layering needs no separator. Drum replacement needs a verified local separation path; the original drum lane stays muted.
4. Review timing direction, manual BPM/meter and anchors. Every segment must fit within 0.75–1.5 times its source duration. Bass/kick leadership labels describe your intent; the actual gain/EQ/high-pass/ducking controls perform the processing.
5. Review the plan revision and disk requirement, then render. Preview renders the complete master before cropping, so it is not a faster path. A full result saves a library track, processed stems, dry references and an editable project. Processed stems precede master loudness processing.

Saved full mixes expose the common audio-version, analysis and export controls. Keep dry/master reference lanes muted unless deliberately comparing them in the editor.

## Replace a voice

Choose prepared vocals, conversion from a song, or the hybrid workflow. Conversion requires a compatible registered voice and reviewed permission/trust declarations. RVC checkpoint loading uses a third-party runtime; registration is not a sandbox or a legal clearance.

Preparation retains dry stages. Review phrase alignment, timing and pitch before rendering, and listen for separation bleed or conversion artifacts. Mix adjustments can reuse completed converted audio. An uncertain network action can be explicitly reconnected with its original request identity. Concurrent saved revisions preserve local edits and require review or discard before the next mutation. A cleanup failure remains owned and cancellable.

Preparation and rendering also save bounded CPU pitch, tonal-balance and accompaniment-correlation observations when compatible local runtimes are available. After saving timing edits, use **Refresh vocal analysis**; unchanged recordings keep their reviewed timing. Pitch and gentle EQ suggestions only populate a draft: review, save and compare previews explicitly. Observed pitch is not a singer’s full range, correlation is not singer identification, and low-level attenuation does not detect breaths. See [voice replacement details](voice-replacement-v2.md).

## Assemble a mashup

Choose two exact saved versions, inspect both and review their combined storage requirement. Confirm fixed-tempo BPM/meter and ordered sections, then prepare retained sources and stems. Inspect the suggested placements, anchors and compatibility components before rendering. Missing vocal evidence leaves the total compatibility score unknown; it is not a musical-quality score. Drifting live timing remains unsupported. Preview/full artifacts and editor publication retain source and recipe evidence.

## Analyze and correct audio

Open **Music analysis** under a saved audio version. Basic features require no beat model. The optional CPU beat method requires the separately installed pinned small0 bundle; analysis never downloads it implicitly. Storage includes a source copy, retained checkpoint when selected and bounded temporary decode space.

Start explicitly, close/reopen or reload to reconnect, and use Cancel to drain its owned workers. Corrections are source/result-specific and revision checked. Estimated beats and half/double tempo interpretations need listening review; meter and vocal activity can remain unknown. Existing results remain visible when selecting a method for the next job. [Native observations and limitations](music-beats-validation.md) use synthetic audio and do not establish real-song accuracy.

## Prepare and evaluate adapters

Use the existing saved dataset library and the model-specific training tab. Regional dataset review binds source/crop hashes, permission records and group-separated held-out material. Use **Check for shared recordings** to scan saved selected excerpts before splitting. Suspected shared-audio pairs stay together by default. Separating a flagged pair requires retained auditions and an identified listening review. Attach the scan, then review the updated split and storage. Missing or insufficient acoustic evidence remains visible; the heuristic is not proof of independence. Stable training receives only selected training excerpts. The comparison workflow binds actual completed base/adapter outputs to matching settings and held-out source identities, then records listening reviews. It does not substitute a fabricated benchmark or a public release approval. See [dataset details](regional-datasets.md).

## Storage and recovery

Settings → Library offers explicit analysis-copy cleanup with a reviewed plan and confirmation. Only verified completed private copies with unchanged originals qualify; models, original media, datasets, adapters, training checkpoints and unknown files remain protected. Model/setup estimates include unknown dependency allowances rather than promising precise package-download sizes. Logical bytes are not a guarantee of physical disk space reclaimed.

Save-only retries preserve completed audio rather than repeating neural work. Job recovery does not imply full-state optimizer continuation for a trainer that lacks it. A browser reconnect and a crashed engine are different recovery cases. No workflow may infer permission to overwrite an existing destination or remove original source files.
