# OpenFabric Studio desktop app — maintained mchosc fork (experimental)

This is the Electron shell for [OpenFabric-Studio/openfabric-studio](https://github.com/OpenFabric-Studio/openfabric-studio), based on [inikolax/remiqora](https://github.com/inikolax/remiqora) by Nikolay Cherkashin ([inikolax](https://github.com/inikolax)). **0.3.0-dev.0 is an unreleased source snapshot:** no fork installer or release tag has been published. Upstream v0.2.1 downloads are historical and do not include this fork’s changes.

The shell runs the same FastAPI backend and built Vue UI. First-run setup checks hardware and installs the baseline music engines into a chosen folder; voice and generated-video engines require their [separate setup](../README.md). It starts the backend on a free loopback port and requests model shutdown when the window closes.

[Roadmap](../ROADMAP.md) · [Contributing](../CONTRIBUTING.md) · [Fork maintenance and release checks](../docs/fork-maintenance.md) · [Fork issues](https://github.com/OpenFabric-Studio/openfabric-studio/issues)

## Platform status

| Platform | Snapshot status |
| --- | --- |
| Windows x64, NVIDIA RTX 20-series or newer, driver 580 or newer | Setup/NSIS packaging implemented; fork clean installation and GPU workflows unverified. Upstream reported an RTX 4080 run. |
| macOS, Apple Silicon | Setup/DMG packaging implemented; fork clean installation and GPU workflows unverified. Upstream reported a manual Mac run. |
| Linux | Packaging configuration exists; desktop first-run setup reports unsupported. Use the repository’s Linux source scripts. |

The Windows baseline prebuilt engine requires CUDA-capable hardware and driver 580 or newer. Upstream reported roughly 30 GB of downloads and 35 GB on disk. Setup estimates space from pending components with a 50% staging/cache allowance; completed components do not inflate update progress or space requirements. These are estimates, not a fork installation benchmark. CPU tests and a successful installer build do not establish GPU or platform support.

## Data folders and existing installations

The fork has app ID `io.github.mchosc.remiqora`, its own Electron preferences, and `OpenFabric Studio-mchosc-*` installer names. **The default data root remains `Remiqora`:** `%LOCALAPPDATA%\Remiqora` on Windows, `~/Library/Application Support/Remiqora` on macOS, or `$XDG_DATA_HOME/Remiqora` (normally `~/.local/share/Remiqora`) on Linux.

An upstream custom-folder selection does not transfer to the fork’s preferences automatically. Back up the library, stop the upstream app, and explicitly choose the existing folder during fork setup if you intend to reuse it. Choose a separate folder for independent testing. **Never run upstream and fork processes that write the same library at the same time.** The different desktop identity does not isolate shared databases or engines.

Settings supports the existing Data folder migration/restart workflow; do not move catalog files by hand. The first-run root also contains engines, tools and caches. Electron preferences live separately in its user-data folder. See the [release checklist](../docs/fork-maintenance.md) for copied-library migration checks before adopting a candidate.

## Run from source

Use Node.js 22.12 or newer. From a fresh checkout:

```bash
git clone https://github.com/OpenFabric-Studio/openfabric-studio.git
cd remiqora/frontend
npm ci
npm run build
cd ../desktop
npm ci
node node_modules/electron/install.js
npm start
```

The source app uses the checkout’s `backend/` and `frontend/dist`. First-run setup may download engines and weights; it is not a lightweight unit test. For browser launchers and refreshing an existing backend environment, see the [root installation guide](../README.md#-installation).

## Build an installer

From the checkout root:

```bash
cd frontend
npm ci
cd ../desktop
npm ci
npm run dist
```

Outputs live under `desktop/dist/` with `Remiqora-mchosc-*` names. Build macOS packages on macOS. `npm run dist:dir` creates an unpacked application for testing.

The build runs the frontend’s strict type check/build, copies backend sources, frontend assets and the ACE-Step/model-download patches into `resources/`, and refuses to package detected `.env`, databases or virtualenvs. Linux `.env.setup` proposals are excluded. It does not bundle model weights. Builds are unsigned and can trigger platform security prompts.

The [Desktop app workflow](../.github/workflows/desktop.yml) requires CI verification before packaging. PR/manual runs keep workflow artifacts; a matching version tag can create a **draft** prerelease with checksums. Publication requires manual review and real platform checks in [fork maintenance](../docs/fork-maintenance.md). There is no promised release date.

## First-run layout

| Path under chosen root | Content |
| --- | --- |
| `tools/uv`, `tools/python`, `tools/ffmpeg` | uv, managed Python 3.12 and pinned FFmpeg binaries |
| `engines/YuE2` | Pinned audio.cpp engine and its models |
| `engines/ACE-Step-1.5` | Pinned source, ACE-Step patch, environment and checkpoints |
| `engines/Demucs` | Demucs environment |
| `backend-venv` | Backend Python environment |
| `data`, `logs` | Library catalog, generated media and logs |
| `cache/` | Model/download/uv caches |

Pinned component versions and hashes are in [manifest.json](manifest.json). Verified downloads support resume/retry; `state.json` records completed versions and existing files. When changing a pin, inspect the publisher’s release/source and update its URL, digest and size as applicable, then run the downloader/setup regressions. Model and engine licenses remain separate from this repository’s [MIT license](../LICENSE); preserve upstream attribution.

ACE source upgrades stage and patch a replacement before promotion. Interrupted swaps recover `checkpoints`; if both trees contain checkpoints, both remain. Conflicts are retained in `engines/ACE-Step-1.5.previous`, or `ACE-Step-1.5.preserved-*` across later upgrades. Review those directories manually before removing them. The installed source version includes the full pinned commit and patch SHA256.

The local `model-manager-resume` update applies [yue-model-resume.patch](../external/patches/yue-model-resume.patch) to the release’s Python downloader. It saves remote content identity, validates SHA256 or Git blob SHA1, serializes writers, and keeps failed/cancelled staging directories compatible with `clean-partial`. Unknown digests are rejected rather than accepting file length as verification. Disk/writability checks run inside the installer, including offline local updates. Changing the document version query after upgrades retains the backend origin and browser storage.

## Verification switches

| Variable | Effect |
| --- | --- |
| `REMIQORA_HOME` | Override the default setup root |
| `REMIQORA_USER_DATA` | Isolate Electron’s saved preferences |
| `REMIQORA_SKIP_COMPONENTS` | Skip listed setup components, e.g. `ace-step,demucs,weights` |
| `REMIQORA_LANG` | Force setup language: `en` or `ru` |
| `REMIQORA_DEVTOOLS` | Open source-run DevTools |

`npm test` uses Node’s test runner and an offline Python 3 harness for downloader/setup/lifecycle behavior. Set `PYTHON_BIN` if Python is not available as `python3` (`python` on Windows). Linux script tests use fake tools and are skipped on Windows. Tests use temporary configuration/data/engine paths and do not download GPU weights. Follow [AGENTS.md](../AGENTS.md) and the isolated full checks in [fork maintenance](../docs/fork-maintenance.md).

[test/e2e/full.js](test/e2e/full.js) is the separate Windows/NVIDIA full-install/generation harness. It downloads real components and requires substantial disk/time; inspect its header and select an isolated folder before explicitly running it. Existing upstream execution reports do not verify the current fork. Unsigned-install behavior, clean installation, copied-library migration and GPU generation remain release checks, not claims made by CPU CI.

## Known gaps

- No code signing or automatic updater.
- Desktop Linux first-run installation is not implemented.
- Current fork installer/downloaded-build behavior is not yet verified on Windows or macOS.
- Model download progress can be estimated rather than exact.
- Windows setup uses built-in `tar.exe` (Windows 10 1803 or newer).
