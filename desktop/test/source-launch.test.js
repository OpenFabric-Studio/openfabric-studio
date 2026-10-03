'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { sourceLayout, prepareSource, sourceEnvironment } = require('../scripts/launch-source');

test('source layout resolves the real Python executable on all platforms', () => {
  assert.equal(sourceLayout('/repo', 'win32').python, path.join('/repo', 'backend', '.venv', 'Scripts', 'python.exe'));
  assert.equal(sourceLayout('/repo', 'linux').python, path.join('/repo', 'backend', '.venv', 'bin', 'python'));
});

test('development origins are allowed only by the development launcher', () => {
  const original = { CUSTOM: 'preserved' };
  assert.equal(sourceEnvironment(original, true).OPENFABRIC_SETUP_ALLOWED_ORIGINS, 'http://localhost:5173,http://127.0.0.1:5173');
  assert.equal(sourceEnvironment(original, false).OPENFABRIC_SETUP_ALLOWED_ORIGINS, undefined);
  assert.equal(original.OPENFABRIC_SETUP_ALLOWED_ORIGINS, undefined);
});

test('an existing source venv is refreshed after requirements changes and failed refresh is not recorded', { skip: process.platform === 'win32' }, async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'openfabric-source-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const L = sourceLayout(root);
  fs.mkdirSync(path.dirname(L.python), { recursive: true });
  fs.mkdirSync(path.join(root, 'frontend', 'node_modules'), { recursive: true });
  fs.writeFileSync(path.join(root, 'frontend', 'package-lock.json'), '{}');
  fs.writeFileSync(path.join(root, 'backend', 'requirements.txt'), 'first requirement\n');
  fs.writeFileSync(L.python, '#!/bin/sh\nexit 0\n', { mode: 0o755 });
  const log = path.join(root, 'commands');
  const uv = path.join(root, 'uv');
  const npm = path.join(root, 'npm');
  fs.writeFileSync(uv, '#!/bin/sh\nprintf "%s\\n" "$*" >> "$COMMAND_LOG"\nif [ -f "$FAIL_INSTALL" ]; then exit 7; fi\n', { mode: 0o755 });
  fs.writeFileSync(npm, '#!/bin/sh\nprintf "npm %s\\n" "$*" >> "$COMMAND_LOG"\n', { mode: 0o755 });
  const env = { ...process.env, UV_BIN: uv, NPM_BIN: npm, COMMAND_LOG: log, FAIL_INSTALL: path.join(root, 'fail') };
  await prepareSource({ root, env });
  assert.match(fs.readFileSync(log, 'utf8'), /pip install .*requirements\.txt/);
  const first = fs.readFileSync(L.requirementsMarker, 'utf8');
  fs.writeFileSync(log, '');
  await prepareSource({ root, env });
  assert.doesNotMatch(fs.readFileSync(log, 'utf8'), /pip install/);
  fs.writeFileSync(path.join(root, 'backend', 'requirements.txt'), 'second requirement\n');
  fs.writeFileSync(env.FAIL_INSTALL, 'fail');
  await assert.rejects(prepareSource({ root, env }), /exited with code 7/);
  assert.equal(fs.readFileSync(L.requirementsMarker, 'utf8'), first);
  fs.unlinkSync(env.FAIL_INSTALL);
  await prepareSource({ root, env });
  assert.notEqual(fs.readFileSync(L.requirementsMarker, 'utf8'), first);
  fs.writeFileSync(path.join(root, 'backend', 'requirements.lock'), 'reviewed hashed lock\n');
  fs.writeFileSync(log, '');
  await prepareSource({ root, env });
  const locked = fs.readFileSync(log, 'utf8');
  assert.match(locked, /pip install .*--require-hashes --only-binary :all:.*requirements\.lock/);
});
