'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const manifest = require('../manifest.json');
const { layout } = require('../src/paths');
const { buildComponents } = require('../src/bootstrap/components');
const { describePlan, runSetup } = require('../src/bootstrap/run');
const { runChecks } = require('../src/bootstrap/checks');
const { loadState } = require('../src/bootstrap/state');
const { backendEnv } = require('../src/server');

function fixture(t, platform = 'darwin-arm64') {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'openfabric-platform-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return { L: layout(root, platform, manifest), manifest, platform, resources: { backend: path.resolve(__dirname, '../../backend'), acePatch: path.resolve(__dirname, '../../external/patches/ace-step.patch') } };
}

test('first run installs only uv and the backend before opening Settings', (t) => {
  const ctx = fixture(t);
  assert.deepEqual(buildComponents(ctx).map(c => c.id), ['uv', 'backend-env']);
});

test('unsupported desktop platforms can describe setup without dereferencing absent engine assets', async (t) => {
  for (const platform of ['linux-x64', 'darwin-x64', 'linux-arm64']) {
    const ctx = fixture(t, platform);
    const plan = await describePlan(ctx);
    const checks = await runChecks({ ...ctx, dataRoot: ctx.L.root, plan, fetchImpl: async () => ({ status: 200 }) });
    assert.equal(checks.blocking.code, 'unsupported-platform');
  }
});

test('minimal Windows bootstrap does not require an NVIDIA GPU', async (t) => {
  const ctx = fixture(t, 'win32-x64');
  const result = await runChecks({ ...ctx, dataRoot: ctx.L.root, plan: [{ id: 'backend-env', weight: 1, done: false, network: true }], fetchImpl: async () => ({ status: 200 }) });
  assert.equal(result.blocking, null);
  assert.ok(!result.items.some(row => row.id === 'gpu' || row.id === 'driver'));
});

test('a successful installer whose verification fails is not recorded as complete', async (t) => {
  const ctx = fixture(t);
  ctx.components = [{ id: 'broken', weight: 1, version: '1', network: false, install: async () => {}, verify: async () => false }];
  await assert.rejects(runSetup(ctx), error => error.componentId === 'broken' && error.code === 'verification-failed');
  assert.deepEqual((await loadState(ctx.L.state)).components, {});
});

test('aborting after installation prevents completion records', async (t) => {
  const ctx = fixture(t);
  const abort = new AbortController();
  ctx.signal = abort.signal;
  ctx.components = [{ id: 'cancelled', weight: 1, version: '1', network: false, install: async () => abort.abort(new Error('cancelled')), verify: async () => true }];
  await assert.rejects(runSetup(ctx), /cancelled/);
  assert.deepEqual((await loadState(ctx.L.state)).components, {});
});

test('layout uses the target platform and includes writable optional roots', (t) => {
  const ctx = fixture(t, 'win32-x64');
  assert.equal(ctx.L.backendPython, path.join(ctx.L.root, 'backend-venv', 'Scripts', 'python.exe'));
  assert.equal(ctx.L.uvBin, path.join(ctx.L.root, 'tools', 'uv', 'uv.exe'));
  assert.equal(ctx.L.seedVc, path.join(ctx.L.root, 'engines', 'seed-vc'));
  assert.equal(ctx.L.gptSovits, path.join(ctx.L.root, 'engines', 'gpt-sovits'));
  assert.equal(ctx.L.ltx, path.join(ctx.L.root, 'engines', 'ltx-2-mlx'));
  assert.equal(ctx.L.roformer, path.join(ctx.L.root, 'engines', 'Music-Source-Separation-Training'));
});

test('desktop supplies a managed root without overriding inherited engine choices or dotenv defaults', (t) => {
  const ctx = fixture(t);
  const previous = process.env.SEED_VC_DIR;
  process.env.SEED_VC_DIR = '/private/existing-engine';
  t.after(() => { if (previous === undefined) delete process.env.SEED_VC_DIR; else process.env.SEED_VC_DIR = previous; });
  const env = backendEnv(ctx);
  assert.equal(env.OPENFABRIC_MODULE_ROOT, ctx.L.root);
  assert.equal(env.SEED_VC_DIR, '/private/existing-engine');
  assert.equal(env.ACE_STEP_DIR, process.env.ACE_STEP_DIR);
  assert.equal(env.OPENFABRIC_GPT_SOVITS_DIR, process.env.OPENFABRIC_GPT_SOVITS_DIR);
});

