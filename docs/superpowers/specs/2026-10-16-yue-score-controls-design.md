# YuE2 musical controls and ABC score editing

Status: user approved both paths on 2026-10-16.

The user wants musical parameters in YuE2's ABC score area, similar to
ACE-Step's advanced settings, including key and BPM.

## Verified behavior

- The current YuE form exposes a raw ABC textarea when planning is enabled.
  ACE-Step separately exposes BPM, key/scale and time signature.
- The installed YuE2 model specification and pinned audio.cpp documentation
  support external ABC with melody/full planning. They expose no independent
  numeric tempo, key or time-signature request options. External ABC replaces
  automatic score planning; a header-only score must not be used as a planner
  prompt.
- The existing backend reference tool transforms a bounded ABC subset. It
  handles major/minor signatures, actual note/chord transposition and tempo,
  and rejects unsupported notation instead of guessing.

Primary native reference:
https://github.com/0xShug0/audio.cpp/blob/f2b4937306daa25f5c78520f3c626ed31495a37a/docs/models/yue2.md#abc-conditioning

## Approaches

1. Recommended: one musical-settings card serves automatic score planning and
   existing ABC. Automatic planning receives clearly labeled prompt guidance;
   an existing score is changed by an explicit Apply to ABC action.
2. Smaller alternative: expose the same card only for an existing ABC score.
   This omits control of automatic planning.

## Proposed interface and behavior

Place a compact Musical settings card directly above the ABC textarea, using
the current form's two-column controls and existing help dialog:

- Tempo (BPM): optional, 20–300, with the current/default tempo preserved when
  omitted. Score editing writes a quarter-note tempo.
- Key / scale: optional major/minor selection. For existing ABC, transpose
  notes and chord symbols to the selected tonic while retaining its major or
  minor mode. Converting major to minor requires rewriting a melody and is not
  silently treated as transposition.
- Time signature: keep existing/default, or a common meter. Changing an ABC
  meter changes its header; it does not rearrange existing note lengths or bars.
- An info button explains each control and the difference between planning
  guidance and editing a supplied score.
- Keep the raw ABC textarea available. Changing form controls does not silently
  rewrite the score. Apply to ABC validates and updates the current draft;
  errors or stale responses preserve the user's current text.

With no supplied ABC, pass musical choices as readable guidance in the existing
style prompt, with honest wording that generated music may not follow them
exactly. Do not fabricate native options or send a header-only external score.

Use the existing bounded reference transformer for supplied ABC, extending its
typed request only as necessary for target key and meter. Retain its supported
notation limits and show an actionable error for unsupported scores. Never
fall back to changing the key label without transposing the music.

## Integration and verification scope

Changes must cover the YuE form, app-owned request/settings schemas, generated
client contracts, backend native-request normalization, saved presets/history
restoration and localized help. Reuse existing validation, reference tooling
and components. Preserve all unrelated uncommitted work and live jobs.

Regression coverage should include tempo/key/meter changes, note/chord pitch
preservation under transposition, unsupported scores, blank-score automatic
planning, mode changes, old snapshots, reuse after reload, input limits, stale
responses and unmount cancellation. Run applicable required repository checks
and inspect the UI without starting a user generation or training job.

No new dependency, model download, native-engine patch, automatic major/minor
conversion or audio post-processing is proposed. Prompt adherence and audio
quality require separate measured evidence.
