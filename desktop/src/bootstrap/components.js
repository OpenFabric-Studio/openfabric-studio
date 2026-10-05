'use strict';
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { downloadFile } = require('./download');
const { extract } = require('./extract');
const { runCommand, cleanEnv } = require('../proc');
const { IS_WINDOWS } = require('../paths');

const exists = (p) => fsp.access(p).then(() => true, () => false);
const sha1 = (text) => crypto.createHash('sha1').update(text).digest('hex').slice(0, 12);

/** Environment for uv: everything (Python, wheel cache) stays under the data root the user chose. */
function uvEnv(L) {
  // only-managed: never bind the venvs to a system Python that the user may later remove or upgrade.
  return cleanEnv({ UV_CACHE_DIR: L.uvCache, UV_PYTHON_INSTALL_DIR: L.pythonDir, UV_PYTHON_PREFERENCE: 'only-managed', HF_HOME: L.hfHome, TORCH_HOME: L.torchHome, UV_NO_PROGRESS: '1', NO_COLOR: '1', PYTHONUTF8: '1' });
}

/** Downloads one manifest file into the downloads cache and reports byte progress offset by `base`. */
function fetchTo(ctx, file, base, total, report) {
  return downloadFile({
    url: file.url,
    dest: path.join(ctx.L.downloads, path.basename(new URL(file.url).pathname)),
    sha256: file.sha256,
    signal: ctx.signal,
    onProgress: (done) => report({ done: base + done, total }),
  });
}

/** First file called `name` anywhere under `dir` (archives differ: uv's zip is flat, its tarballs have a top folder). */
async function findFile(dir, name) {
  for (const entry of await fsp.readdir(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isFile() && entry.name === name) return p;
    if (entry.isDirectory()) {
      const inner = await findFile(p, name);
      if (inner) return inner;
    }
  }
  return null;
}

/**
 * The ordered list of things the first run installs. Each component:
 *   id, weight (bytes, for the overall bar), version (a change re-runs it),
 *   verify(ctx) -> bool (sanity check on disk), install(ctx, report).
 * report({ done, total, note }): byte progress is optional, `note` is a human line (uv output, ...).
 */
function buildComponents({ L, manifest, platform, resources }) {
  const uvAsset = manifest.uv.assets[platform];
  if (!uvAsset) return [];
  const logFile = path.join(L.logs, 'setup.log');
  const lockedRequirements = path.join(resources.backend, 'requirements.lock');
  const requirements = fs.existsSync(lockedRequirements) ? lockedRequirements : path.join(resources.backend, 'requirements.txt');
  const lockOptions = requirements === lockedRequirements ? ['--require-hashes', '--only-binary', ':all:'] : [];

  const backendEnv = {
    id: 'backend-env',
    weight: 60e6,
    version: `py3.12-${sha1(`${path.basename(requirements)}:${fs.readFileSync(requirements, 'utf8')}`)}`,
    async verify(ctx) {
      if (!(await exists(L.backendPython))) return false;
      try {
        await runCommand(L.backendPython, ['-c', 'import sys; assert sys.version_info[:2] == (3, 12); import fastapi, uvicorn, httpx, pydantic, multipart, dotenv, numpy, PIL'], { signal: ctx?.signal, timeoutMs: 15000 });
        await runCommand(L.backendPython, ['-m', 'pip', 'check'], { signal: ctx?.signal, timeoutMs: 15000 });
        return true;
      } catch (error) { if (ctx?.signal?.aborted) throw error; return false; }
    },
    async install(ctx, report) {
      const env = uvEnv(L);
      const note = (line) => report({ note: line });
      await runCommand(L.uvBin, ['venv', '--python', '3.12', '--seed', '--allow-existing', L.backendVenv], { env, onLine: note, signal: ctx.signal, logFile });
      await runCommand(L.uvBin, ['pip', 'install', '--python', L.backendPython, ...lockOptions, '-r', requirements], { env, onLine: note, signal: ctx.signal, logFile });
    },
  };

  const uv = {
    id: 'uv',
    weight: uvAsset.bytes,
    version: manifest.uv.version,
    async verify(ctx) {
      if (!(await exists(L.uvBin))) return false;
      let version = '';
      try {
        await runCommand(L.uvBin, ['--version'], { onLine: (line) => { version += line; }, signal: ctx?.signal, timeoutMs: 5000 });
        return version.trim().split(/\s+/).slice(0, 2).join(' ') === `uv ${manifest.uv.version}`;
      } catch (error) { if (ctx?.signal?.aborted) throw error; return false; }
    },
    async install(ctx, report) {
      const archive = await fetchTo(ctx, uvAsset, 0, uvAsset.bytes, report);
      const tmp = path.join(L.downloads, 'uv-extract');
      await fsp.rm(tmp, { recursive: true, force: true });
      await extract(archive, tmp, { signal: ctx.signal });
      await fsp.mkdir(L.uvDir, { recursive: true });
      const found = await findFile(tmp, path.basename(uvAsset.bin));
      if (!found) throw new Error(`${path.basename(uvAsset.bin)} was not found inside the uv archive`);
      const candidate = `${L.uvBin}.${crypto.randomUUID()}.tmp`;
      try {
        await fsp.copyFile(found, candidate);
        if (!IS_WINDOWS) await fsp.chmod(candidate, 0o755);
        ctx.signal?.throwIfAborted();
        await fsp.rename(candidate, L.uvBin);
      } finally { await fsp.rm(candidate, { force: true }); }
      await fsp.rm(tmp, { recursive: true, force: true });
      await fsp.rm(archive, { force: true });
    },
  };

  // Opening the studio does not require models, a GPU, or optional media tools.
  return [uv, backendEnv].map(c => ({ ...c, network: true }));
}

/** Path of ffmpeg: a pinned build under tools/ffmpeg where there is one, otherwise whatever the system has. */
function ffmpegExecutable(L, manifest, platform, tool = 'ffmpeg') {
  if (tool !== 'ffmpeg' && tool !== 'ffprobe') throw new Error('Unsupported media tool');
  const asset = manifest.ffmpeg.assets[platform];
  const exe = platform.startsWith('win32') ? `${tool}.exe` : tool;
  if (asset) return path.join(L.ffmpegDir, 'bin', exe);
  for (const dir of [process.env.FFMPEG_BIN_DIR, ...(process.env.PATH || '').split(path.delimiter), '/opt/homebrew/bin', '/usr/local/bin', '/usr/bin'].filter(Boolean)) {
    if (fs.existsSync(path.join(dir, exe))) return path.join(dir, exe);
  }
  return path.join(L.ffmpegDir, 'bin', exe);
}

module.exports = { buildComponents, ffmpegExecutable };
