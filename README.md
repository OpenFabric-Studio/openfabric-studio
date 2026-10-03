<p align="center">
  <img src="frontend/public/favicon.svg" width="88" height="88" alt="OpenFabric Studio">
</p>

<h1 align="center">OpenFabric Studio</h1>
<p align="center"><i>Local voice, music, talking audio, and consistent characters</i></p>

**Independent MIT project** under [OpenFabric-Studio](https://github.com/OpenFabric-Studio/openfabric-studio).

Derived from [mchosc/remiqora](https://github.com/mchosc/remiqora), itself based on [inikolax/remiqora](https://github.com/inikolax/remiqora) by Nikolay Cherkashin ([inikolax](https://github.com/inikolax)). See [NOTICE](NOTICE) and [LICENSE](LICENSE) for attribution. This repo is **not** a GitHub fork network child of Remiqora; it reuses code and ideas under MIT with credit.

<p align="center">
  A local GPU studio shell for <b>music generation</b>, <b>voice cloning</b> (singing, speech and audiobooks), and <b>consistent character video</b> for songs, reels, and music videos — one Vue + FastAPI app with a multitrack DAW.
</p>

<p align="center">🚧 Early development — expect breaking changes. Not a stable release.</p>

[Roadmap](ROADMAP.md) · [Contributing](CONTRIBUTING.md) · [Maintenance](docs/fork-maintenance.md) · [Issues](https://github.com/OpenFabric-Studio/openfabric-studio/issues) · [NOTICE](NOTICE)

<p align="center">
  <img alt="Status" src="https://img.shields.io/badge/status-in%20development-eab308?style=flat-square">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-22c55e?style=flat-square"></a>
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-0f0f14?style=flat-square">
  <img alt="Brand" src="https://img.shields.io/badge/brand-indigo%20%2B%20black-4F46E5?style=flat-square">
</p>

---

## Goals

| Area | Direction |
|------|-----------|
| **Voice** | Clone singing voices; preview speech narrators and create recoverable audiobooks. |
| **Music** | Local ACE-Step / YuE2 generation, stems, DAW mixing (heritage from Remiqora). |
| **Video / characters** | Generate images and train **consistent characters** for music videos and short-form reels. |

Daily work also watches related open-source projects (e.g. VoiceStudio, LocalAI) for reusable MIT-compatible ideas — always attributed.

---

## What's inside (current shell)

| Module | What it does |
|--------|----------------|
| **ACE-Step 1.5** | Text/style music generation, covers, section edits |
| **YuE2-3B** | Longer tracks with CoT / ABC planning |
| **Voice Clone** | Seed-VC singing voices, prep, compare |
| **Speech profiles** | Reference clips for speech and audiobooks, with four licensed English starter voices |
| **Video Studio** | Shot lists / LTX-oriented video workflow (experimental) |
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

Optional singing and speech CLI wrappers are `setup_voice.bat` / `./setup_voice.sh` and `setup_speech.bat` / `./setup_speech.sh`. They use the same reviewed catalog as Settings; model downloads require explicit `--download-models`. Preview a CLI plan without installing with the backend environment’s Python and `backend/scripts/setup_modules.py --feature singing`.

Generated video currently requires **Apple Silicon macOS**. CPU-only Seed-VC singing **training** is unsupported; conversion and training have different hardware requirements. Linux/Intel Mac desktop first-run installation is unavailable; use the source route. Clean Windows/Linux installations and GPU workflows still require real platform verification. See [platform setup and troubleshooting](docs/platform-setup.md).

Desktop packaging is experimental. The first-run desktop bootstrap installs uv and the backend; optional engines and weights are selected inside the app. CI packaging runs on tags or manual dispatch. macOS installers are ad-hoc signed (no Developer ID): after download, allow the app once via System Settings → Privacy & Security → Open Anyway. See [desktop/README.md](desktop/README.md).

---



## Talking / audiobook path

Singing voice cloning uses **Seed-VC** today.

Speech and audiobook synthesis uses an optional local **[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)** API (MIT). OpenFabric stores speech **voice profiles** (`/api/voice-profiles`) and offers four bundled English [starter reference voices](docs/speech-starter-voices.md), including previews, transcripts and attribution. These VCTK recordings are licensed separately under CC BY 4.0. Speech trials require an installed, running engine; missing engines are reported explicitly. Model weights are not downloaded by this repo.

Audiobook creation accepts unencrypted **MOBI** through Calibre, with editable saved chapters, inclusion choices, narrator previews and pause/resume controls. Original sources and completed audio stay in the library. See [ebook import and module setup](docs/ebook-and-module-setup.md) for usage and verification limits.

## License and liability

Application code: **MIT** — retain Nikolay Cherkashin’s copyright notice and this project’s NOTICE. Generated audio/video and third-party model weights have separate terms; you are responsible for lawful use of voices, likenesses, and media.

Upstream Remiqora remains the work of its authors; OpenFabric Studio is an independent derivative.
