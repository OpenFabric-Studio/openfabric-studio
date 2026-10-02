'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const manifest = require('../manifest.json');
const { evaluateGpu, freeBytes, runChecks } = require('../src/bootstrap/checks');
const { runSetup, isSetupComplete, describePlan } = require('../src/bootstrap/run');
const { buildComponents, demucsProject, ffmpegExecutable, recoverAceStepSource, replaceAceStepSource } = require('../src/bootstrap/components');
const { extract, tarBinary } = require('../src/bootstrap/extract');
const { layout, PLATFORM } = require('../src/paths');
const { backendEnv } = require('../src/server');
const { ensureWritableDir } = require('../src/config');

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), 'remiqora-setup-'));
const req = manifest.requirements;

test('ACE recovery retains conflicting checkpoint trees', async () => {
  const current = path.join(tmp(), 'ace');
  for (const [dir, contents] of [[current, 'new model'], [`${current}.previous`, 'old model']]) {
    fs.mkdirSync(path.join(dir, 'checkpoints'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'checkpoints', 'model'), contents);
  }
  await recoverAceStepSource(current);
  assert.equal(fs.readFileSync(path.join(current, 'checkpoints', 'model'), 'utf8'), 'new model');
  assert.equal(fs.readFileSync(path.join(`${current}.previous`, 'checkpoints', 'model'), 'utf8'), 'old model');
});

test('ACE replacement and interrupted swaps preserve downloaded checkpoints', async () => {
  const current = path.join(tmp(), 'ace');
  fs.mkdirSync(path.join(`${current}.previous`, 'checkpoints'), { recursive: true });
  fs.writeFileSync(path.join(`${current}.previous`, 'checkpoints', 'model'), 'precious');
  await recoverAceStepSource(current);
  const staged = `${current}.tmp`;
  fs.mkdirSync(staged);
  fs.writeFileSync(path.join(staged, 'source'), 'replacement');
  await replaceAceStepSource(current, staged);
  assert.equal(fs.readFileSync(path.join(current, 'checkpoints', 'model'), 'utf8'), 'precious');
  assert.equal(fs.readFileSync(path.join(current, 'source'), 'utf8'), 'replacement');
  assert.equal(fs.existsSync(`${current}.previous`), false);
});

test('ACE failed source promotion restores the previous tree', async () => {
  const fsp = require('node:fs/promises');
  const current = path.join(tmp(), 'ace');
  const staged = `${current}.tmp`;
  fs.mkdirSync(path.join(current, 'checkpoints'), { recursive: true });
  fs.mkdirSync(staged);
  fs.writeFileSync(path.join(current, 'checkpoints', 'model'), 'retain');
  const rename = fsp.rename;
  fsp.rename = async (from, to) => {
    if (from === staged) throw new Error('simulated interruption');
    return rename(from, to);
  };
  try { await assert.rejects(replaceAceStepSource(current, staged), /simulated interruption/); }
  finally { fsp.rename = rename; }
  assert.equal(fs.readFileSync(path.join(current, 'checkpoints', 'model'), 'utf8'), 'retain');
  assert.equal(fs.existsSync(staged), true);
});

test('later ACE updates retain earlier checkpoint conflicts', async () => {
  const parent = tmp();
  const current = path.join(parent, 'ace');
  for (const [dir, contents] of [[current, 'new'], [`${current}.previous`, 'old']]) {
    fs.mkdirSync(path.join(dir, 'checkpoints'), { recursive: true });
    fs.writeFileSync(path.join(dir, 'checkpoints', 'model'), contents);
  }
  const staged = `${current}.tmp`;
  fs.mkdirSync(staged);
  await replaceAceStepSource(current, staged);
  const retained = fs.readdirSync(parent).find((name) => name.startsWith('ace.preserved-'));
  assert.ok(retained);
  assert.equal(fs.readFileSync(path.join(parent, retained, 'checkpoints', 'model'), 'utf8'), 'old');
  assert.equal(fs.readFileSync(path.join(current, 'checkpoints', 'model'), 'utf8'), 'new');
});

