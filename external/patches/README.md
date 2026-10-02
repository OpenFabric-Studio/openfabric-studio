# Patches for ACE-Step-1.5 and audio.cpp (YuE2)

Remiqora orchestrates two external inference engines that are **not**
vendored into this repository. `setup_models.ps1` clones the original
upstream projects and, for ACE-Step, applies the small patch in this folder
on top, so the engine exposes the extra bits Remiqora's backend actually
calls.

Only functional, API/engine-level changes are patched in. The custom
web-ui folders that used to ship inside each upstream checkout are **not**
needed and are excluded from the patch — Remiqora's own frontend
replaces them entirely.

## ace-step.patch

- Upstream: https://github.com/ace-step/ACE-Step-1.5
- Base commit: `ca1e85f`
- License: MIT
- Adds: `POST /cancel_task` and `POST /cancel_all_tasks` (cooperative
  cancellation for queued/running generation jobs — used by Remiqora's
  cancel button), a couple of job-store/runtime fields needed to support
  it, small LoRA/LoKr training-route additions, and a launch-script fix
  (`PYTHONUTF8=1` in `start_api_server.bat`, so nano-vllm's logging doesn't
  crash on non-English Windows locales).

## audio.cpp (YuE2)

`setup_models.ps1` clones `https://github.com/0xShug0/audio.cpp` (`dev`
branch — YuE2 support is dev-only upstream, not yet on `main`) and pins it
to a specific commit, but applies no YuE runtime patch by default. The optional
isolated source build below applies the two maintained runtime patches. This used to carry a small
patch that exposed the ABC plan a YuE2 generation actually used (model-built
or caller-supplied) as a response artifact — upstream's `dev` branch has
since implemented the same thing natively (`Yue2RunResult::plan_abc_text` in
`src/models/yue2/pipeline.cpp`, surfaced as a `"score"` artifact in
`src/models/yue2/session.cpp`), which Remiqora's frontend already reads
generically (`frontend/src/api/yue2.ts`, `abcFromResult()`), so the patch
was retired.

Current pinned source commit: `39f9013463053e206aa160f8453d734f78999b9d`, License: Apache-2.0. Desktop release archives are pinned separately in `desktop/manifest.json`.

### yue-model-resume.patch

Applied by Linux source setup and the desktop's local `model-manager-resume` component. Its base `tools/model_manager_v2.py` is byte-identical at the source pin and desktop `v0.8.1` release.

