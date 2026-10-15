# LoRA export safety implementation plan

> Use the existing app contracts, filesystem ownership and dialog patterns. Preserve the uncommitted ACE progress repair; do not commit or push without a requested target branch.

**Goal:** Export and register a trained LoRA without deleting or overwriting existing files, with a folder chooser and an explicit destination review.

**Architecture:** OpenFabric owns the export operation instead of forwarding to the destructive native endpoint. The backend validates the training source and new absolute destination, copies into an exclusively created directory, and returns the adapter path only on success. The maintained ACE patch separately protects standalone REST and Gradio exporters. No overwrite option is offered.

**Tech stack:** Existing FastAPI/Pydantic, Python standard library, Vue/Pinia and existing accessible modal. No new dependencies.

## Implementation

- [x] Reproduce native deletion with isolated temporary source and destination fixtures; never export to the incident folder.
- [x] Add typed app-owned preview/export/options/picker contracts, generated client contracts, and backend behavioral tests. Existing files/directories/dangling links, overlapping roots, unsafe sources, concurrent creation and copy failures must preserve previous data. Retain partial new copies on failure instead of deleting directories.
- [x] Block the legacy destructive proxy endpoint. Own filesystem copying through cancellation; only register a successfully completed export.
- [x] Patch the native REST and Gradio exporters to refuse replacement. Verify the full maintained patch against pinned upstream and provide a differential for the installed customized checkout.
- [x] Use a parent-folder chooser plus name and show the backend-resolved destination/checkpoint in an accessible confirmation modal. Keep manual folder entry as a fallback. Preserve the old saved exact path without silently treating it as the new parent folder.
- [x] Add explicit warnings before MIDI/stem recreation and describe actual file loss in existing song/video deletion confirmations. Metadata-only removal must not claim to delete files.
- [x] Run focused regressions before the full frontend suite/build, backend suite, generated-contract drift check and strict Python platform loop. Run desktop tests if desktop code/startup changes.
- [x] Review the complete diff; test any live export only in a new temporary directory. Apply the installed-engine differential only after backup and an idle-state check. Preserve the trained source and incident destination.

## Verified on 2026-10-15

- Frontend: 165 test files and 1,520 tests passed; strict TypeScript and production build passed.
- Backend: 2,007 tests ran successfully with 10 skips. The final focused export/native/progress run passed all 56 tests, including the last native race regressions added after full-suite discovery.
- Generated-contract drift check and all 21 CI strict-mypy commands across Linux, macOS and Windows typing paths passed. These are typing checks, not runtime tests on those operating systems.
- Desktop: 87 tests passed with one skipped.
- Live application API: preview created nothing; a new temporary export copied all three adapter files (335,617,569 bytes) with matching SHA-256 digests. A repeated export returned 409 and preserved both weights and a sentinel file. The legacy proxy returned 410.
- macOS picker process started from the browser, and reloading the page terminated and reaped it. Folder selection itself and other operating-system dialogs were not manually exercised.
- The installed ACE export differential was applied after stopping the idle engine and backing up its existing source changes. Reverse comparison confirmed those pre-existing changes were preserved. The application was restarted; no commit or push was made.

## Incident and verification limits

The native REST exporter called `shutil.rmtree(export_path)` before copying. The source adapter remains intact. Local recovery or Google Drive recovery has not been performed; no files in the affected destination may be changed by this work. Google Drive Trash/Activity is the first recovery check: https://support.google.com/drive/answer/15701190?hl=EN.

The browser-native picker uses the existing macOS dialog mechanism; platforms without that mechanism retain manual entry. Successful copying does not establish model quality. Packaging and other operating-system dialogs require their own runtime evidence.

Portable canonical-path checks and exclusive creation protect the tested concurrent writers, including directory-link substitution at copy boundaries. They do not pin kernel directory handles against a hostile process running with the same filesystem authority; platform-specific handle-based copying is needed if that threat enters scope.