test('ACE version and verification include source commit and exact patch hash', async () => {
  const L = layout(tmp(), 'darwin-arm64', manifest);
  const patch = path.join(tmp(), 'patch');
  fs.writeFileSync(patch, 'first patch');
  const resources = { backend: path.join(__dirname, '..', '..', 'backend'), acePatch: patch, modelManagerPatch: path.join(__dirname, '..', '..', 'external', 'patches', 'yue-model-resume.patch') };
  const component = () => buildComponents({ L, manifest, platform: 'darwin-arm64', resources }).find((c) => c.id === 'ace-step');
  const first = component();
  fs.mkdirSync(path.join(L.aceStep, '.venv'), { recursive: true });
  fs.writeFileSync(path.join(L.aceStep, '.remiqora-patched'), 'obsolete-marker');
  assert.equal(await first.verify(), false);
  assert.ok(first.version.includes(manifest.aceStep.commit));
  fs.writeFileSync(patch, 'second patch');
  assert.notEqual(component().version, first.version);
});

test('model manager upgrades verify applied content, not a comment marker', async () => {
  const L = layout(tmp(), 'darwin-arm64', manifest);
  const resources = { backend: path.join(__dirname, '..', '..', 'backend'), acePatch: path.join(__dirname, '..', '..', 'external', 'patches', 'ace-step.patch'), modelManagerPatch: path.join(__dirname, '..', '..', 'external', 'patches', 'yue-model-resume.patch') };
  fs.mkdirSync(path.join(L.yue2, 'tools'), { recursive: true });
  fs.copyFileSync(path.join(__dirname, 'fixtures', 'model_manager_v2.py'), path.join(L.yue2, 'tools', 'model_manager_v2.py'));
  const component = buildComponents({ L, manifest, platform: 'darwin-arm64', resources }).find((c) => c.id === 'model-manager-resume');
  assert.ok(component);
  assert.equal(component.network, false);
  await component.install({}, () => {});
  assert.equal(await component.verify(), true);
  const script = path.join(L.yue2, 'tools', 'model_manager_v2.py');
  fs.appendFileSync(script, '\n# accidental content change\n');
  assert.equal(await component.verify(), false);
  await component.install({}, () => {});
  assert.equal(await component.verify(), true);
});

test('actual installation enforces pending disk and writability checks, including local-only updates', async () => {
  const L = layout(tmp(), PLATFORM, manifest);
  const installed = [];
  const ctx = { L, manifest, platform: PLATFORM, resources: {}, components: fakeComponents(installed).map((c) => ({ ...c, network: false })),
    checkInstall: async ({ requiredBytes, networkRequired }) => {
      assert.ok(requiredBytes > 0);
      assert.equal(networkRequired, false);
      return { blocking: { code: 'no-disk' } };
    } };
  await assert.rejects(runSetup(ctx), (err) => err.code === 'no-disk');
  assert.deepEqual(installed, []);
});

test('the installer refuses an unwritable root before a local update runs', async () => {
  const root = path.join(tmp(), 'occupied');
  fs.writeFileSync(root, 'existing file');
  const L = layout(root, PLATFORM, manifest);
  const installed = [];
  await assert.rejects(runSetup({ L, manifest, platform: PLATFORM, resources: {}, components: fakeComponents(installed).map((c) => ({ ...c, network: false })) }), (error) => error.code === 'not-writable');
  assert.deepEqual(installed, []);
  assert.equal(fs.readFileSync(root, 'utf8'), 'existing file');
});

test('writability probes preserve existing files in the chosen root', async () => {
  const root = tmp();
  fs.writeFileSync(path.join(root, '.write-test'), 'user-owned content');
  await ensureWritableDir(root);
  assert.equal(fs.readFileSync(path.join(root, '.write-test'), 'utf8'), 'user-owned content');
  assert.deepEqual(fs.readdirSync(root), ['.write-test']);
});

test('the setup plan measures only pending components and marks local-only updates', async () => {
  const L = layout(tmp(), PLATFORM, manifest);
  const ctx = { L, manifest, platform: PLATFORM, resources: {}, components: fakeComponents([]) };
  await runSetup(ctx);
  ctx.components = fakeComponents([]).map((c) => c.id === 'c' ? { ...c, version: '2', network: false } : c);
  const plan = await describePlan(ctx);
  assert.equal(plan.filter((c) => !c.done).reduce((n, c) => n + c.weight, 0), 10);
  assert.equal(plan.find((c) => c.id === 'c').network, false);
});

