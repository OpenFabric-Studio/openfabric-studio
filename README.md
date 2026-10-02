<p align="right"><b>English</b> · <a href="README.ru.md">Русский</a></p>

<p align="center">
  <img src="frontend/public/favicon.svg" width="88" height="88" alt="OpenFabric Studio">
</p>

<h1 align="center">OpenFabric Studio</h1>
<p align="center"><i>Local voice, music, talking audio, and consistent characters</i></p>

**Independent MIT project** under [OpenFabric-Studio](https://github.com/OpenFabric-Studio/openfabric-studio).

Derived from [mchosc/remiqora](https://github.com/mchosc/remiqora), itself based on [inikolax/remiqora](https://github.com/inikolax/remiqora) by Nikolay Cherkashin ([inikolax](https://github.com/inikolax)). See [NOTICE](NOTICE) and [LICENSE](LICENSE) for attribution. This repo is **not** a GitHub fork network child of Remiqora; it reuses code and ideas under MIT with credit.

<p align="center">
  A local GPU studio shell for <b>music generation</b>, <b>voice cloning</b> (singing today; talking / audiobooks ahead), and <b>consistent character video</b> for songs, reels, and music videos — one Vue + FastAPI app with a multitrack DAW.
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
| **Voice** | Clone singing voices today; expand toward natural talking voice and audiobook creation. |
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
| **Video Studio** | Shot lists / LTX-oriented video workflow (experimental) |
| **Demucs / DAW** | Stems and multitrack timeline |

UI languages: English / Russian. Brand palette: near-black + indigo (`#4F46E5` / `#6366F1`).

---

## Environment variables

Prefer **`OPENFABRIC_*`**. Legacy **`REMIQORA_*`** names are still read as fallbacks:

| Canonical | Legacy alias |
|-----------|--------------|
| `OPENFABRIC_DATA_DIR` | `REMIQORA_DATA_DIR` |
| `OPENFABRIC_LOG_DIR` | `REMIQORA_LOG_DIR` |
| `OPENFABRIC_CONFIG` | `REMIQORA_CONFIG` |
| `OPENFABRIC_HOME` | `REMIQORA_HOME` |

See `backend/.env.example`.

---

## Quick start (source)

```bash
git clone https://github.com/OpenFabric-Studio/openfabric-studio.git
cd openfabric-studio
# Follow setup_prereqs / setup_models scripts for your OS, then:
./dev.sh   # or dev.bat on Windows
```

Desktop packaging is experimental; CI packaging runs only on tags / manual dispatch to save Actions minutes.

---

## License and liability

Application code: **MIT** — retain Nikolay Cherkashin’s copyright notice and this project’s NOTICE. Generated audio/video and third-party model weights have separate terms; you are responsible for lawful use of voices, likenesses, and media.

Upstream Remiqora remains the work of its authors; OpenFabric Studio is an independent derivative.