Inspired by [darkyeg's pinned recovery candidate](https://github.com/darkyeg/remiqora/blob/cb5001809130f9f2329414c5e016760385cc7844/external/patches/yue-model-resume.patch), this fork implementation adds saved immutable content identity, SHA256/Git blob SHA1 verification, complete Range validation, OS-owned writer locks, and digest-based interrupted promotion recovery. Failures/cancellation keep staging files. Stable staging names retain the upstream `.<target>.*` cleanup contract; cleanup waits for a package writer rather than deleting an active download. A model without a supported remote digest is rejected. A response length or HTTP 416 alone never establishes completion.

Desktop update versions include the patch SHA256 and verify actual applied hunks plus the installed script digest. The pinned upstream fixture and Apache license under `desktop/test/fixtures/` provide offline regression coverage. Windows file locking and real provider/platform installation still require platform evidence; CPU tests do not establish those flows.

`dev` is a moving branch upstream and gets rebased/force-pushed occasionally
(this pin has already needed bumping once after the previous commit
disappeared from its history) — if `setup_models.ps1` fails to check it out,
bump the ref in that script to a current `dev` commit.

### Optional YuE runtime build

`yue-workspace-release.patch` releases the AR prefill graph's temporary compute
workspace after synchronizing the backend. It retains prefix state buffers
needed by the acoustic solver and makes cleanup idempotent.

`yue-progress.patch` publishes optional per-request telemetry. The request must
provide `options.openfabric_run_id` as 32 lowercase hexadecimal characters, and
the process must have `OPENFABRIC_YUE2_PROGRESS_PATH` set. Invalid identities and
telemetry failures leave generation running. Exclusive temporary creation and
atomic replacement keep readers from seeing partial JSON; a generation token
rejects publishers left over from older requests, including foreign threads.
The payload contains the exact run identity, phase, counters and epoch
timestamps. Only acoustic solver steps and VAE decode tiles have known totals.
ABC/semantic token caps are limits, so those phases expose `total: null`.
The patch does not change sampling, numeric precision or audio calculations.

Both patches are checked against the source pin
`39f9013463053e206aa160f8453d734f78999b9d` and desktop release `v0.8.1`
`f2b4937306daa25f5c78520f3c626ed31495a37a`. The workspace patch derives from
[darkyeg's pinned fork](https://github.com/darkyeg/remiqora/tree/cb5001809130f9f2329414c5e016760385cc7844/external/patches);
the progress patch adapts that fork's instrumentation with request ownership,
bounded metadata and atomic publication. The native source remains Apache-2.0.

From this repository root:

```sh
# Read-only validation of an existing clean, exact-pinned source checkout.
python backend/scripts/setup_yue_native.py --check --source /path/to/clean/audio.cpp

# Copy into a new isolated directory, apply and verify the complete patches.
python backend/scripts/setup_yue_native.py --apply \
  --source /path/to/clean/audio.cpp --workspace /path/to/isolated/yue-native

# Build that verified copy. Choose cpu, metal or cuda for the installed toolchain.
python backend/scripts/setup_yue_native.py --build \
  --workspace /path/to/isolated/yue-native --backend cpu --jobs 2
```

Without `--source`, apply/build clones official upstream at the supported pin.
Use `--commit f2b4937306daa25f5c78520f3c626ed31495a37a` for the desktop release
source. The helper refuses dirty/unsupported source, unowned workspaces,
workspaces inside the input checkout, changed patch identities and unexpected
patched files. Repeating apply/build is allowed only for a verified owned copy.
It never changes the active engine, app configuration or user media, and does
not fetch models. CMake, Ninja and a C++17 toolchain are required; Metal/CUDA
also require their platform toolchains. Optional frontend submodules remain
disabled; GGML and SentencePiece are already vendored in the pinned source.

The build includes YuE2, SheetSage2, MuScriptor and native model management,
which upstream requires for OpenFabric Studio's `--ui-management` and model-load API.
By default CMake fetches and SHA256-verifies upstream's pinned BoringSSL
`0.20260813.0` source archive. For offline builds, pass
`--boringssl-archive /path/to/archive.tar.gz`; upstream still verifies its
digest. `--tls system` instead requires installed OpenSSL development libraries.
No dependency or license setting is changed on the host.

After a successful build, the helper prints `YUE2_SERVER_BIN` and writes
`audiocpp_server[.exe].openfabric.json` beside the binary. That manifest binds the
binary SHA256, exact source pin, complete patch identities, selected backend
and progress schema. App configuration validates it before enabling native
progress. Failed rebuilds remove the old manifest; moving a binary requires
preserving its runtime libraries as well as the adjacent manifest. Unmodified
release binaries continue to show phase/elapsed states without claiming
native counters.

Verification performed: both complete patch sets apply to full clean pinned
checkouts and offline source fixtures; a C++17 probe exercises stale-request
filtering and concurrent readers during repeated publication. A source-pin
CPU build compiled on macOS arm64 and started an isolated server with the
app's flags; `/health`, model listing, `--version` and `--list-devices` worked
without weights. CUDA, Metal, Windows compilation and generated audio quality
remain unverified. A CPU compile does not measure GPU quality.

The OpenFabric progress rename changes the patch and patched-source identities.
Use a new isolated workspace when rebuilding; existing owned workspaces and
manifests with the previous identity are intentionally rejected. The rename is
covered by offline source/hash checks for both pins and the C++ progress probe.
The complete engine build has not been repeated after this rename.

## Regenerating a patch

If you make further changes inside a cloned checkout under `external/`,
regenerate the corresponding patch with:

```sh
git diff <base-commit> HEAD -- . ':(exclude)web-ui' > external/patches/<name>.patch
```
