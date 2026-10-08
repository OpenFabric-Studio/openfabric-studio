# Speech references and cast inspection

Approved scope: validate speech-reference uploads, protect voice-profile mutations
with the existing trusted-origin rule, and inspect chapter cast routing without
synthesis. Engine upgrades and automatic reference selection are separate work.

Uploads must be bounded before multipart parsing and before reading file bytes.
Decode supported audio before storing a new profile; malformed/truncated WAV and
decoder failures must return stable errors without leaving a profile or file.
Keep original valid audio bytes and existing profiles unchanged. Apply origin
checks to create, starter import, patch and delete, including local profiles.

Cast inspection uses the existing label parser and narration turn resolver.
Inspect one selected chapter of a draft or saved book. Return resolved speaker,
profile ID/name and spoken text, plus unmatched-label, narrator-shared and unused
cast warnings. Unmatched labels remain narrator text. Missing/revoked profiles
must be visible as warnings. Saved checks require the selected chapter revision;
stale revisions fail and client results disappear after edits or navigation.
Checking must not synthesize, call providers, write media or modify a book.

Expose the check beside existing auditions, with keyboard-accessible controls,
typed response parsing and clear loading/error states. No database migration or
new package family; promote the existing SoundFile decoder into baseline locked
requirements so clean installs support validation. Generate client schemas from
backend models. Verify malformed and oversized uploads, trusted/untrusted origins, routing parity, voice readiness,
stale saved revisions and late frontend responses using temporary libraries.
