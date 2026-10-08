<p align="center">
  <img src="frontend/public/favicon.svg" width="88" height="88" alt="OpenFabric Studio">
</p>

<h1 align="center">OpenFabric Studio</h1>
<p align="center"><i>Local voice, music, talking audio, and consistent characters</i></p>

**Independent AGPLv3 project** under [OpenFabric-Studio](https://github.com/OpenFabric-Studio/openfabric-studio).

Derived from [mchosc/remiqora](https://github.com/mchosc/remiqora), itself based on [inikolax/remiqora](https://github.com/inikolax/remiqora) by Nikolay Cherkashin ([inikolax](https://github.com/inikolax)). See [NOTICE](NOTICE) and [LICENSE](LICENSE) for attribution. This repo is **not** a GitHub fork network child of Remiqora; it reuses code and ideas under MIT with credit.

<p align="center">
  A local GPU studio shell for <b>music generation</b>, <b>voice cloning</b> (singing, speech and audiobooks), and <b>consistent character video</b> for songs, reels, and music videos — one Vue + FastAPI app with a multitrack DAW.
</p>

<p align="center">🚧 Early development — expect breaking changes. Not a stable release.</p>

[Roadmap](ROADMAP.md) · [Contributing](CONTRIBUTING.md) · [Maintenance](docs/fork-maintenance.md) · [Issues](https://github.com/OpenFabric-Studio/openfabric-studio/issues) · [NOTICE](NOTICE)

<p align="center">
  <img alt="Status" src="https://img.shields.io/badge/status-in%20development-eab308?style=flat-square">
  <a href="LICENSE"><img alt="License: AGPL-3.0-only" src="https://img.shields.io/badge/license-AGPL--3.0--only-22c55e?style=flat-square"></a>
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-0f0f14?style=flat-square">
  <img alt="Brand" src="https://img.shields.io/badge/brand-indigo%20%2B%20black-4F46E5?style=flat-square">
</p>

---

## Goals

| Area | Direction |
|------|-----------|
| **Voice** | Clone singing voices; preview speech narrators and create recoverable audiobooks. |
| **Music** | Local ACE-Step / YuE2 generation, stems, DAW mixing (heritage from Remiqora). |
| **Video / characters** | Silent videos and reels. A local character LoRA trains on this Mac when the video engine Python is configured. A locked still is not training. |

Daily work also watches related open-source projects (e.g. VoiceStudio, LocalAI) for reusable ideas — always attributed. Reusing source requires checking its exact license and preserving its notices.

---

## What's inside (current shell)

| Module | What it does |
|--------|----------------|
| **ACE-Step 1.5** | Text/style music generation, covers, section edits |
| **YuE2-3B** | Longer tracks with CoT / ABC planning |
| **Voice Clone** | Seed-VC singing voices, prep, compare |
| **Speech profiles** | Reference clips for speech and audiobooks, with four licensed English starter voices |
| **Video Studio** | Local LTX pictures and optional quoted OpenRouter shots with approvals/export |
| **OpenRouter media** | Bring your own key for cloud speech, video and experimental Lyria music |
| **Demucs / DAW** | Stems and multitrack timeline |

UI language: English. Brand palette: near-black + indigo (`#4F46E5` / `#6366F1`).

---

## Environment variables

Use **`OPENFABRIC_*`** names (see `backend/.env.example`):

| Variable | Purpose |
|----------|---------|
| `OPENFABRIC_DATA_DIR` | Library / data root |
| `OPENFABRIC_LOG_DIR` | Log directory |
| `OPENFABRIC_CONFIG` | Path to `config.json` |
| `OPENFABRIC_HOME` | Desktop install home (Electron) |
| `OPENFABRIC_GPT_SOVITS_DIR` | Optional GPT-SoVITS checkout |

---

## Installation

The studio opens after a small backend setup. Choose optional features in **Settings → Setup & modules**, review downloads and hardware requirements, then install and verify them. Existing configured engine paths are preserved. Installing the app does not certify that every engine runs on every computer.

Use Git, **Node.js 22.12 or newer**, and [uv](https://docs.astral.sh/uv/getting-started/installation/). The source launcher creates a Python 3.12 backend environment, installs the hash-pinned backend lock, and refreshes dependencies when that lock or the frontend lockfile changes. It never runs the legacy multi-model setup scripts automatically.

```bash
git clone https://github.com/OpenFabric-Studio/openfabric-studio.git
cd openfabric-studio
```

| Platform | Prepare tools | Start the studio |
| --- | --- | --- |
| Windows x64 | `powershell -File setup_prereqs.ps1 -SkipHeavy`, then open a new terminal | `prod_run.bat` |
| macOS | `./setup_prereqs.sh` after installing Homebrew and accepting the Xcode Command Line Tools prompt | `./prod_run.sh` |
| Linux | Install Git, supported Node.js and uv using their official installation instructions | `./prod_run.sh` |

Open **http://127.0.0.1:9000** when the launcher reports that the backend is running. Ctrl+C stops the servers. Developers can use `dev.bat` / `./dev.sh` and open **http://127.0.0.1:5173**. The development launcher permits only the two local Vite origins for setup actions; the normal launcher remains on one local origin.

Optional singing and speech CLI wrappers are `setup_voice.bat` / `./setup_voice.sh` and `setup_speech.bat` / `./setup_speech.sh`. They use the same reviewed catalog as Settings; model downloads require explicit `--download-models`. Preview a CLI plan without installing with the backend environment’s Python and `backend/scripts/setup_modules.py --feature singing`. Kokoro, Chatterbox, Wan 2.2 TI2V-5B, and RVC use `./setup_kokoro.sh`, `./setup_chatterbox.sh`, `./setup_wan22.sh`, and `./setup_rvc.sh` (and the matching `.bat` files). Those wrappers clone and install only. They do not download weights. After the weights are on disk, call POST /api/local-engines/kokoro, /chatterbox, /wan (body engine wan22), or /rvc. Song videos stay on LTX.

The local LTX video engine currently requires **Apple Silicon macOS**. Optional OpenRouter picture generation uses a cloud provider and local FFmpeg conformance/export; it does not require a local GPU. CPU-only Seed-VC singing **training** is unsupported; conversion and training have different hardware requirements. Linux/Intel Mac desktop first-run installation is unavailable; use the source route. Clean Windows/Linux installations and GPU workflows still require real platform verification. See [platform setup and troubleshooting](docs/platform-setup.md).

Desktop packaging is experimental. The first-run desktop bootstrap installs uv and the backend; optional engines and weights are selected inside the app. CI packaging runs on tags or manual dispatch. macOS installers are ad-hoc signed (no Developer ID): after download, allow the app once via System Settings → Privacy & Security → Open Anyway. See [desktop/README.md](desktop/README.md).

---



## Talking / audiobook path

Singing voice cloning uses **Seed-VC** today.

Speech and audiobook synthesis uses an optional local **[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)** API (MIT). OpenFabric stores speech **voice profiles** (`/api/voice-profiles`) and offers four bundled English [starter reference voices](docs/speech-starter-voices.md), including previews, transcripts and attribution. These VCTK recordings are licensed separately under CC BY 4.0. Local speech trials require an installed, running engine; missing engines are reported explicitly. Model weights are not downloaded by this repo.

Audiobook creation accepts unencrypted **MOBI** through Calibre, with editable saved chapters, inclusion choices, narrator previews and pause/resume controls. Original sources and completed audio stay in the library. See [ebook import and module setup](docs/ebook-and-module-setup.md) for usage and verification limits.

## Optional cloud media

Open **Settings → Cloud providers** to configure your own OpenRouter key, check
its connection, set an estimate ceiling and refresh supported models. Music has
an experimental cloud tab; speech profiles support provider voices or separately
approved reference cloning; Video quotes one cloud shot at a time. Every paid
action needs an input-bound estimate and transfer approval. Local engines remain
independent. Completed results and request receipts live in the library.

Keys stay behind the backend, with native credential storage when available and
session/environment fallback. The estimate ceiling is not a billing cap: set a
spending limit on the provider key. Unknown submission outcomes never trigger an
automatic paid retry. Live billed quality and native stores on all three operating
systems remain unverified. See [OpenRouter setup, privacy and recovery](docs/openrouter-media.md).

## License and liability

OpenFabric Studio is offered under the **GNU Affero General Public License, version 3 only** (`AGPL-3.0-only`); see [LICENSE](LICENSE). Contributions use the same terms. The program comes without any warranty; you may redistribute and modify it under that license.

Inherited MIT code retains its original terms and copyright notices in [LICENSE-MIT](LICENSE-MIT), including credit to Nikolay Cherkashin. Preserve [NOTICE](NOTICE) and the applicable license notices when redistributing. Versions through commit `e72d55f`, before the 2026-10-08 transition, remain available under MIT.

AGPL permits commercial use. Distributing covered binaries requires providing their Corresponding Source under the license's terms. Modified versions used remotely over a network must prominently offer those users the Corresponding Source of that version at no charge. For official releases, use the exact release tag's source archive, including the build and installation scripts. A link to an unrelated or unmodified upstream version is insufficient for a modified deployment. See [AGPL sections 6 and 13](https://www.gnu.org/licenses/agpl-3.0.html).

Third-party engines, model weights, datasets and recordings retain their own licenses. The application license does not automatically apply to generated audio/video or grant rights to voices, likenesses or source media.

Upstream Remiqora remains the work of its authors; OpenFabric Studio is an independent derivative.
