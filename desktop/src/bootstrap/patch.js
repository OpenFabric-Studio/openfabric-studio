'use strict';
const fsp = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { parsePatch, applyPatch, reversePatch } = require('diff');

const stripPrefix = (name) => name.replace(/^[ab]\//, '');

async function patchTarget(rootDir, name) {
  if (typeof name !== 'string') throw new Error('unsafe patch path');
  const relative = stripPrefix(name);
  if (!relative || /^([a-z]:|[\/\\])/i.test(relative) || relative.split(/[\/\\]/).includes('..')) throw new Error(`unsafe patch path: ${relative}`);
  const root = await fsp.realpath(rootDir);
  const target = path.resolve(root, relative);
  let probe = target;
  for (;;) {
    try {
      const real = await fsp.realpath(probe);
      const fromRoot = path.relative(root, real);
      if (fromRoot === '..' || fromRoot.startsWith(`..${path.sep}`) || path.isAbsolute(fromRoot)) throw new Error(`patch path is outside root: ${relative}`);
      break;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      probe = path.dirname(probe);
    }
  }
  return { relative, target };
}

/**
 * Applies a `git diff` style patch to the files under `rootDir` without needing Git.
 * All results are computed in memory first, so a patch that does not apply leaves the tree untouched.
 * Returns the number of files written.
 */
async function applyGitPatch(patchText, rootDir) {
  // The patch is stored with LF; a Windows checkout may have turned it into CRLF.
  const patches = parsePatch(patchText.replace(/\r\n/g, '\n'));
  const results = [];

  for (const p of patches) {
    const { relative, target } = await patchTarget(rootDir, p.newFileName);
    const isNew = p.oldFileName === '/dev/null';
    let source = '';
    if (!isNew) {
      try {
        source = await fsp.readFile(target, 'utf8');
      } catch {
        throw new Error(`patch target is missing: ${relative}`);
      }
    }
    const output = applyPatch(source, p);
    if (output === false) throw new Error(`patch does not apply cleanly to ${relative}`);
    results.push({ target, output });
  }

  const temporary = [];
  try {
    for (const { target, output } of results) {
      await fsp.mkdir(path.dirname(target), { recursive: true });
      const staged = `${target}.${crypto.randomUUID()}.tmp`;
      temporary.push({ staged, target });
      const mode = await fsp.stat(target).then((s) => s.mode, () => 0o644);
      await fsp.writeFile(staged, output, { flag: 'wx', mode });
    }
    for (const { staged, target } of temporary) await fsp.rename(staged, target);
  } finally {
    await Promise.all(temporary.map(({ staged }) => fsp.rm(staged, { force: true })));
  }
  return results.length;
}

/** Check actual hunks in existing files, including new files, without relying on marker comments. */
async function isGitPatchApplied(patchText, rootDir) {
  const patches = parsePatch(patchText.replace(/\r\n/g, '\n'));
  if (!patches.length) return false;
  for (const p of patches) {
    if (!p.newFileName) return false;
    const { target } = await patchTarget(rootDir, p.newFileName);
    const source = await fsp.readFile(target, 'utf8').catch(() => null);
    if (source === null || applyPatch(source, reversePatch(p)) === false) return false;
  }
  return true;
}

module.exports = { applyGitPatch, isGitPatchApplied };
