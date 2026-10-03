'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { extract, extractAtomic, tarBinary } = require('../src/bootstrap/extract');

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'openfabric-extract-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const source = path.join(root, 'source');
  await fs.mkdir(source);
  await fs.writeFile(path.join(source, 'file'), 'new');
  const archive = path.join(root, 'archive.tar.gz');
  execFileSync(tarBinary(), ['-czf', archive, '-C', source, 'file']);
  const dest = path.join(root, 'installed');
  await fs.mkdir(dest);
  await fs.writeFile(path.join(dest, 'file'), 'old');
  return { root, archive, dest };
}

test('failed archive promotion restores the existing installation', async (t) => {
  const { archive, dest } = await fixture(t);
  const rename = fs.rename;
  fs.rename = async (from, to) => {
    if (to === dest && from !== `${dest}.previous`) throw new Error('promotion interrupted');
    return rename(from, to);
  };
  try { await assert.rejects(extractAtomic(archive, dest), /promotion interrupted/); }
  finally { fs.rename = rename; }
  assert.equal(await fs.readFile(path.join(dest, 'file'), 'utf8'), 'old');
});

test('an already cancelled extraction does not create an output directory', async (t) => {
  const { root, archive } = await fixture(t);
  const abort = new AbortController();
  abort.abort(new Error('cancelled extraction'));
  const dest = path.join(root, 'cancelled');
  await assert.rejects(extract(archive, dest, { signal: abort.signal }), /cancelled extraction/);
  await assert.rejects(fs.stat(dest), { code: 'ENOENT' });
});

test('an archive symlink cannot escape the extracted serving root', { skip: process.platform === 'win32' }, async (t) => {
  const { root } = await fixture(t);
  const source = path.join(root, 'unsafe-source');
  await fs.mkdir(source);
  await fs.symlink('../../outside', path.join(source, 'escape'));
  const archive = path.join(root, 'unsafe.tar.gz');
  execFileSync(tarBinary(), ['-czf', archive, '-C', source, 'escape']);
  await assert.rejects(extract(archive, path.join(root, 'unsafe-output')), /symlink|unsafe/i);
});
