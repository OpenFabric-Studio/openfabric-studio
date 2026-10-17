# Optional Music Technology Evaluation Plan

> **For agentic workers:** Use superpowers:executing-plans for each selected experiment. This is a decision backlog, not an instruction to install every candidate. No model download, source import, cloud call or publication occurs during planning.

**Goal:** Account for the report's complete technology catalog without turning every alternative into a new maintenance obligation.

**Architecture:** Prefer current services and a deterministic baseline. Introduce an isolated, pinned candidate only when a measured gap justifies it; share source snapshots, ownership, evidence and the same export/editor boundaries. Optional failure must not disable the baseline workflow.

**Tech Stack:** Existing module setup, isolated Python environments, source/artifact manifests, owned workers, existing Vue controls and evidence/benchmark tooling.

---

## Catalog disposition

Report sections 3.3, 4.2/4.6, 5.3–5.5 and 7.1–7.5. The descriptions below are proposed roles/dispositions. Recheck exact source, dependency and weight terms before adopting any candidate; the report's stars/dates/license labels are not current installation evidence.

| Candidates | Role | Disposition |
| --- | --- | --- |
| Demucs maintained source / existing install | Stem separation | Reuse; identify exact installed source/model before new claims |
| ZFTurbo separation tooling; BS-RoFormer architecture; Kimberley Mel-RoFormer | Higher-quality separation | Existing optional path; validate the exact checkpoint and required backing output |
| python-audio-separator | Multi-model separator runner | Optional only if existing runner cannot meet a demonstrated need; per-model evidence mandatory |
| Spleeter | Alternative separator | Not in baseline; compare only if a measured compatibility/quality gap remains |
| Seed-VC | Singing conversion | Preserve existing pinned isolated path; verify local compatibility and retained notices |
| RVC | Trained singing conversion | Required optional-engine choice in plan 02; registry/ownership integration first |
| Applio | Alternative RVC tooling | Candidate X03, not automatically another engine selector |
| so-vits-svc-fork | Alternative singing conversion | Candidate after RVC baseline only; no duplicate trainer by default |
| OpenVoice v2 | Speech tone conversion | Outside singing baseline; do not advertise as equivalent singing replacement without evidence |
| DiffSinger | Score-to-singing synthesis | Candidate X05 for prepared vocals; requires voicebank provenance and platform proof |
| beat_this | Beats/downbeats | Preferred analysis candidate in plan 01; validate offline local checkpoint behavior |
| librosa | Feature extraction/DTW/key inputs | Use a pinned isolated analysis dependency if needed; bounded DTW memory is still required |
| all-in-one / allin1 | Beat/structure labels | Optional structure experiment; audit NATTEN, madmom and all model dependencies |
| madmom | MIR toolkit and models | Not default; report identifies model restrictions; no implicit DBN model install |
| SongFormer / MuQ | Structure analysis | Research option only until exact backbone restrictions and runtime reviewed |
| msaf | Classical structure segmentation | Optional comparison to own generic segmentation; avoid adding it if current methods suffice |
| Essentia; libKeyFinder; aubio | MIR alternatives | Not default. Respect project provenance/copy policy and exact code/model obligations; do not copy implementations from the report's links |
| BTC-ISMIR19; autochord | Chord recognition | Optional X02 only after chord-aware decisions improve measured results; verify weights separately |
| Basic Pitch | Audio-to-MIDI | Preferred permissive candidate to evaluate when existing restricted transcription is unsuitable |
| MuScriptor; SheetSage2 | Existing native transcription | Preserve with exact model restrictions and explicit NC choice; not silently enabled for a permissive route |
| torchcrepe; existing pYIN | Pitch diagnostics | Choose the smallest sufficient tool after exact checkpoint/dependency audit; not singer-identity proof |
| music21 | Theory/scale helpers | Optional; small sourced scale tables do not justify an extra dependency alone |
| mido; pretty-midi | MIDI serialization | Use one installed/appropriate library, or a small verified serializer; do not add both for the same purpose |
| muspy | Symbolic metrics | Optional evaluation; only if metrics answer a defined release question |
| Anticipatory Music Transformer | Symbolic infill | X01; weight terms and runtime remain a prerequisite |
| GrooVAE / magenta-js | Pattern infill/humanisation | X01; compare with deterministic timing/velocity baseline first |
| Signalsmith Stretch / python-stretch | Stretch/pitch DSP | Preferred candidate in plan 01; pin wrapper and native dependency identities |
| Existing SoundTouchJS | Editor uniform warp | Preserve existing purpose; not a substitute for variable-tempo placement metadata |
| Rubber Band | Alternative stretch | Not baseline; exact license/distribution obligations and quality gain must justify it |
| Matchering; pedalboard | Mastering/effects alternatives | Not baseline; reuse current FFmpeg/mixer/DSP first, respect source-copy policy |
| pyloudnorm | Loudness | Unnecessary duplicate for current baseline; existing FFmpeg measurement already serves this role |
| Tone.js | Browser scheduling | Optional only if a live sampler needs it; exact-artifact preview avoids another audio engine initially |
| FluidSynth | Whole-bank SoundFont playback | Optional X04; preserve bank-only terms and platform packaging obligations |
| sfizz | SFZ playback | Optional renderer alternative; inspect maintenance/platform status before reliance |
| MTS-ESP | External microtuning sync | Optional X04 for external synth users; not needed for offline cents-aware rendering |
| DDSP | Learned timbre synthesis/transfer | Research-only X05 with owned training data and a defined timbre goal |
| ACE-Step base/XL-base | Complete/Lego/Extract | Plan 05 pinned setup and runtime proof; do not infer base support from Turbo/SFT |
| ACE-Step Turbo/SFT/XL-SFT | Existing generation/Cover/Repaint | Preserve existing capabilities and model-specific guards |
| ACE-Step v1 | Older generation | Not baseline; no replacement of current integration without a measured reason |
| Stable Audio 3 | Existing instrumental generation and proposed conditioning | Plan 05 verifies the pinned MLX route for each variant/mode; terms remain separate from source code |
| Stable Audio Open | Older audio generation | Not baseline; evaluate only for a concrete capability unavailable in current integration |
| YuE2 | Existing native music/planning | Preserve existing AR/NAR workflow and model-specific adapters; explicit restriction handling |
| YuE v1 | Alternate model family | Optional X05; compare real runtime/conditioning/terms instead of assuming interchangeability with YuE2 |
| DiffRhythm | Reference-conditioned generation | Optional research review; include MuQ/conditioning dependencies, not just the headline checkpoint license |
| MusicGen / AudioCraft | Melody/audio conditioning | Restricted experiment only; not default or commercially cleared by source MIT |
| SongGeneration / LeVo | Song generation | Restricted research option; NVIDIA/platform and exact terms gate any integration |
| Magenta RealTime 2 | Live streaming music | X06; distinct live session architecture and measured Mac performance required |
| HeartMuLa; MuLaCover | Former generation/cover family | Retired by explicit user request. No reinstall, wrapper, trainer or generation route planned |
| fadtk; frechet-audio-distance | Distribution-level audio evaluation | Choose one after embedding/license/sample-size audit; integrate in plan 07 when useful |
| CLAP | Text/audio diagnostic similarity | Optional plan 07 metric with exact model identity; not cultural approval |
| mir_eval | Annotated music metrics | Use for appropriate beat/onset/key/section tests when its assumptions match fixtures |
| SpeechBrain ECAPA | Existing speaker similarity | Reuse optional screening; never claim it proves identity/consent |
| Whisper | Lyric/intelligibility diagnostics | Optional existing transcription path; document language limitations and reference text |
| MERT | Music embeddings | Restricted research metric if allowed; not default for an unrestricted evaluation package |

