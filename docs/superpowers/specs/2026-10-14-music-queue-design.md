# Music queue — proposed design

> Historical record — HeartMuLa retired on 16 October 2026. Its generation, training and installation instructions below no longer describe supported functionality. See [current music documentation](../../local-music.md).

Status: approved by the user on 14 October 2026, including disclosed one-shot
HeartMuLa/Stable Audio reloads. Implementation follows the companion plan.

## Outcome

Make `/music` the default place to describe music, select local models, choose
variations and run an ordered queue. The backend owns execution and saves every
completed take. Selecting a checkbox does not load a model. Starting the queue
authorizes automatic model loading, generation and unloading in the displayed
order.

The first scope covers ACE-Step, YuE, HeartMuLa and Stable Audio 3. Existing
engine-specific generators, cloud music and LoRA training remain accessible.
Paid cloud generation is not automatically added to the local queue.

## Interface

The recommended layout has an editable brief on the left, a live generation plan
on the right, and the queue below. It retains the existing dark theme, locally
served Inter font, indigo accent and shared components. On narrow screens these
sections stack. The queue becomes the focus after adding a brief.

- Brief: optional title, music description, vocals/instrumental choice, target
  length and expandable lyrics. Tempo, key and language are optional additional
  direction. Keep the original text independently of model-specific requests.
- Models: selectable cards with compatibility and setup status, selection order
  and an explanation when a model cannot use the brief. Do not start engines
  merely by visiting Music or selecting a model.
- Variations: one count per selected model, initially 1–8; show the exact total.
  For example, three selected models and four variations mean twelve items.
  Allocate distinct, persisted seeds before execution.
- Plan: show model order, variation count, effective duration and compatibility
  adjustments. “See how your brief is adapted” reveals each normalized request.
  Compatible adapter selections belong in model-specific advanced settings;
  loading an adapter never applies it to another engine.
- Queue: “Add to queue” saves an immutable brief/plan snapshot. “Start queue”
  begins execution. New briefs may be added while it runs. Reorder or cancel
  unstarted briefs with keyboard-accessible controls. Editing the composer does
  not change an already queued brief.
- During execution show the active model, completed/total takes and verified
  stages. “Pause after this variation” saves the current output, releases the
  model and leaves the rest pending. Cancellation of active work stops/drains
  it and pauses advancement; completed outputs remain available.
- Results: group takes by brief, then model and variation. Reuse the existing
  library audio, favourite and editor actions. Preserve exact requests, seeds,
  model variants and adapter provenance with saved tracks.

A guided three-step wizard is an alternative if the split view feels crowded;
it adds navigation between the brief, model selection and review. A spreadsheet
of per-model jobs gives more direct editing but introduces many fields before
the user has made a first track. The split view is the recommended first version.

The companion mockup is isolated under `.superpowers/brainstorm/` and uses only
simulated readiness, progress and results. It calls no generation APIs, creates
no audio and does not persist a real queue.

## Verified model differences

These describe the currently selected app contracts and pinned runtimes.

| Model | Input mapping and limits | Residency |
| --- | --- | --- |
| ACE-Step | Style description, lyrics or instrumental; manual duration 10–300 seconds or auto. Native batch size 1–8. Optional native LoRA strength 0–1. | Managed native server can remain loaded across compatible items. |
| YuE | Nonempty lyrics and style are required. No duration input exists; token limits must not be presented as a duration. Precision and AR/NAR adapters determine session configuration. | Native server reuses matching loaded session options. |
| HeartMuLa | Tags/description and optional lyrics; 5–240 seconds is a maximum duration, with possible early EOS. No LoRA API in the selected runtime. | Current CLI loads its model/codec/tokenizer for one output, then exits. |
| Stable Audio 3 | Instrumental only in this app; Small Music 5–120 seconds, Medium 5–380. Optional adapter must match the selected variant. | Current CLI loads for one output, then exits. |

Normalization means deterministic field mapping and validation, not an LLM
rewriting the user's instructions. Use structured fields where available and
append explicit musical direction to descriptions where needed. Preserve lyrics;
do not invent missing lyrics, silently truncate input or discard incompatible
vocals. Preview the completed prompt against its engine's bounds before enqueue.

A target outside a model's bounds displays the effective value before enqueue.
YuE displays “length is model-led.” The initial interface disables SA3 for a
vocal brief and YuE for an instrumental brief. An instrumental reinterpretation
of a vocal brief would require an explicit per-model choice rather than silently
removing lyrics. Missing runtime, weights or platform support prevents a model
from entering a runnable plan; configuration links lead to existing setup.

A shared 31-bit seed range fits all four integrations. The same numeric seed
across different architectures does not represent equivalent randomness or audio.

Initial app-owned bounds are: title 120 characters, description 3,500 characters,
lyrics 20,000 characters, requested target length 10–380 seconds, optional tempo
20–300 BPM, and optional key/language values 80 characters each. Each finalized
engine request must also pass that engine's contract; reject an overlong assembled
prompt rather than truncating it. Enforce a transactional limit of 1,000 outstanding
variations across the queue. These are application limits, not claims about every
native model's maximum capability.

