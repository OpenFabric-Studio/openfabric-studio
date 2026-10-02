'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { applyGitPatch } = require('../src/bootstrap/patch');

const tree = (files) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'remiqora-patch-'));
  for (const [name, text] of Object.entries(files)) {
    fs.mkdirSync(path.dirname(path.join(dir, name)), { recursive: true });
    fs.writeFileSync(path.join(dir, name), text);
  }
  return dir;
};
const read = (dir, name) => fs.readFileSync(path.join(dir, name), 'utf8');

const PATCH = `diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -1,3 +1,4 @@
 line one
-line two
+line two changed
+line two and a half
 line three
diff --git a/pkg/new.py b/pkg/new.py
new file mode 100644
--- /dev/null
+++ b/pkg/new.py
@@ -0,0 +1,2 @@
+created
+by patch
`;

test('modifies a file and creates a new one', async () => {
  const dir = tree({ 'pkg/mod.py': 'line one\nline two\nline three\n' });
  assert.equal(await applyGitPatch(PATCH, dir), 2);
  assert.equal(read(dir, 'pkg/mod.py'), 'line one\nline two changed\nline two and a half\nline three\n');
  assert.equal(read(dir, 'pkg/new.py'), 'created\nby patch\n');
});

test('accepts a patch that a Windows checkout turned into CRLF', async () => {
  const dir = tree({ 'pkg/mod.py': 'line one\nline two\nline three\n' });
  await applyGitPatch(PATCH.replace(/\n/g, '\r\n'), dir);
  assert.match(read(dir, 'pkg/mod.py'), /line two changed/);
});

test('keeps CRLF line endings of a target file', async () => {
  const dir = tree({ 'pkg/mod.py': 'line one\r\nline two\r\nline three\r\n' });
  await applyGitPatch(PATCH, dir);
  assert.equal(read(dir, 'pkg/mod.py'), 'line one\r\nline two changed\r\nline two and a half\r\nline three\r\n');
});

test('a patch that does not apply leaves the tree untouched', async () => {
  const dir = tree({ 'pkg/mod.py': 'completely different\ncontent here\nnothing matches\n' });
  await assert.rejects(applyGitPatch(PATCH, dir), /does not apply cleanly to pkg\/mod\.py/);
  assert.equal(read(dir, 'pkg/mod.py'), 'completely different\ncontent here\nnothing matches\n');
  assert.equal(fs.existsSync(path.join(dir, 'pkg/new.py')), false);
});

test('reports a missing target file', async () => {
  const dir = tree({});
  await assert.rejects(applyGitPatch(PATCH, dir), /patch target is missing: pkg\/mod\.py/);
});

test('failed atomic patch promotion retains the original and removes temporary files', async () => {
  const fsp = require('node:fs/promises');
  const dir = tree({ 'pkg/mod.py': 'line one\nline two\nline three\n' });
  const rename = fsp.rename;
  fsp.rename = async () => { throw new Error('simulated interrupted promotion'); };
  try {
    await assert.rejects(applyGitPatch(PATCH, dir), /simulated interrupted promotion/);
    assert.equal(read(dir, 'pkg/mod.py'), 'line one\nline two\nline three\n');
    assert.deepEqual(fs.readdirSync(path.join(dir, 'pkg')), ['mod.py']);
  } finally { fsp.rename = rename; }
});

test('patch paths cannot escape the root through traversal or symlinks', async () => {
  const dir = tree({ 'pkg/mod.py': 'line one\nline two\nline three\n' });
  const outside = tree({ 'mod.py': 'line one\nline two\nline three\n' });
  for (const name of ['../escape.py', '/absolute.py']) {
    const unsafe = PATCH.replaceAll('pkg/mod.py', name);
    await assert.rejects(applyGitPatch(unsafe, dir), /outside|unsafe/);
  }
  fs.symlinkSync(outside, path.join(dir, 'linked'), process.platform === 'win32' ? 'junction' : 'dir');
  await assert.rejects(applyGitPatch(PATCH.replaceAll('pkg/mod.py', 'linked/mod.py'), dir), /outside|unsafe/);
  assert.equal(read(outside, 'mod.py'), 'line one\nline two\nline three\n');
});
