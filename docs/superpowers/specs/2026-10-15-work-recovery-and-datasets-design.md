# Work recovery and saved LoRA datasets

> Historical record — HeartMuLa retired on 16 October 2026. Its generation, training and installation instructions below no longer describe supported functionality. See [current music documentation](../../local-music.md).

Status: approved by the maintainer on 2026-10-15. Implementation is authorized;
a follow-up request on the same date authorizes committing and pushing the feature branch.

## Problem and verified evidence

A transient auto-label status failure sets `autoLabelRunning=false` and stops
the frontend polling loop. Its task ID, selected dataset and form settings are
only held in memory. Preprocessing has the same failure behavior. Training can
also stop its initial observation if the first status request fails before a
running job has been discovered.

The installed ACE engine exposes latest-task status endpoints for auto-label
and preprocessing, as well as current training status. Its auto-label worker
continues independently of a browser connection and atomically saves the dataset
after each successful label when a save path exists. Read-only inspection found
the reported job still running, with completed labels on disk. Four existing
saved dataset JSON files were discoverable under the configured datasets root.

Music generation services and the music queue already persist work on the
backend and generally retry observation failures. Their disconnect, reload and
ambiguous-submission paths still need regression coverage against this request.

## Options

1. **Reconnect and recover saved work, plus a dataset library (recommended).**
   Keep observing existing jobs across network failure/reload; save dataset and
   workspace state; resume interrupted labelling/preprocessing from verified
   completed items. Use existing generation/queue persistence. This directly
   fixes the reported failure without replacing model runtimes.
2. **Frontend retry alone.** Smallest patch, but loses context on refresh and
   does not provide a saved dataset library. Incomplete for this request.
3. **Also develop full training-checkpoint continuation now.** Requires changes
   and validation inside each supported trainer. More scope than reconnecting:
   an adapter file is not a complete training checkpoint.

## Recommended recovery behavior

- A failed status request means **Reconnecting**, not Failed or Stopped. Keep the
  last confirmed progress, identity and settings. Retry observation; do not
  resubmit work. Teardown must still abort/invalidate observers.
- Reload/navigation restores the selected dataset and settings and discovers
  the existing native task by its documented status API. Reconcile engine
  identity, dataset and saved run information before adopting a job; do not
  attach stale responses to a different dataset/session.
- Missing/changed engines and unknown submissions have an explicit interrupted
  state. Retained work remains visible; a fresh submission needs an explicit
  user action. No inferred exactly-once native submission guarantee.
- Ensure a dataset snapshot exists before auto-labeling, and show save failures
  separately from network failures. Preserve completed captions and manual edits.
  Resume labelling uses the saved dataset and only remaining unlabelled samples.
- Preprocessing can reuse completed tensors only when dataset, model and
  relevant settings match; otherwise explain why reuse is unavailable.
- Running training reconnects to the existing run without restarting its step
  count. Running music reconnects to its backend job. The queue preserves
  completed takes and resumes pending items using its existing controls.
- After a native engine dies, music generation has no verified mid-take
  checkpoint API. Preserve finished takes/audio and allow an explicit retry of
  the interrupted take; never call that a continuation from its last sample.

## Training checkpoint boundary

The installed ACE HTTP training request does not expose `resume_from`, although
one internal trainer path supports checkpoint loading. Its checkpoints do not
consistently contain the complete state needed for identical continuation.
The selected Stable MLX integration also does not offer verified full-state
resume. Reattaching to a live run is supported independently of this limitation.

Only show **Resume training from checkpoint** when the engine has a verified
implementation that restores model/adapter, optimizer, scheduler, counters,
random state and data order, and validates the same data/settings/base model.
Otherwise explain the limitation and retain artifacts; restarting must create
a separate run and preserve earlier checkpoints. Full checkpoint development
is a separate selectable scope, not an adapter-only warm start disguised as
resume. No GPU validation is claimed by mocked tests.

## Dataset library

Replace the free-text dataset name area with an existing-dataset selector plus
**New**, **Rename** and **Delete** controls. Display sample/label counts and the
saved or active state. Keep advanced folder import available.

- Discover valid existing dataset JSON files within the configured ACE datasets
  root. Selecting an existing dataset loads its saved metadata/captions; it does
  not rescan and reset labels. Surface malformed/unavailable entries safely.
  Training manifests, adapter configurations and caption sidecars are not dataset
  records. Unreadable dataset warnings include the filename and a safe reason.
- New datasets receive stable backend identifiers and owned storage. Display
  names can change without moving audio, tensors or checkpoint paths.
- Rename updates metadata atomically and rejects conflicting names. Changes
  carry revisions so another tab cannot silently overwrite a newer edit.
- Delete marks the dataset as recoverable logical trash, with Restore. Keep its
  JSON and audio in place, preserving shared references and existing tensor
  paths. The October 16 follow-up adds **Remove record** and **Clear trash
  records**, with explicit confirmation of the named records. These permanently
  forget only the revision-checked catalog records; audio, labels, tensors and
  adapters stay on disk. Persist excluded dataset paths so discovery cannot
  recreate cleared records. There is no file deletion endpoint. Reject traversal,
  absolute identifiers and symlink escapes.
- Block conflicting load/rename/delete/scan operations while that dataset is in
  use. Uploading/rescanning an existing dataset must preserve labels/manual edits
  for unchanged sources rather than silently rebuilding an empty dataset.
- Backend schemas generate client contracts. Filesystem discovery, validation,
  persistence and deletion decisions stay on the backend. Reuse current atomic
  metadata helpers, components and styling; add no queue service or dependency.

## Verification and rollout

First reproduce transient polling failure and reload loss with failing tests.
Cover initial-connect failure, repeated disconnect/reconnect, accepted submission
with lost response, stale results after dataset switch/unmount, preserved labels,
remaining-only processing, settings mismatch and actual interrupted engine state.
Verify music, training and queue observation without duplicating jobs.

Dataset tests use temporary roots and cover legacy discovery, create/load,
rename conflicts, delete/restore, busy datasets, shared external sources,
interrupted writes, revisions and symlink/traversal rejection. UI tests cover the
selector, counts, keyboard access and honest reconnect/resume/error states.

Run the required frontend/build, backend, contracts, strict platform typing and
affected desktop checks. Browser tests use an isolated library and mocked model
boundaries. Do not restart or alter the user's currently running engine/job
during investigation. Install/restart only after a verified idle boundary.
Leave new changes uncommitted unless the maintainer separately requests Git
integration for this work.

## LoRA navigation follow-up

After recovery and the dataset library, add an overview explaining the workflow
and training controls, followed by a tab for each music model. Reuse the existing
ACE and Stable Audio training interfaces. YuE and HeartMuLa tabs must explain
their actual loading/training capabilities instead of presenting unsupported
training controls. Preserve existing direct links and active-job observation.
