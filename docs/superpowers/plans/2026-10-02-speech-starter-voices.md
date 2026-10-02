# Speech Starter Voices Implementation Plan

**Goal:** Supply four documented speech references that users can preview and use in Speech and Audiobooks.

**Architecture:** Bundle small VCTK clips and a validated catalog with the backend. Explicit imports copy audio into the existing SQLite profile library; generated contracts connect the catalog to an accessible frontend component.

**Tech stack:** FastAPI, Pydantic, SQLite, Vue, strict TypeScript, unittest and Vitest.

## Tasks

- [x] Prepare four 3–10 second reference WAVs under `backend/assets/starter-voices/`, with exact transcripts, hashes, measured audio properties, official license and attribution.
- [x] Add failing HTTP tests in `backend/app/speech_starter_voices_test.py` for offline catalog, preview, import, duplication, failures, migration, deletion/reimport and unsafe paths. Run with temporary configuration/data/engine paths.
- [x] Extend `backend/app/voice_profile_contracts.py`; add `backend/app/speech_starter_voices.py`; migrate the profile database with a nullable unique starter identifier. Regenerate client contracts using `backend/scripts/generate_contracts.py` and run the focused backend tests.
- [x] Add guarded media routes for catalog audio and existing speech trial output. Verify traversal, missing files and symlinks using temporary assets.
- [x] Add failing frontend tests for preview/import, attribution, failure/retry, selection preservation, draft preservation, duplicate clicks, and playback teardown. Implement a focused starter catalog component and integrate with `VoiceProfilesPanel.vue`, API helpers and translated copy.
- [x] Review real clips' provenance, exact waveform integrity and measured properties; verify source playback in the running Speech UI. Verify the import is visible in the Audiobooks selector using a temporary test library. Subjective synthesis quality remains unverified.
- [x] Run frontend tests/build, backend regression tests, contract check and strict mypy platform checks; run desktop tests if resource packaging is affected. Inspect the final diff and report limitations. Leave changes uncommitted unless the user requests a commit.

## Ownership

Asset preparation owns only `backend/assets/starter-voices/**`. Frontend work owns only the new starter UI, API helper additions, Speech workspace integration, localization and frontend tests. The primary agent owns backend contracts, storage, routes, integration verification and final review. Generated artifacts are regenerated only by the primary agent.

## Verification evidence

- Frontend: `npm test` passed 859 tests in 99 files; `npm run build` passed strict type policy, vue-tsc and Vite.
- Backend: isolated `python -m unittest discover -s app -p '*_test.py' -v` passed 687 tests with 10 skips. Tests used temporary configuration, library and Seed-VC paths.
- Contracts: `python backend/scripts/generate_contracts.py --check` passed.
- Python core: the current CI strict mypy command passed for `linux`, `darwin` and `win32`, covering 80 source files. This is the declared core scope, not whole-backend typing or actual Windows/Linux runtime verification.
- Desktop: `npm test` passed 47 tests, including recursive copying of all starter assets and transcripts.
- Browser: an isolated temporary library confirmed source playback, single playback ownership, catalog import, selected-text focus, explicit silent mock trial playback/download, and the imported Audiobook narrator option. The user's active backend and engines were not restarted.
- Real engine probe: the existing GPT-SoVITS API responded to status checks, but a non-mock p225 trial failed with an incomplete chunked response. No generated voice quality was established. Engine models were not downloaded or changed.
- Review: fixed database/profile-directory symlink escapes and a directory-handling defect in the packaging regression test. Final source diff and whitespace checks passed.

## Remaining limits

The running backend needs a restart to load the new routes. The separate existing GPT-SoVITS synthesis failure needs engine diagnostics before real generation quality can be evaluated. An abrupt process kill before a profile transaction commits may leave an unreferenced new UUID directory; retry remains idempotent for visible profiles and never overwrites original references.