test('local updates work offline and disk requirements exclude completed components', async () => {
  const dir = tmp();
  const result = await runChecks({ platform: 'darwin-arm64', dataRoot: dir, manifest, plan: [
    { id: 'models', weight: Number.MAX_SAFE_INTEGER, done: true, network: true },
    { id: 'patch', weight: 100, done: false, network: false },
  ], fetchImpl: async () => { throw new Error('network must not be consulted'); } });
  assert.equal(result.blocking, null);
  assert.equal(result.items.find((c) => c.id === 'disk').requiredBytes, 150);
  assert.equal(result.items.find((c) => c.id === 'network').required, false);
});

test('GPU verdicts', () => {
  assert.deepEqual(evaluateGpu({ name: 'RTX 4080', driver: '610.47', vramMiB: 16376, computeCap: 8.9 }, req), { ok: true });
  assert.equal(evaluateGpu(null, req).code, 'no-gpu');
  assert.equal(evaluateGpu({ name: 'RTX 4080', driver: '552.44', vramMiB: 16376, computeCap: 8.9 }, req).code, 'old-driver');
  assert.equal(evaluateGpu({ name: 'GTX 1080', driver: '610.47', vramMiB: 8192, computeCap: 6.1 }, req).code, 'old-gpu');
  assert.equal(evaluateGpu({ name: 'RTX 4080', driver: '610.47', vramMiB: 16376, computeCap: null }, req).ok, true);
});

test('checks refuse unsupported platforms and report free space', async () => {
  const dir = tmp();
  assert.ok((await freeBytes(path.join(dir, 'not', 'created', 'yet'))) > 0);
  const linux = await runChecks({ platform: 'linux-x64', dataRoot: dir, manifest, fetchImpl: async () => ({}) });
  assert.equal(linux.blocking.code, 'unsupported-platform');
  // The real requirement is 50 GB, which a CI runner or a small disk may not have: take the disk out of this test.
  const roomy = { ...manifest, requirements: { ...manifest.requirements, minFreeBytes: 1 } };
  const offline = await runChecks({ platform: 'darwin-arm64', dataRoot: dir, manifest: roomy, fetchImpl: async () => { throw new Error('down'); } });
  assert.equal(offline.blocking.code, 'offline');
  const online = await runChecks({ platform: 'darwin-arm64', dataRoot: dir, manifest: roomy, fetchImpl: async () => ({ status: 200 }) });
  assert.equal(online.blocking, null);
  const full = await runChecks({ platform: 'darwin-arm64', dataRoot: dir, manifest: { ...manifest, requirements: { ...manifest.requirements, minFreeBytes: Number.MAX_SAFE_INTEGER } }, fetchImpl: async () => ({ status: 200 }) });
  assert.equal(full.blocking.code, 'no-disk');
});

/** Fake components: the runner does not care what a component installs. */
function fakeComponents(log, { failOn } = {}) {
  return ['a', 'b', 'c'].map((id) => ({
    id, weight: 10, version: '1',
    verify: async () => true,
    async install(_ctx, report) {
      log.push(id);
      report({ done: 5, total: 10 });
      if (id === failOn) throw new Error(`boom in ${id}`);
    },
  }));
}

test('runs components in order, persists state and skips finished ones on the next run', async () => {
  const L = layout(tmp(), PLATFORM, manifest);
  const log = [];
  const events = [];
  const ctx = { L, manifest, platform: PLATFORM, resources: {}, components: fakeComponents(log, { failOn: 'b' }) };
  await assert.rejects(runSetup(ctx, (e) => events.push(e)), (err) => err.componentId === 'b');
  assert.deepEqual(log, ['a', 'b']);
  assert.equal(await isSetupComplete(ctx), false);

  log.length = 0;
  ctx.components = fakeComponents(log);
  await runSetup(ctx, () => {});
  assert.deepEqual(log, ['b', 'c'], 'a is already installed and must not run again');
  assert.equal(await isSetupComplete(ctx), true);
  assert.deepEqual((await describePlan(ctx)).map((p) => p.done), [true, true, true]);
});

