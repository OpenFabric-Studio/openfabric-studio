'use strict';
const fsp = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { IS_WINDOWS } = require('../paths');
const { runCommand } = require('../proc');

/**
 * The system bsdtar reads both .zip and .tar.gz: tar.exe ships with Windows 10 1803+ and macOS.
 * Not "tar" from PATH on Windows: Git Bash puts GNU tar first, and that one cannot read zip.
 */
function tarBinary() {
  return IS_WINDOWS ? path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'tar.exe') : 'tar';
}

/** Extracts `archive` into `destDir` (created if missing). */
async function extract(archive, destDir, { stripComponents = 0, signal } = {}) {
  signal?.throwIfAborted();
  // Validate before writing. Pinned archives do not need links or special files.
  let unsafe = false;
  let entries = 0;
  await runCommand(tarBinary(), ['-tf', archive], { signal, timeoutMs: 120000, onLine: (line) => {
    const name = line.replaceAll('\\', '/');
    entries++;
    if (entries > 100000 || name.length > 4096 || name.startsWith('/') || /^[a-z]:/i.test(name) || name.split('/').includes('..')) unsafe = true;
  } });
  await runCommand(tarBinary(), ['-tvf', archive], { signal, timeoutMs: 120000, onLine: (line) => {
    if (line && line[0] !== '-' && line[0] !== 'd') unsafe = true;
  } });
  if (unsafe) throw new Error(`unsafe archive entry or symlink in ${path.basename(archive)}`);
  signal?.throwIfAborted();
  await fsp.mkdir(destDir, { recursive: true });
  const args = ['-xf', archive, '-C', destDir];
  if (stripComponents) args.push(`--strip-components=${stripComponents}`);
  await runCommand(tarBinary(), args, { signal, timeoutMs: 120000 });
  const verify = async dir => {
    for (const entry of await fsp.readdir(dir, { withFileTypes: true })) {
      if (entry.isSymbolicLink() || (!entry.isFile() && !entry.isDirectory())) throw new Error('unsafe extracted symlink or special file');
      if (entry.isDirectory()) await verify(path.join(dir, entry.name));
    }
  };
  await verify(destDir);
}

/**
 * Extracts into a sibling temp directory and renames it into place, so an interrupted
 * extraction never leaves a half-populated `destDir` that looks installed.
 */
async function extractAtomic(archive, destDir, options) {
  const tmp = `${destDir}.${crypto.randomUUID()}.tmp`;
  try {
    await extract(archive, tmp, options);
    await promoteDirectory(tmp, destDir, options);
  } finally { await fsp.rm(tmp, { recursive: true, force: true }); }
}

/** Retains the old directory until staged extraction/verification can be promoted. */
async function promoteDirectory(tmp, destDir, options) {
  const previous = `${destDir}.previous`;
  const exists = file => fsp.lstat(file).then(() => true, () => false);
  if (await exists(previous)) {
    if (!(await exists(destDir))) await fsp.rename(previous, destDir);
    else await fsp.rename(previous, `${destDir}.preserved-${crypto.randomUUID()}`);
  }
  options?.signal?.throwIfAborted();
  if (await exists(destDir)) await fsp.rename(destDir, previous);
  try {
    options?.signal?.throwIfAborted();
    await fsp.rename(tmp, destDir);
  } catch (error) {
    if (!(await exists(destDir)) && await exists(previous)) await fsp.rename(previous, destDir);
    throw error;
  }
  await fsp.rm(previous, { recursive: true, force: true });
}

module.exports = { extract, extractAtomic, promoteDirectory, tarBinary };
