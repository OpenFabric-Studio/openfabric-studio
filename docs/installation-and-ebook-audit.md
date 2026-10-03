# Installation, module status and ebook import audit

Audited 2 October 2026. This document records source inspection, a read-only
desktop plan-construction probe and a proposed design. It is not a clean-install
certification or an implementation-completion report. No packages or model
weights were installed during this audit.

## Findings that affect the requested work

| Finding | Evidence | Consequence |
| --- | --- | --- |
| Audiobook creation accepts chapter text, not ebook files. | `backend/app/audiobook_contracts.py`, `backend/app/api/routes_audiobooks.py`, `frontend/src/views/voice/AudiobooksPanel.vue` | Add import and text review before submitting narration. Existing editable chapter drafts can be reused. |
| Narration submits an entire chapter to a 120-second speech request. Workers are daemon threads without startup recovery, shutdown draining or cancellation. | `backend/app/audiobooks.py`, `backend/app/speech_clone.py`, `backend/app/main.py` | Whole-book generation needs bounded narration sections, retained completed sections, owned workers, cancellation and recovery. Adding an upload button alone does not establish reliable book generation. |
| Library migration omits audiobook, speech-profile and speech-trial folders and their stored absolute paths. | `backend/app/data_root.py` | Preserve and migrate these existing data types before adding more durable audiobook data. Use copied temporary libraries for tests; do not move an active library. |
| Desktop bootstrap requires all baseline music components before opening the app. | `desktop/src/bootstrap/components.js`, `desktop/src/bootstrap/run.js`, `desktop/src/main.js` | Start with a minimal backend, then let users select features and inspect downloads. |
| Desktop planning dereferences absent platform assets before the compatibility check. | `desktop/src/main.js`, `desktop/src/bootstrap/components.js` | Read-only probes reproduced failures for Linux x64/arm64 and Intel macOS. Unsupported combinations must yield a typed, useful result rather than a planning exception. |
| Linux desktop assets and packaging are absent. Current baseline targets Windows x64 CUDA and macOS arm64 Metal. | `desktop/manifest.json`, `desktop/src/paths.js`, `.github/workflows/desktop.yml` | A Settings redesign cannot establish Linux installation support. Provide a verified Linux source path and add managed installation/packaging only with supported assets or builds. |
| macOS desktop media installation supplies FFmpeg without FFprobe. | `desktop/src/bootstrap/components.js`, `backend/app/video_render.py`, `backend/app/reference_imports.py` | Provision and verify both tools; clean machines must not depend on an incidental Homebrew installation. |
| Optional desktop engines default to resource-relative external directories. | `desktop/src/server.js`, `backend/app/config.py`, `backend/app/voice_separation.py` | Export the chosen managed engine layout for Seed-VC, GPT-SoVITS, LTX and RoFormer. Packaged resources are not writable installation destinations. |
| Optional voice/speech scripts use POSIX interpreter/activation paths and moving upstream revisions. | `setup_voice.sh`, `setup_speech.sh`, `setup_roformer.sh` | Reuse platform-aware Python installation helpers, pin reviewed sources and expose partial installations honestly. |
| Many readiness checks only find a directory, file or responding HTTP endpoint. | `backend/app/speech_clone.py`, `backend/app/voice_separation.py`, `desktop/src/bootstrap/components.js` | Separate presence, dependency checks, weight verification, hardware compatibility, runtime state and successful capability verification. |
| Source launchers refresh dependencies only when an environment is absent; Linux launcher instructions are fragmented. | `dev.*`, `prod_run.*`, `README.md` | Check dependency versions on upgrade and document one supported launch/install path for each platform. |
| Existing desktop setup cancellation/state ownership is weaker than the backend job helpers. | `desktop/src/proc.js`, `desktop/src/bootstrap/extract.js`, `desktop/src/bootstrap/state.js`, `desktop/src/main.js` | Await setup cancellation and subprocess exit, serialize mutations and recover interrupted extraction/promotion before exposing installation through multiple UI surfaces. |

## Actual integration boundaries

The following is an integration inventory, not evidence that every listed
platform has passed clean installation or inference tests.