test('an existing Python file with broken dependencies is not a verified backend environment', async (t) => {
  const ctx = fixture(t);
  fs.mkdirSync(path.dirname(ctx.L.backendPython), { recursive: true });
  fs.writeFileSync(ctx.L.backendPython, 'not an executable Python environment');
  const component = buildComponents(ctx).find(c => c.id === 'backend-env');
  assert.equal(await component.verify(ctx), false);
});

test('malformed setup state is rejected instead of trusted as component records', async (t) => {
  const ctx = fixture(t);
  fs.writeFileSync(ctx.L.state, JSON.stringify({ schema: 1, components: [] }));
  assert.deepEqual(await loadState(ctx.L.state), { schema: 1, components: {} });
  fs.writeFileSync(ctx.L.state, JSON.stringify({ schema: 1, components: { uv: { version: 7, at: [] } } }));
  assert.deepEqual(await loadState(ctx.L.state), { schema: 1, components: {} });
});

test('stopping a backend during startup prevents an orphan process or a later ready state', async (t) => {
  const { BackendServer } = require('../src/server');
  const ctx = fixture(t);
  // A missing executable also checks that spawn errors are owned and logged.
  const server = new BackendServer(ctx);
  const started = server.start({ timeoutMs: 10000 });
  await server.stop();
  await assert.rejects(started);
  assert.equal(server.child, null);
  assert.equal(server.url, null);
});

test('an early backend startup failure clears admission for the next attempt', async (t) => {
  const { BackendServer } = require('../src/server');
  const ctx = fixture(t);
  fs.writeFileSync(ctx.L.logs, 'a file cannot be a log directory');
  const server = new BackendServer(ctx);
  await assert.rejects(server.start(), /EEXIST/);
  assert.equal(server.startAbort, null);
  fs.unlinkSync(ctx.L.logs);
  await assert.rejects(server.start({ timeoutMs: 5000 }), /backend (could not start|exited)/);
  assert.equal(server.startAbort, null);
  assert.equal(server.child, null);
});

test('a stopped backend can start again with the same origin', { skip: process.platform === 'win32' }, async (t) => {
  const { BackendServer } = require('../src/server');
  const ctx = fixture(t);
  fs.mkdirSync(path.dirname(ctx.L.backendPython), { recursive: true });
  fs.writeFileSync(ctx.L.backendPython, `#!${process.execPath}\n'use strict';
const http = require('node:http');
const port = Number(process.argv[process.argv.indexOf('--port') + 1]);
const server = http.createServer((request, response) => response.end('{}'));
server.listen(port, '127.0.0.1');
process.on('SIGTERM', () => server.close(() => process.exit(0)));
`, { mode: 0o755 });
  const server = new BackendServer(ctx);
  t.after(() => server.stop());
  const first = await server.start({ timeoutMs: 5000 });
  const port = server.port;
  await server.stop();
  assert.equal(server.startAbort, null);
  assert.equal(server.child, null);
  assert.equal(await server.start({ timeoutMs: 5000, preferredPort: port }), first);
});

test('a lone FFmpeg executable cannot mark media tools complete', async (t) => {
  const ctx = fixture(t);
  fs.mkdirSync(path.join(ctx.L.ffmpegDir, 'bin'), { recursive: true });
  fs.writeFileSync(path.join(ctx.L.ffmpegDir, 'bin', 'ffmpeg'), '#!/bin/sh\necho "ffmpeg version test"\n', { mode: 0o755 });
  assert.equal(await buildComponents({ ...ctx, includeOptional: true }).find(c => c.id === 'ffmpeg').verify(ctx), false);
});

test('desktop fingerprints the hashed backend lock in preference to the input requirements', (t) => {
  const ctx = fixture(t);
  const backend = path.join(ctx.L.root, 'backend-source');
  fs.mkdirSync(backend);
  fs.writeFileSync(path.join(backend, 'requirements.txt'), 'range input');
  fs.writeFileSync(path.join(backend, 'requirements.lock'), 'first locked versions');
  ctx.resources = { backend };
  const version = () => buildComponents(ctx).find(c => c.id === 'backend-env').version;
  const first = version();
  fs.writeFileSync(path.join(backend, 'requirements.txt'), 'different input');
  assert.equal(version(), first);
  fs.writeFileSync(path.join(backend, 'requirements.lock'), 'second locked versions');
  assert.notEqual(version(), first);
});