test('a new component version is installed again', async () => {
  const L = layout(tmp(), PLATFORM, manifest);
  const log = [];
  const ctx = { L, manifest, platform: PLATFORM, resources: {}, components: fakeComponents(log) };
  await runSetup(ctx, () => {});
  ctx.components = fakeComponents(log).map((c) => (c.id === 'c' ? { ...c, version: '2' } : c));
  log.length = 0;
  await runSetup(ctx, () => {});
  assert.deepEqual(log, ['c']);
});

test('skipped components are reported and do not count as installed', async () => {
  const L = layout(tmp(), PLATFORM, manifest);
  const log = [];
  const events = [];
  const ctx = { L, manifest, platform: PLATFORM, resources: {}, skip: ['b'], components: fakeComponents(log).map((c) => ({ ...c, verify: async () => false })) };
  await runSetup(ctx, (e) => events.push(e));
  assert.deepEqual(log, ['a', 'c']);
  assert.ok(events.some((e) => e.id === 'b' && e.status === 'skipped'));
});

test('the real plan has every component, in dependency order', () => {
  const L = layout(tmp(), 'win32-x64', manifest);
  const resources = { backend: path.join(__dirname, '..', '..', 'backend'), acePatch: path.join(__dirname, '..', '..', 'external', 'patches', 'ace-step.patch') };
  const ids = buildComponents({ L, manifest, platform: 'win32-x64', resources }).map((c) => c.id);
  assert.deepEqual(ids, ['uv', 'ffmpeg', 'engine', 'model-manager-resume', 'backend-env', 'ace-step', 'ace-models', 'demucs', 'weights']);
});

test('Demucs gets the CUDA torch index off macOS only', () => {
  assert.match(demucsProject('win32-x64'), /pytorch-cu128/);
  assert.doesNotMatch(demucsProject('darwin-arm64'), /pytorch-cu128/);
});

test('the backend keeps its port between starts so localStorage survives, and moves only when it must', async () => {
  const net = require('node:net');
  const { freePort } = require('../src/server');
  const first = await freePort();
  assert.equal(await freePort(first), first, 'a free preferred port is reused');
  const blocker = net.createServer();
  await new Promise((r) => blocker.listen(first, '127.0.0.1', r));
  try {
    const moved = await freePort(first);
    assert.notEqual(moved, first, 'a taken port is replaced');
    assert.ok(moved > 0);
  } finally {
    blocker.close();
  }
});

test('the backend environment points every path at the data root', () => {
  const L = layout(tmp(), 'win32-x64', manifest);
  const env = backendEnv({ L, manifest, platform: 'win32-x64' });
  assert.equal(env.REMIQORA_DATA_DIR, L.data);
  assert.equal(env.REMIQORA_LOG_DIR, L.logs);
  assert.equal(env.YUE2_DIR, L.yue2);
  assert.equal(env.HF_HOME, L.hfHome, 'model caches stay inside the chosen folder');
  assert.equal(env.TORCH_HOME, L.torchHome);
  assert.equal(env.CUDA_BIN_DIR, L.yue2Bin);
  assert.ok(env.PATH.split(path.delimiter).includes(L.uvDir));
  assert.equal(env.ELECTRON_RUN_AS_NODE, undefined);
});

test('the uv component finds the binary inside a tarball with a top-level folder (the macOS layout)', async (t) => {
  const http = require('node:http');
  const crypto = require('node:crypto');
  const src = tmp();
  fs.mkdirSync(path.join(src, 'uv-aarch64-apple-darwin'), { recursive: true });
  fs.writeFileSync(path.join(src, 'uv-aarch64-apple-darwin', 'uv'), 'fake uv binary');
  fs.writeFileSync(path.join(src, 'uv-aarch64-apple-darwin', 'uvx'), 'fake uvx');
  const archive = path.join(tmp(), 'uv.tar.gz');
  execFileSync(tarBinary(), ['-czf', archive, '-C', src, 'uv-aarch64-apple-darwin']);
  const body = fs.readFileSync(archive);
  const server = http.createServer((_req, res) => { res.writeHead(200, { 'content-length': body.length }); res.end(body); });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  t.after(() => { server.closeAllConnections(); server.close(); });

  const fake = JSON.parse(JSON.stringify(manifest));
  fake.uv.assets['darwin-arm64'] = {
    url: `http://127.0.0.1:${server.address().port}/uv-aarch64-apple-darwin.tar.gz`,
    sha256: crypto.createHash('sha256').update(body).digest('hex'), bytes: body.length, bin: 'uv-aarch64-apple-darwin/uv',
  };
  const L = layout(tmp(), 'darwin-arm64', fake);
  const resources = { backend: path.join(__dirname, '..', '..', 'backend'), acePatch: path.join(__dirname, '..', '..', 'external', 'patches', 'ace-step.patch') };
  const uv = buildComponents({ L, manifest: fake, platform: 'darwin-arm64', resources }).find((c) => c.id === 'uv');
  await uv.install({ L, manifest: fake, platform: 'darwin-arm64', signal: undefined }, () => {});
  assert.equal(fs.readFileSync(L.uvBin, 'utf8'), 'fake uv binary');
});