| Feature | Current integration boundary | Status evidence to reuse |
| --- | --- | --- |
| ACE-Step music and LoRA | Upstream supports CPU/CUDA and Apple acceleration. Current desktop baseline is Windows CUDA/macOS arm64; CPU performance is not established here. | Process status plus `/api/ace/health` model initialization fields. |
| YuE music | Current defaults use CUDA on Windows/Linux and Metal on macOS. Native CPU builds exist as an advanced setup path. | Owned process, health/backend and loaded model inventory. |
| SheetSage / MuScriptor | Child capabilities of the native YuE runtime, not independent services. | Required model artifacts and runtime capability checks. |
| GPT-SoVITS speech / audiobook | Optional local API and model environments. A checkout marker or HTTP response is insufficient proof of synthesis. | Environment/weight checks, API identity and an explicit short synthesis verification. |
| Seed-VC | Installed inference code selects CUDA, MPS or CPU; its training code selects MPS/CUDA without a CPU fallback. | Report conversion and training readiness separately. |
| Demucs / RoFormer | Separate Python environments and model dependencies. Local adapters have differing hardware support. | Interpreter/import checks, pinned installer receipts, required model verification and optional runtime checks. |
| Generated LTX video | Current MLX adapter explicitly requires macOS ARM64. Windows/Linux would require another adapter. | Per-option video readiness, hardware support and verified artifacts. |
| Cover / visualizer / export | CPU media workflows using FFmpeg, FFprobe and Python media packages. | Executable/version/codec and Python import checks. |
| Reference transcription | Whisper.cpp executable, compatible model and compiled backend. | Existing reference capability checks, extended with version/model compatibility. |
| URL reference import | yt-dlp/EJS, Deno and media tools. | Existing bounded capability checks, distinguishing missing from incompatible versions. |
| MOBI ebook conversion | Not implemented. Optional Calibre executable required by the proposed design. | Executable discovery, version check and bounded test conversion. |

## Recommended architecture

Three approaches were considered:

- **Checks and guided manual instructions:** smallest implementation, but users
  still manage environments and model downloads outside the app.
- **Shared feature-based setup service (recommended):** more initial work, with
  one dependency plan used by desktop, browser Settings and CLI. Users install
  only selected features and can recover interrupted work.
- **Extend the current all-components desktop installer:** reuses more of today's
  UI, but retains compulsory downloads and does not solve source/browser setup
  or the duplicated optional-engine scripts.

1. **Reuse a small desktop bootstrap.** It installs managed uv/Python and the
   minimum backend environment. Opening the app should not require downloading
   every music model. Existing configured engines remain usable.
2. **Keep one installation service on the backend.** Typed module inventory,
   dependency plans and durable installation jobs serve Settings, onboarding and
   CLI adapters. Do not build a second full installer in the Vue UI or three
   independent implementations in shell, PowerShell and Electron.
3. **Share pinned artifacts and a managed layout.** Sources, executable builds,
   package environments and weights have reviewed identities and platform
   constraints. Do not overwrite discovered external installations. Stage managed
   replacements and promote only after verification; preserve checkpoints.
4. **Use owned work.** Reuse `job_lifecycle.py` and `video_process.py` for process
   supervision, cancellation, Windows Job Objects and shutdown draining.
   Serialize work on the same engine and block replacement during generation or
   training. Model downloads remain outside Git and separate from the media
   library.
5. **Restrict installation inputs.** Accept catalog module IDs and supported
   options, not arbitrary commands, package names, URLs or destinations. Reject
   cross-origin browser installation mutations. Keep loopback binding. Privileged
   system actions and third-party license acceptance remain explicit user steps.
6. **Report facts independently.** Platform support, configuration, source,
   environment, model verification, runtime state, recent capability verification
   and restart requirements are distinct. Polling is bounded and read-only: it
   must not load GPU models, download files, rewrite symlinks or repeatedly hash
   gigabytes. Stale/offline data cannot retain a current green status.

## Proposed user experience

Settings gains a **Setup & modules** section alongside Library, Audio and
Preferences. It shows the computer's supported acceleration, the managed install
location and grouped module rows with a specific next action: install, configure,
download weights, start, verify or repair. Individual dependency checks and logs
live in expandable details.

