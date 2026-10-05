'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { sourceLayout, prepareSource, sourceEnvironment, launchSource } = require('../scripts/launch-source');

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

test('source launch disables inherited backend reload without mutating the caller environment', () => {
  const original = { WEB_CONCURRENCY: '8', UVICORN_RELOAD: 'true', CUSTOM: 'preserved' };
  for (const development of [true, false]) {
    const prepared = sourceEnvironment(original, development);
    assert.equal(prepared.UVICORN_RELOAD, undefined);
    assert.equal(prepared.CUSTOM, 'preserved');
  }
  assert.equal(original.UVICORN_RELOAD, 'true');
});

test('source backend pins one worker even with inherited web concurrency', { skip: process.platform === 'win32' }, async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'openfabric-source-worker-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const L = sourceLayout(root);
  fs.mkdirSync(path.dirname(L.python), { recursive: true });
  fs.mkdirSync(path.join(root, 'frontend', 'node_modules'), { recursive: true });
  fs.writeFileSync(path.join(root, 'frontend', 'package-lock.json'), '{}');
  fs.writeFileSync(path.join(root, 'backend', 'requirements.txt'), 'fixture\n');
  const log = path.join(root, 'backend-command');
  fs.writeFileSync(L.python, '#!/bin/sh\nif [ "$1" = "-m" ] && [ "$2" = "uvicorn" ]; then printf "%s\\n" "$@" > "$COMMAND_LOG"; fi\nexit 0\n', { mode: 0o755 });
  const uv = path.join(root, 'uv');
  const npm = path.join(root, 'npm');
  fs.writeFileSync(uv, '#!/bin/sh\nexit 0\n', { mode: 0o755 });
  fs.writeFileSync(npm, '#!/bin/sh\nexit 0\n', { mode: 0o755 });
  await launchSource({ root, env: { ...process.env, UV_BIN: uv, NPM_BIN: npm, COMMAND_LOG: log, WEB_CONCURRENCY: '8' } });
  const args = fs.readFileSync(log, 'utf8').trim().split('\n');
  assert.equal(args[args.indexOf('--workers') + 1], '1');
  assert.equal(args[args.indexOf('--host') + 1], '127.0.0.1');
});

test('backend shell launcher overrides inherited workers and reload', { skip: process.platform === 'win32' }, (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'openfabric-shell-worker-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const executable = path.join(root, '.venv', 'bin', 'python3');
  fs.mkdirSync(path.dirname(executable), { recursive: true });
  fs.copyFileSync(path.join(__dirname, '../../backend/run.sh'), path.join(root, 'run.sh'));
  fs.writeFileSync(executable, '#!/bin/sh\nprintf "%s\\n" "$@" > "$COMMAND_LOG"\nprintf "%s" "${UVICORN_RELOAD-unset}" > "$RELOAD_LOG"\n', { mode: 0o755 });
  const commandLog = path.join(root, 'args');
  const reloadLog = path.join(root, 'reload');
  const result = spawnSync('bash', [path.join(root, 'run.sh')], {
    env: { ...process.env, WEB_CONCURRENCY: '8', UVICORN_RELOAD: 'true', COMMAND_LOG: commandLog, RELOAD_LOG: reloadLog },
    encoding: 'utf8',
  });
  assert.equal(result.status, 0, result.stderr);
  const args = fs.readFileSync(commandLog, 'utf8').trim().split('\n');
  assert.equal(args[args.indexOf('--workers') + 1], '1');
  assert.equal(fs.readFileSync(reloadLog, 'utf8'), 'unset');
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