HeartMuLa and SA3 residency across multiple variations is not implemented by
their current CLIs. The initial coordinator can safely reuse their one-shot
workers, disclose the reload cost and finish all variations of that model before
switching to the next model. A requirement to load those weights only once for a
whole group entails dedicated batch adapters and real model validation. Repeating
`runpy.run_path` is not resident batching.

Pinned source evidence:

- [HeartMuLa CLI](https://github.com/Acelogic/heartlib-mlx/blob/02dd2e22f6a1bf1a2f769a13f8af952a7a4e9cee/generate.py)
- [Stable Audio 3 MLX CLI](https://github.com/Stability-AI/stable-audio-3/blob/77e0398c26f7d22d801ec1bb036307e8b231f741/optimized/mlx/scripts/sa3_mlx.py)
- Local `generation_contracts.py`, `yue_contracts.py`, `local_music_contracts.py`,
  `ace_jobs.py`, `yue_jobs.py`, `local_music.py` and `local_music_runtime.py`.

## Backend ownership and persistence

Add one durable coordinator over the existing generation services. Submit one
child job at a time. Group variations by selected engine/configuration within
each brief and retain brief FIFO order; do not reorder unrelated user briefs to
optimize loading. Consecutive compatible native items may reuse their loaded
model. Unload after the final group, a pause, cancellation or queue completion.

Use SQLite migrations for queue control, brief records and ordered item records.
Store original brief, normalized requests, input hashes, resolved seeds,
capability/adapter identities, ordering, child job links, attempts and stable
errors. Completed files remain in the existing track library. Avoid another
output catalog or copies of published audio.

New app-owned request/response schemas are Pydantic contracts with generated
frontend types. Model-specific requests use a discriminated union and existing
validated engine contracts rather than opaque browser-supplied vendor JSON.
Preview, enqueue and execution all validate readiness, adapters and bounds;
execution never silently substitutes a different plan after settings change.

Enqueue accepts a client-generated submission key and records a request hash.
Repeating a key with identical content returns the same saved brief; using it
with different content fails. Revision checks protect reorder/start/pause actions
from stale browser tabs. Bound text, variation count and total outstanding work.

One private internal queue owner reserves accelerator and engine transitions.
It must participate in existing admission rules without blocking its own child
submission, model switch or unload. A pending/paused queue alone does not reserve
the GPU. External generation, native initialization, LoRA mutation, setup and
model switching respect the active queue reservation. Existing work finishes
before the queue takes control.

Use exact `manager.owned_instance()` identities for native lifecycle actions;
never kill a process based on a PID or active-model name alone. Manual Stop also
stops queue advancement. ACE session-global LoRA state must be explicitly applied
or cleared for each planned configuration. Preserve existing process supervisors,
output publication and cleanup-failure admission blocks.

The initial brief excludes automatic voice conversion and reference-audio
transformations. Existing advanced generators retain them. Generation completion
can precede voice-conversion completion, so adding it later requires an explicitly
awaited stage before unloading; it cannot be treated as a cosmetic option.

## Failure, pause and recovery

1. Preflight the full brief before enqueue; incompatible selections stay visible
   with actionable field/setup errors.
2. Known per-item failure retains its attempt and completed outputs. A failed
   model startup blocks the remaining items in that model group. Other groups
   can advance only after verified release of the failed model's resources.
3. Failed cleanup or unknown ownership pauses the whole queue. Never load the
   next model while the previous one may still own a worker or accelerator.
4. Browser reload/navigation only reconnects to persisted queue state; it must
   not submit items or restart timers that launch generation.
5. App shutdown drains owned work and retains pending items. On backend restart,
   reconcile existing engine jobs and completed publication first, then leave
   remaining work paused for explicit resume.
6. An ambiguous native submission is not silently retried. ACE can accept a task
   before its returned ID is durably linked; no native request-id deduplication
   is verified. Mark this for reconciliation rather than claiming exactly-once
   inference or creating a duplicate take.
7. Retry saving reuses retained audio. Retrying inference is an explicit new
   recorded attempt with the resolved request/seed preserved.

Progress uses available evidence: queue position, child status, saved item count,
owned lifecycle transitions and supported native progress. One-shot loading and
generation may share a status when no separate signal exists. Do not infer a
percentage or ETA from elapsed time. Mockup timing is demonstrative only.

## Scope and verification for implementation

Affected integrations: `/music` routing and navigation, composer/queue/results
views and translated text, generated API contracts, SQLite migrations, existing
engine submissions and result provenance, model/LoRA controls, resource admission,
manual Stop, startup recovery, shutdown, support diagnostics and strict CI scope.
Keep existing direct engine URLs and the current shared design components.

Regression coverage must include multi-model ordering, variation/seed mapping,
plan limits, unsupported vocals/duration, changed/missing adapters, idempotent
enqueue, conflicting tabs, pause/unload order, cancellation during model loading,
failed drains, manual Stop, interrupted native submission, saved-output recovery,
partial success and reconnect after browser reload. Use temporary configuration,
library, module and Seed-VC roots, mocked model boundaries and CPU worker fixtures.

Run the repository's frontend tests/build, backend discovery, generated-contract
check and all strict mypy platform batches. Run affected desktop lifecycle tests.
Inspect real browser keyboard/responsive flows. Actual loading, residency, memory
release, model output and audio quality need evidence from installed model weights;
mocked transitions alone do not establish those properties.