## Reusable experiment checklist

Every selected X-task follows these concrete steps. An experiment's outputs are code/prototype evidence and a decision; it is not counted as a shipped integration.

1. Write one measurable question and a baseline comparison using existing tools.
2. Inspect the exact upstream source/API and full dependency graph; record source commit, package/wheel hashes, platform requirements, model/encoder/codec identities, licenses and acquisition method.
3. Calculate download, unpack, conversion and scratch space. Review the proposed setup plan before a large optional install; do not start downloads from a generation click.
4. Add a pinned isolated setup/capability prototype in the existing module mechanism. Never add heavy models to the base FastAPI environment just to make imports work.
5. Add a tiny temporary-fixture worker smoke with no model downloads during unit tests. Mock only the actual external boundary, not all orchestration.
6. Measure the selected Mac path with a short rights-reviewed fixture. Record hardware/runtime, cold/warm load, time, peak memory/disk, output identity and failure logs.
7. Force cancellation, crash, missing-file, corrupted-file and network-disabled cases. Verify descendant exit, lock release, temporary file containment and recoverable errors.
8. Compare blind or objective results with the baseline. Record a decision: adopt for a defined capability, keep as an explicit experiment, or decline and explain the measured reason.
9. Only after adoption, add its generated contracts, UI help/status, manifests, installer/desktop coverage and a maintenance owner/document. No 'supported' badge from an upstream README alone.

## X01 — Optional symbolic AI patterns

- [ ] Compare rule-based variation/humanise with GrooVAE and one infill candidate on the same 10 patterns and fixed seeds where supported.
- [ ] Validate checkpoint terms and all downloaded assets; reject unreviewed browser-time fetches.
- [ ] Preserve user-locked hits, instrument roles, meter, scale, tempo and pattern length. Reject invalid or oversized model output at the backend boundary.
- [ ] Measure editability, timing, variety and latency. MIDI output must not exceed the sampler's range, event or polyphony budgets.
- [ ] If browser inference is selected, account for model memory/download, CSP/offline behavior, teardown and strict typed output validation; the server still validates saved patterns.
- [ ] Adopt only if it offers an audible/useful improvement over the existing rule engine without breaking reproducibility and recovery.