test('ffmpeg: a single static binary (the macOS build) is installed where the backend looks for it', async (t) => {
  const http = require('node:http');
  const crypto = require('node:crypto');
  const body = Buffer.from('fake static ffmpeg');
  const server = http.createServer((_req, res) => { res.writeHead(200, { 'content-length': body.length }); res.end(body); });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  t.after(() => { server.closeAllConnections(); server.close(); });

  const fake = JSON.parse(JSON.stringify(manifest));
  fake.ffmpeg.assets['darwin-arm64'] = {
    version: 'test-1', kind: 'binary', url: `http://127.0.0.1:${server.address().port}/ffmpeg-osx-arm64`,
    sha256: crypto.createHash('sha256').update(body).digest('hex'), bytes: body.length,
  };
  const L = layout(tmp(), 'darwin-arm64', fake);
  const resources = { backend: path.join(__dirname, '..', '..', 'backend'), acePatch: path.join(__dirname, '..', '..', 'external', 'patches', 'ace-step.patch') };
  const ffmpeg = buildComponents({ L, manifest: fake, platform: 'darwin-arm64', resources }).find((c) => c.id === 'ffmpeg');
  assert.equal(await ffmpeg.verify({}), false);
  await ffmpeg.install({ L, manifest: fake, platform: 'darwin-arm64' }, () => {});
  const exe = ffmpegExecutable(L, fake, 'darwin-arm64');
  assert.equal(fs.readFileSync(exe, 'utf8'), 'fake static ffmpeg');
  assert.equal(path.dirname(exe), path.join(L.ffmpegDir, 'bin'));
  assert.equal(await ffmpeg.verify({}), true);
  assert.equal(ffmpeg.version, 'test-1', 'the recorded version follows the platform asset');
  if (process.platform !== 'win32') assert.ok(fs.statSync(exe).mode & 0o100, 'executable bit set');
});

test('the Windows FFmpeg asset keeps its recorded version and folder layout', () => {
  const L = layout(tmp(), 'win32-x64', manifest);
  const resources = { backend: path.join(__dirname, '..', '..', 'backend'), acePatch: path.join(__dirname, '..', '..', 'external', 'patches', 'ace-step.patch') };
  const ffmpeg = buildComponents({ L, manifest, platform: 'win32-x64', resources }).find((c) => c.id === 'ffmpeg');
  assert.equal(ffmpeg.version, manifest.ffmpeg.version, 'unchanged, so existing installs are not re-downloaded');
  assert.match(ffmpegExecutable(L, manifest, 'win32-x64'), /ffmpeg-9\.0\.1-essentials_build[\\/]bin[\\/]ffmpeg\.exe$/);
});

test('every uv asset in the manifest names a binary that is "uv" or ends in "/uv"', () => {
  for (const [platform, asset] of Object.entries(manifest.uv.assets)) {
    assert.match(asset.bin, /(^|\/)uv(\.exe)?$/, platform);
  }
});

test('extracts a tar.gz archive', async () => {
  const src = tmp();
  fs.mkdirSync(path.join(src, 'top', 'sub'), { recursive: true });
  fs.writeFileSync(path.join(src, 'top', 'sub', 'f.txt'), 'hello');
  const archive = path.join(tmp(), 'a.tar.gz');
  execFileSync(tarBinary(), ['-czf', archive, '-C', src, 'top']); // the same bsdtar the app uses; GNU tar reads "E:\..." as a remote host
  const out = tmp();
  await extract(archive, out, { stripComponents: 1 });
  assert.equal(fs.readFileSync(path.join(out, 'sub', 'f.txt'), 'utf8'), 'hello');
});
