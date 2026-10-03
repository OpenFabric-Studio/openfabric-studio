'use strict';
// One source launcher for Windows/macOS/Linux. Optional engines are installed in Settings.
const fs = require('node:fs/promises');
const path = require('node:path');
const crypto = require('node:crypto');
const { runCommand, spawnTree, killTree, cleanEnv } = require('../src/proc');

function sourceLayout(root, platform = process.platform) {
  const venv = path.join(root, 'backend', '.venv');
  return {
    venv,
    python: path.join(venv, platform === 'win32' ? 'Scripts' : 'bin', platform === 'win32' ? 'python.exe' : 'python'),
    requirementsMarker: path.join(venv, '.openfabric-requirements.sha256'),
    frontendMarker: path.join(root, 'frontend', 'node_modules', '.openfabric-lock.sha256'),
  };
}

function sourceEnvironment(env, development) {
  return { ...env, ...(development ? { OPENFABRIC_SETUP_ALLOWED_ORIGINS: 'http://localhost:5173,http://127.0.0.1:5173' } : {}) };
}

function npmCommand(args, env, platform = process.platform) {
  const npm = env.NPM_BIN || 'npm';
  if (platform !== 'win32') return { command: npm, args };
  // cmd.exe is required for npm.cmd. Only fixed launcher arguments are passed.
  if (/["&|<>%^\r\n]/.test(npm) || args.some(arg => !/^[a-z0-9.-]+$/i.test(arg))) throw new Error('Unsupported npm command path or argument');
  return { command: env.ComSpec || 'cmd.exe', args: ['/d', '/s', '/c', `""${npm}" ${args.join(' ')}"`] };
}

async function writeMarker(file, value) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  const temporary = `${file}.${crypto.randomUUID()}.tmp`;
  try { await fs.writeFile(temporary, value, { flag: 'wx' }); await fs.rename(temporary, file); }
  finally { await fs.rm(temporary, { force: true }); }
}

async function prepareSource({ root, env = process.env, signal, platform = process.platform }) {
  const L = sourceLayout(root, platform);
  const childEnv = { ...cleanEnv(), ...env };
  delete childEnv.ELECTRON_RUN_AS_NODE;
  const uv = childEnv.UV_BIN || 'uv';
  if (!(await fs.stat(L.python).catch(() => null))) {
    await runCommand(uv, ['venv', '--python', '3.12', '--seed', L.venv], { env: childEnv, signal, onLine: console.log });
  }
  const lock = path.join(root, 'backend', 'requirements.lock');
  const locked = (await fs.stat(lock).catch(() => null))?.isFile() === true;
  const requirements = locked ? lock : path.join(root, 'backend', 'requirements.txt');
  const hash = crypto.createHash('sha256').update(path.basename(requirements)).update(await fs.readFile(requirements)).digest('hex');
  const previous = await fs.readFile(L.requirementsMarker, 'utf8').catch(() => '');
  if (previous !== hash) {
    await runCommand(uv, ['pip', 'install', '--python', L.python, ...(locked ? ['--require-hashes', '--only-binary', ':all:'] : []), '-r', requirements], { env: childEnv, signal, onLine: console.log });
  }
  await runCommand(L.python, ['-m', 'pip', 'check'], { env: childEnv, signal, onLine: console.log, timeoutMs: 15000 });
  await runCommand(L.python, ['-c', 'import sys; assert (3,12) <= sys.version_info[:2] < (3,13), "Python 3.12 is required; preserve the old venv and recreate it with uv"; import fastapi, uvicorn, httpx, pydantic, multipart, dotenv, numpy, PIL'], { env: childEnv, signal, timeoutMs: 15000 });
  signal?.throwIfAborted();
  if (previous !== hash) await writeMarker(L.requirementsMarker, hash);
  const lockHash = crypto.createHash('sha256').update(await fs.readFile(path.join(root, 'frontend', 'package-lock.json'))).update(process.versions.node.split('.')[0]).digest('hex');
  if ((await fs.readFile(L.frontendMarker, 'utf8').catch(() => '')) !== lockHash) {
    const npm = npmCommand(['ci'], childEnv, platform);
    await runCommand(npm.command, npm.args, { cwd: path.join(root, 'frontend'), env: childEnv, signal, onLine: console.log });
    signal?.throwIfAborted();
    await writeMarker(L.frontendMarker, lockHash);
  }
  return L;
}

async function launchSource({ root, development = false, env = process.env }) {
  const controller = new AbortController();
  const children = [];
  let stopping = null;
  const stop = () => {
    controller.abort(new Error('source launch cancelled'));
    stopping ||= Promise.all(children.map(({ child, graceMs }) => killTree(child, { graceMs })));
    return stopping;
  };
  const onSignal = () => { void stop(); };
  process.on('SIGINT', onSignal);
  process.on('SIGTERM', onSignal);
  try {
    const childEnv = sourceEnvironment({ ...cleanEnv(), ...env }, development);
    const port = Number(childEnv.OPENFABRIC_PORT || 9000);
    if (!Number.isInteger(port) || port < 1 || port > 65535 || (development && port !== 9000)) throw new Error('Use OPENFABRIC_PORT=1..65535; development requires port 9000 for the Vite API proxy');
    const L = await prepareSource({ root, env: childEnv, signal: controller.signal });
    if (!development) {
      const npm = npmCommand(['run', 'build'], childEnv);
      await runCommand(npm.command, npm.args, { cwd: path.join(root, 'frontend'), env: childEnv, signal: controller.signal, onLine: console.log });
    }
    controller.signal.throwIfAborted();
    const own = (command, args, cwd, graceMs = 1500) => {
      const child = spawnTree(command, args, { cwd, env: childEnv, stdio: 'inherit' });
      children.push({ child, graceMs });
      return new Promise((resolve, reject) => { child.once('error', reject); child.once('close', code => code === 0 || controller.signal.aborted ? resolve() : reject(new Error(`${path.basename(command)} exited with code ${code}`))); });
    };
    const backend = own(L.python, ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)], path.join(root, 'backend'), 150000);
    const running = [backend];
    if (development) {
      const npm = npmCommand(['run', 'dev', '--', '--host', '127.0.0.1', '--port', '5173', '--strictPort'], childEnv);
      running.push(own(npm.command, npm.args, path.join(root, 'frontend')));
    }
    console.log(`Open http://127.0.0.1:${development ? 5173 : port} — Ctrl+C stops the owned servers.`);
    // Either server exiting drains both; every promise has an observer.
    const settled = running.map(task => task.then(() => ({ error: null }), error => ({ error })));
    const result = await Promise.race(settled);
    await stop();
    await Promise.all(settled);
    if (result.error) throw result.error;
  } finally {
    await stop();
    process.removeListener('SIGINT', onSignal);
    process.removeListener('SIGTERM', onSignal);
  }
}

if (require.main === module) {
  const args = process.argv.slice(2);
  if (args.some(arg => arg !== '--dev')) { console.error('Usage: node desktop/scripts/launch-source.js [--dev]'); process.exitCode = 2; }
  else launchSource({ root: path.resolve(__dirname, '../..'), development: args.includes('--dev') }).catch(error => { console.error(error.message); process.exitCode = 1; });
}

module.exports = { sourceLayout, sourceEnvironment, prepareSource, launchSource, npmCommand };
