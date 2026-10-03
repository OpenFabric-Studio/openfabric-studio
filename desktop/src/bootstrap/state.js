'use strict';
const fsp = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');

const empty = () => ({ schema: 1, components: {} });

async function loadState(file) {
  try {
    const parsed = JSON.parse(await fsp.readFile(file, 'utf8'));
    if (!parsed || parsed.schema !== 1 || !parsed.components || typeof parsed.components !== 'object' || Array.isArray(parsed.components)) return empty();
    if (!Object.entries(parsed.components).every(([id, rec]) => /^[a-z][a-z0-9-]{0,79}$/.test(id) && rec && typeof rec === 'object' && !Array.isArray(rec) && typeof rec.version === 'string' && typeof rec.at === 'string' && Number.isFinite(Date.parse(rec.at)))) return empty();
    return parsed;
  } catch {
    return empty();
  }
}

/** Written through a temp file so a crash cannot leave a half-written state.json. */
async function saveState(file, state) {
  await fsp.mkdir(path.dirname(file), { recursive: true });
  const tmp = `${file}.${crypto.randomUUID()}.tmp`;
  try {
    await fsp.writeFile(tmp, JSON.stringify(state, null, 2), { flag: 'wx', mode: 0o600 });
    await fsp.rename(tmp, file);
  } finally { await fsp.rm(tmp, { force: true }); }
}

module.exports = { loadState, saveState };