## X02 — Optional structure, chord and transcription analysis

- [ ] Compare generic section boundaries against msaf and allin1 on manually annotated held-out excerpts. Keep generic labels distinct from verse/chorus predictions.
- [ ] Audit auxiliary models for allin1/madmom/SongFormer and record restrictions in every resulting analysis artifact.
- [ ] Evaluate chord recognition only if mashup planning or regional harmony needs it; test relative-key ambiguity, detuning, dense mixes and non-Western tuning.
- [ ] Compare Basic Pitch against the existing native transcription for a defined monophonic/reference task. Do not substitute a tool that fails the required score format.
- [ ] Maintain the same AnalysisResult schema and source/version hashes; add method identity and availability, not a competing analysis document.
- [ ] Measure accuracy, decode/runtime/memory and cancellation; keep the baseline available when optional dependencies fail.

## X03 — Optional conversion engines beyond the RVC baseline

- [ ] Compare the pinned RVC path with Applio only after plan 02's registered model and replacement lifecycle are working.
- [ ] Check actual checkpoint/index compatibility, CLI arguments, pitch extractor and required encoder model terms from source. A UI badge does not establish interoperability.
- [ ] Test Mac CPU/MPS paths explicitly; CUDA evidence is separate. Never infer Apple acceleration from generic PyTorch support.
- [ ] Reuse voice authorization, captured model hashes, replacement stems and result versions. Do not create a parallel consent or path-based unvalidated model picker.
- [ ] Evaluate so-vits-svc only against a clearly identified unresolved quality need. OpenVoice remains a speech experiment until a singing evaluation justifies a claim.

## X04 — Optional instrument renderers and external tuning

- [ ] Compare the own sampler with FluidSynth for an actual whole-bank use case and sfizz for an actual SFZ articulation gap.
- [ ] Verify exact sample/bank redistribution rules and native binary obligations; include required notices in desktop packages.
- [ ] Test per-note cents, sustain/release, round-robin samples, loop boundaries and multi-voice pitch bends. Unsupported details must be visible, not dropped silently.
- [ ] Implement a renderer adapter only after the gap is demonstrated; both preview and export use the same chosen renderer/version.
- [ ] Add MTS-ESP only for a defined external-synth session. Disconnection, device tuning reset and compatibility require their own acceptance tests; it is not required for saved audio rendering.

## X05 — Optional new generation/prepared-vocal experiments

- [ ] Write a concrete capability gap for each proposed YuE v1, DiffRhythm, MusicGen, LeVo, DiffSinger or DDSP experiment; do not make a generic selector collecting model names.
- [ ] Audit all conditioning models, training sources and voicebanks; honor NC/research limits and preserve the default permissive workflow.
- [ ] Test the exact input/output format, sample rate, duration, reference conditioning and adapter compatibility. Similar names do not mean shared LoRAs.
- [ ] Run a short Mac-first smoke only after setup evidence and disk budget exist; use an optional reviewed NVIDIA package if the native trainer/runtime genuinely requires it.
- [ ] Keep generated prepared vocals distinct from the strict human-guide production route. No new model may silently bypass that route's source/consent gates.
- [ ] Adopt only a measured useful result with complete lifecycle/provenance integration. HeartMuLa stays excluded regardless of catalog comparisons.

## X06 — Optional Magenta RealTime 2 live-jam workflow

- [ ] Inspect the current official streaming API and MLX runtime; pin the chosen small/base model and exact source/weight terms.
- [ ] Measure sustained real-time generation on the target M4 Max before proposing a low-latency interface. Upstream device statements are not our benchmark.
- [ ] Define a bounded session owner: start, prompt/style update, buffered playback, stop, underrun, model failure and shutdown. Apply existing GPU admission to prevent overlap with training/generation.
- [ ] Persist captured audio chunks independently of the browser. On connection loss retain completed audio and show whether a live session still exists; do not promise a crashed streaming model can resume state.
- [ ] Bound ring buffers and disk capture, signal underruns honestly, avoid growing memory with session duration and clean up audio graphs on stop/navigation.
- [ ] Publish captured audio with exact session/model/prompt provenance. A live stream is a separate artifact, not a resumable ordinary queue item without adaptation.
- [ ] Test 30-second, five-minute and interruption sessions; measure latency/underruns/memory/disk and run a listening comparison before enabling an experimental label.

## Expansion beyond the initial six regional groups

The report names Vietnam, Philippines, Mongolia, Central/West Asia and broader Carnatic coverage as later scope. Each expansion needs instrument data, source-specific tuning/phrasing, rights evidence, independent musician review and the same preset acceptance checks. Do not represent these traditions by copying another region's preset and changing its name.

**Verification status:** Catalog and experiment plan only. No candidate was installed, benchmarked or accepted for integration in this planning task.
