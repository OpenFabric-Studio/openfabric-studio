'use strict';
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const net = require('node:net');
const path = require('node:path');
const crypto = require('node:crypto');
const { EventEmitter } = require('node:events');
const { spawnTree, killTree, cleanEnv } = require('./proc');
const { ffmpegExecutable } = require('./bootstrap/components');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Parse the app-owned readiness boundary without trusting HTTP success or an unbounded body. */
async function verifyReadiness(response, nonce, pid) {
  const limit = 1024;
  if (response.status !== 200 || !response.body) { await response.body?.cancel(); return false; }
  const reader = response.body.getReader();
  try {
    const declared = response.headers.get('content-length');
    if (declared !== null && (!/^\d+$/.test(declared) || Number(declared) > limit)) return false;
    const chunks = [];
    let size = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > limit) return false;
      chunks.push(value);
    }
    const data = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    return data !== null && typeof data === 'object' && !Array.isArray(data)
      && data.service === 'openfabric-studio' && data.nonce === nonce && /^[0-9a-f]{64}$/.test(data.nonce)
      && Number.isSafeInteger(data.pid) && data.pid > 0 && data.pid === pid;
  } catch { return false; }
  finally { await reader.cancel().catch(() => {}); }
}

function listenOnce(port) {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.once('error', reject);
    srv.listen(port, '127.0.0.1', () => {
      const bound = srv.address().port;
      srv.close(() => resolve(bound));
    });
  });
}

/**
 * A free local port. The UI runs at http://127.0.0.1:<port>, and the browser keeps localStorage (language,
 * saved presets, LoRA list) per origin, so the port has to be the same on every start: `preferred` is tried first.
 */
async function freePort(preferred) {
  if (preferred) {
    try { return await listenOnce(preferred); } catch { /* taken by something else: pick another */ }
  }
  return listenOnce(0);
}

/** Environment of the backend and, through it, of the model servers it starts. */
function backendEnv({ L, manifest, platform }) {
  const sep = path.delimiter;
  const ffmpegBin = path.dirname(ffmpegExecutable(L, manifest, platform));
  const pathParts = [L.uvDir, ffmpegBin, L.yue2Bin, process.env.PATH || ''];
  const env = cleanEnv({
    PATH: pathParts.filter(Boolean).join(sep),
    UV_CACHE_DIR: L.uvCache,
    UV_PYTHON_INSTALL_DIR: L.pythonDir,
    UV_PYTHON_PREFERENCE: 'only-managed',
    HF_HOME: L.hfHome,
    TORCH_HOME: L.torchHome,
    UV_NO_PROGRESS: '1',
    PYTHONUTF8: '1',
    // Python reads .env before choosing defaults. Injecting engine defaults here
    // would override private .env choices before load_dotenv can see them.
    OPENFABRIC_MODULE_ROOT: process.env.OPENFABRIC_MODULE_ROOT || L.root,
    OPENFABRIC_DATA_DIR: L.data,
    OPENFABRIC_LOG_DIR: L.logs,
  });
  // Uvicorn's CLI inherits UVICORN_RELOAD even without --reload. Desktop
  // readiness must identify this owned process rather than a reload monitor.
  delete env.UVICORN_RELOAD;
  return env;
}

/** Owns the FastAPI backend process: start, wait until it answers, stop cleanly. */
class BackendServer extends EventEmitter {
  constructor(ctx) {
    super();
    this.ctx = ctx;
    this.child = null;
    this.url = null;
    this.port = null;
    this.startAbort = null;
    this.stopTask = null;
  }

  async start({ timeoutMs = 90000, preferredPort } = {}) {
    if (this.child || this.startAbort || this.stopTask) throw new Error('the backend is already starting or running');
    const startAbort = new AbortController();
    this.startAbort = startAbort;
    const signal = startAbort.signal;
    let logStream = null;
    try {
      const { L, resources } = this.ctx;
      const nonce = crypto.randomBytes(32).toString('hex');
      await fsp.mkdir(L.logs, { recursive: true });
      await fsp.mkdir(L.data, { recursive: true });
      const port = await freePort(preferredPort);
      signal.throwIfAborted();
      this.port = port;
      logStream = fs.createWriteStream(path.join(L.logs, 'backend-server.log'), { fd: fs.openSync(path.join(L.logs, 'backend-server.log'), 'a') });
      logStream.on('error', () => {
        startAbort.abort(new Error('the backend log could not be written'));
        void this.stop().catch(error => this.emit('stop-error', error));
      });
      logStream.write(`\n--- start ${new Date().toISOString()} port ${port}\n`);

      // One owned backend process provides readiness identity and writes this library.
      const child = spawnTree(L.backendPython, ['-m', 'uvicorn', 'app.main:app', '--workers', '1', '--host', '127.0.0.1', '--port', String(port)], {
        cwd: resources.backend,
        env: { ...backendEnv(this.ctx), OPENFABRIC_DESKTOP_STARTUP_NONCE: nonce },
        stdio: ['ignore', 'pipe', 'pipe'],
      });
      this.child = child;
      child.stdout.pipe(logStream, { end: false });
      child.stderr.pipe(logStream, { end: false });
      let exitCode = null;
      let spawnError = null;
      child.on('error', error => { spawnError = error; logStream.write(`Backend spawn failed: ${error.message}\n`); });
      child.on('close', (code) => { exitCode = code ?? -1; logStream.end(); this.emit('exit', exitCode); });

      const base = `http://127.0.0.1:${port}/`;
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        signal.throwIfAborted();
        if (spawnError) throw new Error(`the backend could not start (see ${path.join(L.logs, 'backend-server.log')})`);
        if (exitCode !== null) throw new Error(`the backend exited with code ${exitCode} (see ${path.join(L.logs, 'backend-server.log')})`);
        try {
          const res = await fetch(`${base}api/desktop/ready`, { redirect: 'error', signal: AbortSignal.any([signal, AbortSignal.timeout(2000)]) });
          const ready = await verifyReadiness(res, nonce, child.pid);
          signal.throwIfAborted();
          if (ready && this.child === child && child.exitCode === null && child.signalCode === null) { this.url = base; return base; }
        } catch { /* not up yet */ }
        await sleep(400);
      }
      throw new Error('the backend did not answer in time');
    } catch (error) { await this.stop(); throw error; }
    finally {
      if (this.startAbort === startAbort) this.startAbort = null;
      if (!this.child) logStream?.end();
    }
  }

  /**
   * Stops the model servers through the backend first (otherwise a GPU process can outlive the app),
   * then ends the backend and everything left under it.
   */
  async stop() {
    this.startAbort?.abort(new Error('backend startup cancelled'));
    if (this.stopTask) return this.stopTask;
    const child = this.child;
    if (!child) return;
    this.stopTask = (async () => {
      if (this.url && child.exitCode === null && child.signalCode === null) {
        try {
          await fetch(`${this.url}api/orchestrator/stop`, { method: 'POST', signal: AbortSignal.timeout(30000) });
        } catch { /* the backend may already be gone */ }
      }
      // FastAPI lifespan drains model/job registries after SIGTERM.
      await killTree(child, { graceMs: 150000 });
      this.child = null;
      this.url = null;
    })();
    try { await this.stopTask; }
    finally { this.stopTask = null; }
  }
}

module.exports = { BackendServer, backendEnv, freePort, verifyReadiness };
