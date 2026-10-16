# Training audio import

> Historical record — HeartMuLa retired on 16 October 2026. Its generation, training and installation instructions below no longer describe supported functionality. See [current music documentation](../../local-music.md).

**Goal:** Add multiple audio files or a folder directly to YuE2 LoRA training.

**Design:** Keep the existing library picker and backend upload API. Add a small
training importer to the shared YuE2/HeartMuLa form. File and folder buttons are
keyboard alternatives to the drop area. Read nested dropped directories fully,
including browser batches; accept WAV, MP3 and FLAC, matching the upload API.
Upload sequentially, add each confirmed library track to the selected samples,
and show progress, selection count, skipped files and unconfirmed uploads.
Captions and lyrics remain explicit per recording. Training stays disabled
during import. Limit selected tracks to 100, retain confirmed additions on
failure, and stop further work after cancellation, deactivation or unmount.

- [x] Reproduce the missing multiple-file input in the training form with an
  integration test. Verify two uploaded tracks reach one paired training request.
- [x] Add a bounded, typed dropped-directory collector with tests for nested
  folders, multiple read batches, errors, unsupported files and cancellation.
- [x] Add the importer with per-file upload ownership, duplicate selection
  prevention, clear limits/errors and accessible file/folder controls. Cover
  partial failure, cancel, late responses and selection capacity in UI tests.
- [x] Wire the importer into the existing selected track list and training
  admission; preserve unrelated drafts, library and training behavior.
- [x] Run frontend tests/build, generated contract check, inspect the task diff,
  and verify actual local file/folder selection in a separate browser test tab.

No model work, backend schema changes, dependencies, commits or pushes.

## Verification

- Regression tests first reproduced the missing multiple-file input. Additional
  regressions caught distinct recordings with identical names/size/timestamps
  being conflated and selection capacity changing during an awaited upload.
- Frontend: 1,808 tests passed; strict TypeScript/type policy and production
  build passed. Generated API contracts are unchanged and their check passed.
- Native Chrome on macOS imported two selected WAV files, then a folder with
  one WAV and another in a nested folder. All four appeared in the training
  list; the folder's text file was reported as skipped. This used a temporary
  backend/configuration/library; no user recording was added or replaced.
- Automated tests cover dropped directories, browser entry batches, read errors,
  cancellation, late responses, partial upload failures and the 100-track limit.
- Independent review found no remaining blockers after both regression fixes.
- No training job was started. Backend/desktop suites and GPU training were not
  rerun for this frontend-only change.
