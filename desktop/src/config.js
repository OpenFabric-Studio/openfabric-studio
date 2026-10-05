'use strict';
const fsp = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const writes = new Map();

function serialize(dir, task) {
  const key = path.resolve(dir);
  const next = (writes.get(key) || Promise.resolve()).catch(() => {}).then(task);
  writes.set(key, next);
  return next.finally(() => { if (writes.get(key) === next) writes.delete(key); });
}

/** Tiny per-user settings file in Electron's userData folder (the data root itself can live on another drive). */
async function loadConfig(dir) {
  try {
    const parsed = JSON.parse(await fsp.readFile(path.join(dir, 'config.json'), 'utf8'));
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    if (parsed.dataRoot !== undefined && (typeof parsed.dataRoot !== 'string' || !path.isAbsolute(parsed.dataRoot) || parsed.dataRoot.length > 4096)) return {};
    if (parsed.port !== undefined && (!Number.isInteger(parsed.port) || parsed.port < 1 || parsed.port > 65535)) return {};
    return parsed;
  } catch {
    return {};
  }
}

async function writeConfig(dir, config) {
  await fsp.mkdir(dir, { recursive: true });
  const file = path.join(dir, 'config.json');
  const temporary = `${file}.${crypto.randomUUID()}.tmp`;
  try {
    await fsp.writeFile(temporary, JSON.stringify(config, null, 2), { flag: 'wx', mode: 0o600 });
    await fsp.rename(temporary, file);
  } finally { await fsp.rm(temporary, { force: true }); }
}

/** Merges `patch` into the saved settings (data root, remembered port, ...). */
function updateConfig(dir, patch) {
  return serialize(dir, async () => {
    const next = { ...(await loadConfig(dir)), ...patch };
    await writeConfig(dir, next);
    return next;
  });
}

/** The data root must be creatable and writable before anything is downloaded into it. */
async function ensureWritableDir(dir) {
  if (typeof dir !== 'string' || !path.isAbsolute(dir) || dir.length > 4096) throw new Error('Choose an absolute data folder path.');
  await fsp.mkdir(dir, { recursive: true });
  const probe = path.join(dir, `.write-test-${crypto.randomUUID()}`);
  try { await fsp.writeFile(probe, 'ok', { flag: 'wx' }); }
  finally { await fsp.rm(probe, { force: true }); }
}

module.exports = { loadConfig, updateConfig, ensureWritableDir };
