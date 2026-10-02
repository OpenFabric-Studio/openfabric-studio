'use strict';
const path = require('node:path');
const os = require('node:os');

/** "win32-x64", "darwin-arm64", ... : the key used in manifest.json assets. */
const PLATFORM = `${process.platform}-${process.arch}`;

const IS_WINDOWS = process.platform === 'win32';

function repoRoot() {
  return path.resolve(__dirname, '..', '..');
}

/**
 * Where the backend sources, the built frontend and the ACE-Step patch live:
 * next to the app when installed, inside the repository when running from source.
 * The backend finds the frontend as ../frontend/dist relative to itself, so both
 * layouts keep that shape.
 */
function resourcePaths(isPackaged) {
  if (isPackaged) {
    const base = process.resourcesPath;
    return {
      backend: path.join(base, 'backend'),
      frontendDist: path.join(base, 'frontend', 'dist'),
      acePatch: path.join(base, 'patches', 'ace-step.patch'),
      modelManagerPatch: path.join(base, 'patches', 'yue-model-resume.patch'),
    };
  }
  const root = repoRoot();
  return {
    backend: path.join(root, 'backend'),
    frontendDist: path.join(root, 'frontend', 'dist'),
    acePatch: path.join(root, 'external', 'patches', 'ace-step.patch'),
    modelManagerPatch: path.join(root, 'external', 'patches', 'yue-model-resume.patch'),
  };
}

/** Default data root. Models and generated audio are large, so it is per-user and never inside the install dir. */
function defaultDataRoot() {
  const home = process.env.OPENFABRIC_HOME;
  if (home) return path.resolve(home);
  const folder = 'OpenFabricStudio';
  switch (process.platform) {
    case 'win32':
      return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), folder);
    case 'darwin':
      return path.join(os.homedir(), 'Library', 'Application Support', folder);
    default:
      return path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share'), folder);
  }
}

/** Every path the installer and the server use, all under one root the user can pick. */
function layout(root, platform = PLATFORM, manifest = null) {
  const engineAsset = manifest && manifest.engine.assets[platform];
  const preset = engineAsset ? engineAsset.preset : IS_WINDOWS ? 'windows-cuda-release' : 'macos-metal-release';
  const yue2 = path.join(root, 'engines', 'YuE2');
  const uvDir = path.join(root, 'tools', 'uv');
  const backendVenv = path.join(root, 'backend-venv');
  return {
    root,
    uvDir,
    uvBin: path.join(uvDir, IS_WINDOWS ? 'uv.exe' : 'uv'),
    ffmpegDir: path.join(root, 'tools', 'ffmpeg'),
    pythonDir: path.join(root, 'tools', 'python'),
    uvCache: path.join(root, 'cache', 'uv'),
    hfHome: path.join(root, 'cache', 'huggingface'),
    torchHome: path.join(root, 'cache', 'torch'),
    downloads: path.join(root, 'cache', 'downloads'),
    aceStep: path.join(root, 'engines', 'ACE-Step-1.5'),
    yue2,
    yue2Bin: path.join(yue2, 'build', preset, 'bin'),
    demucs: path.join(root, 'engines', 'Demucs'),
    backendVenv,
    backendPython: path.join(backendVenv, IS_WINDOWS ? 'Scripts' : 'bin', IS_WINDOWS ? 'python.exe' : 'python'),
    data: path.join(root, 'data'),
    logs: path.join(root, 'logs'),
    state: path.join(root, 'state.json'),
  };
}

module.exports = { PLATFORM, IS_WINDOWS, repoRoot, resourcePaths, defaultDataRoot, layout };
