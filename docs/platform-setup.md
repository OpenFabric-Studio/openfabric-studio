# Platform setup

Start with the [source installation route](../README.md#installation) for your system. Desktop auto-bootstrap currently supports Windows x64 and Apple Silicon macOS. Other desktop targets show source instructions before attempting downloads. This is a support boundary, not a claim that clean installation or GPU generation has been verified on each supported platform.

## Minimal startup

The desktop first run installs pinned uv and a managed Python 3.12 backend environment. It does not require NVIDIA or install music, speech, singing, separation, or video models before opening the studio. It verifies the executables, required backend imports and Python dependency consistency before recording completion.

Source `prod_run.sh` / `prod_run.bat` builds the frontend and starts the backend on loopback. `dev.sh` / `dev.bat` starts both backend and Vite, with fixed local development origins. All wrappers share `desktop/scripts/launch-source.js`; Windows uses `.venv/Scripts/python.exe`, POSIX uses `.venv/bin/python`. Requirements changes refresh the existing environment before startup, and frontend lockfile or Node major-version changes run `npm ci`. If a previous environment uses another Python version, preserve it and recreate `backend/.venv` with Python 3.12 rather than modifying it blindly.

The launchers prefer checked-in `backend/requirements.lock`, fingerprint its contents, and install with `--require-hashes --only-binary :all:`. `backend/requirements.txt` remains the input specification. Only source fixtures or older resource bundles without the lock fall back to the input file. Regenerate the universal Python 3.12 lock deliberately after reviewing dependency changes:

```bash
uv pip compile backend/requirements.txt --python-version 3.12 --universal --generate-hashes --no-annotate --output-file backend/requirements.lock
```

Hash verification establishes artifact integrity, not clean-machine runtime or GPU support. Wheel availability on each OS still requires platform evidence.

## Optional modules and paths

Use **Settings → Setup & modules** to check the computer, select features, review requirements, install, and verify. Heavy downloads are explicit. A checkout directory, an executable filename, or a responding HTTP server alone does not establish model readiness.

Desktop exports `OPENFABRIC_MODULE_ROOT` pointing to the selected writable home. Defaults under it are:

| Relative path | Module |
| --- | --- |
| `engines/ACE-Step-1.5`, `engines/YuE2`, `engines/Demucs` | Music and separation |
| `engines/seed-vc`, `engines/gpt-sovits` | Singing and speech |
| `engines/ltx-2-mlx`, `engines/Music-Source-Separation-Training` | Generated video and optional RoFormer |
| `tools/ffmpeg/bin` | FFmpeg **and** FFprobe |
| `tools/uv`, `tools/python`, `backend-venv`, `cache`, `logs` | Runtime, downloads and logs |

Explicit inherited environment and `backend/.env` engine choices take priority over managed defaults. Source launchers do not replace your `.env` or inject a different engine root. To select a source managed home, set `OPENFABRIC_MODULE_ROOT` deliberately and preserve existing explicit engine paths. Fresh source installations default to `~/.openfabric-studio/runtime` when an implicit legacy checkout is absent. Never move a library during active work or run two applications against the same writable library.

For CLI setup, use the backend Python:

```text
backend/scripts/setup_modules.py --feature singing
backend/scripts/setup_modules.py --feature singing --install
backend/scripts/setup_modules.py --feature singing --install --download-models
```

These are arguments to Python, not executable shell commands. Use `backend/.venv/Scripts/python.exe` on Windows and `backend/.venv/bin/python` on macOS/Linux. `--root /absolute/path` is an explicit operator choice; it is not a browser-supplied destination. `setup_voice` and `setup_speech` wrappers select fixed catalog features. `setup_video` preserves the existing video helper’s `--preflight`, `--pack`, and `--cache-dir` options. RoFormer keeps its separate pinned setup and reviewed model variant; it is not silently substituted for Demucs.

## Media tools and unsupported hardware

FFmpeg and FFprobe must both work. Windows provisioning uses the existing pinned publisher ZIP. Apple Silicon provisioning uses matching `n8.1.2-1` static binaries, with the FFprobe SHA256 and size verified against the [publisher release API](https://api.github.com/repos/shaka-project/static-ffmpeg-binaries/releases/tags/n8.1.2-1). The desktop manifest is copied byte-for-byte into packaged backend resources so the wizard shares the same pins. Other targets must install both tools using a trusted platform package manager and configure `FFMPEG_BIN_DIR` or PATH; no unverified asset is invented.

Generated video uses the MLX adapter and requires Apple Silicon macOS. Supporting Windows/Linux generated scenes needs a separate tested adapter. Cover/visualizer media modes have lighter dependencies. Seed-VC’s current training entry point selects MPS or CUDA and has no CPU fallback; CPU conversion does not imply CPU training support.

Cancellation stops owned process trees and waits for cleanup; resistant POSIX processes are force-stopped. Archives are screened for unsafe paths, links and special files. Atomic directory replacement retains the previous installation through a failed promotion and recovers interrupted swaps. Setup records and desktop preferences use atomic writes; preference read/modify/write updates are serialized.

## Verification limits

Automated desktop tests use temporary folders, local fixture servers, and fake installation tools. They cover unsupported platforms, partial installs, cancellation, descendant termination, recovery, private configuration preservation, package-refresh failures, and shared manifest packaging without downloading models. Actual Windows taskkill behavior, Linux/Windows dependency installation, signed installer behavior, and GPU inference remain separate platform checks.