The wizard follows **computer check → choose features → review downloads →
install and verify**. Installing chosen features is the recommended default.
Downloaded byte progress is shown when measured; package installation uses named
steps rather than invented percentages. Downloads, disk allowance, manual steps
and applicable terms are shown before installation. Reload/cancel/retry retains
the operation record and completed work.

The existing 44-pixel header stays compact. Keep ACE-Step and YuE, then add Speech,
Singing, Video and Tools indicators. Each has a distinct icon, an accessible
status label, keyboard/touch tooltip and a route to the relevant Settings detail.
Expose individual libraries/models in Settings rather than adding an icon for
every package. Unsupported capabilities and optional uninstalled features are
not generic failures.

## MOBI import proposal

Use optional, separately installed **Calibre `ebook-convert`** to normalize
unencrypted MOBI into EPUB. Its official documentation covers MOBI6/KF8 and the
conversion CLI. This avoids maintaining an additional MOBI parser in the
application. Calibre is not installed at the inspected local PATH/default macOS
location. Distribution and installation details still need explicit platform
verification; do not claim automatic installation before it exists.

The flow is **upload → review extracted title/chapters → narrator and short
preview → generate**. Preserve the original source and submitted text. Follow
EPUB spine/TOC order; chapter detection can be uncertain and must show warnings.
Let users exclude front matter and edit titles/text. Never silently truncate
books or treat archive filename order as reading order. EPUB can reuse the same
reader; adding extra formats is a separate decision.

Use bounded upload/output/archive/text sizes, private random workspaces and
contained paths. Parse plain text without fetching remote resources or exposing
uploaded HTML. Reject unsafe archives/XML, unsupported or encrypted files with
stable errors. Conversion needs timeout/cancellation/shutdown ownership, and late
results cannot overwrite a newer draft. A failed import preserves existing edits.

Whole-book narration requires a reliability pass: bounded speech sections within
chapters, persistent completion records, sequential ownership of the shared
speech engine, pause/cancel/retry/resume, restart recovery, atomic publication and
retained completed audio. The current combined WAV export is the existing output;
additional codecs/containers should use the existing export settings when added.

## Delivery sequence and verification

These are three connected workstreams, not one cosmetic page change:

1. Harden audiobook ownership/storage integration and implement reviewed MOBI
   import and narration using the existing speech profiles.
2. Implement typed module status and the compact header/Settings experience.
3. Consolidate installation planning/jobs and extend platform installers, with
   recorded clean-environment results and explicit unsupported combinations.

Use temporary libraries/configuration and engine roots for automated tests.
Cover converter absence/failure, malicious and oversized input, TOC ordering,
draft preservation, cancellation/concurrency, interrupted section generation,
restart recovery and copied-library migration. Installer coverage must include
unsupported planning, Windows paths, packaged engine roots, FFprobe, partial or
corrupt environments/models, interrupted extraction/promotion and blocked updates
during active work. Frontend tests must cover keyboard access, state labels,
stale responses, reload, draft preservation and responsive layouts.

Required checks remain frontend tests/build, backend regressions, generated
contract drift, strict Python platform checks and affected desktop tests.
Passing Ubuntu CI or packaging a Windows/macOS installer is not proof of a clean
installation or GPU inference. This host can directly verify macOS; Windows/Linux
claims require appropriate CI or actual platform evidence.

## Primary sources

- [Calibre supported formats](https://manual.calibre-ebook.com/faq.html)
- [Calibre conversion CLI](https://manual.calibre-ebook.com/generated/en/ebook-convert.html)
- [Calibre macOS CLI location](https://manual.calibre-ebook.com/generated/en/cli-index.html)
- [EPUB reading order](https://www.w3.org/TR/epub-33/#sec-spine)
- [ACE-Step installation](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/INSTALL.md)
- [audio.cpp](https://github.com/0xShug0/audio.cpp)
- [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS/blob/main/README.md)
- [Seed-VC](https://github.com/Plachtaa/seed-vc)
- [Demucs](https://github.com/facebookresearch/demucs)
- [Pinned RoFormer inference](https://raw.githubusercontent.com/ZFTurbo/Music-Source-Separation-Training/84b1eac0887756b4f1a9d7a1ff49105939749ed2/inference.py)
- [LTX MLX adapter](https://github.com/dgrauet/ltx-2-mlx)
- [Whisper.cpp](https://github.com/ggml-org/whisper.cpp)
