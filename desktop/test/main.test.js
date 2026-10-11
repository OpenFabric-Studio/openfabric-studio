'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { EventEmitter } = require('node:events');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const tick = () => new Promise(resolve => setImmediate(resolve));

/** Exercise the shipped main process without opening Electron or writing preferences. */
function fixture({ preflight = Promise.resolve(true), config = Promise.resolve({ dataRoot: '/tmp/openfabric-main-fixture' }) } = {}) {
  const src = path.resolve(__dirname, '../src');
  const events = [], servers = [], handlers = new Map();
  let checks = 0, quits = 0;
  class Server extends EventEmitter {
    constructor() {
      super(); this.ready = deferred(); this.port = 9100 + servers.length;
      this.url = `http://127.0.0.1:${this.port}/`; this.child = {}; this.stops = 0;
      servers.push(this);
    }
    start() { return this.ready.promise; }
    async stop() {
      this.stops++;
      if (this.stopGate) await this.stopGate.promise;
      this.child = null;
      this.ready.reject(new Error('backend startup cancelled'));
    }
  }
  const app = {
    requestSingleInstanceLock: () => true,
    on: (event, callback) => handlers.set(event, callback),
    whenReady: () => new Promise(() => {}),
    getPath: () => '/unused-test-preferences', getVersion: () => 'test',
    quit: () => { quits++; },
  };
  class Window {
    constructor() {
      events.push('window created');
      this.webContents = { send: (_, value) => events.push(value.type), setWindowOpenHandler() {}, on() {} };
    }
    once() {} on() {} isDestroyed() { return false; }
    async loadURL(url) { events.push('app ' + url); }
    async loadFile(_, options) { events.push('setup ' + (options.query.state || 'ready')); }
  }
  const context = {
    console, setTimeout, AbortController, module: { exports: {} }, __dirname: src,
    process: { env: {}, platform: process.platform }, events,
    require(name) {
      if (name === 'electron') return { app, BrowserWindow: Window, ipcMain: { handle() {} }, dialog: {}, shell: {}, Menu: { setApplicationMenu() {}, buildFromTemplate() {} }, Notification: {} };
      if (name === './bootstrap/run') return { isSetupComplete: async () => { checks++; return preflight; } };
      if (name === './bootstrap/checks') return {};
      if (name === './server') return { BackendServer: Server };
      if (name === './config') return { loadConfig: async () => config, updateConfig: async () => ({}) };
      return name.startsWith('.') ? require(path.resolve(src, name)) : require(name);
    },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(src, 'main.js'), 'utf8') + `
    ctx = {};
    win = { isDestroyed: () => false,
      webContents: { send: (_, value) => events.push(value.type) },
      loadURL: async url => events.push('app ' + url),
      loadFile: async (_, options) => events.push('setup ' + (options.query.state || 'ready')) };
    globalThis.launchTest = launch;
    globalThis.bootTest = boot;
  `, context);
  return {
    launch: () => context.launchTest(), boot: () => context.bootTest(), servers, events,
    checks: () => checks, quits: () => quits,
    quit() { handlers.get('before-quit')({ preventDefault: () => events.push('quit deferred') }); },
  };
}

test('concurrent Open/Retry requests share one launch and never replace success with a stale failure', async () => {
  const ready = deferred(), state = fixture({ preflight: ready.promise });
  const first = state.launch(), second = state.launch();
  try {
    await tick();
    assert.equal(state.checks(), 1, 'preflight must belong to one launch');
    ready.resolve(true); await tick();
    assert.equal(state.servers.length, 1, 'only one backend can start');
    state.servers[0].ready.resolve(state.servers[0].url);
    await Promise.all([first, second]);
    assert.equal(state.events.filter(value => value.startsWith('app ')).length, 1);
    assert.equal(state.events.some(value => value === 'setup crashed'), false);
  } finally {
    ready.resolve(true); await tick();
    for (const server of state.servers) server.ready.resolve(server.url);
    await Promise.allSettled([first, second]);
  }
});

test('initial saved-root boot owns its preflight and drains it on quit', async () => {
  const ready = deferred(), state = fixture({ preflight: ready.promise });
  const booting = state.boot(); await tick(); state.quit();
  ready.resolve(true); await booting; await tick();
  assert.ok(state.events.includes('quit deferred'), 'Initial preflight must belong to the launch registry');
  assert.equal(state.servers.length, 0);
  assert.equal(state.quits(), 1);
});

test('quit while boot reads configuration prevents a later window and preflight', async () => {
  const config = deferred(), state = fixture({ config: config.promise });
  const booting = state.boot(); state.quit();
  config.resolve({ dataRoot: '/tmp/openfabric-main-fixture' }); await booting;
  assert.equal(state.events.includes('window created'), false);
  assert.equal(state.checks(), 0);
});

test('quit during launch preflight drains the launch without spawning a backend afterward', async () => {
  const ready = deferred(), state = fixture({ preflight: ready.promise });
  const launching = state.launch();
  await tick(); state.quit();
  ready.resolve(true); await tick();
  for (const server of state.servers) server.ready.resolve(server.url);
  await launching; await tick();
  assert.equal(state.servers.length, 0, 'quit must prevent a post-preflight backend spawn');
  assert.ok(state.events.includes('quit deferred'));
  assert.equal(state.quits(), 1);
  assert.equal(state.events.some(value => value.startsWith('app ')), false);
});

test('quit while a restart drains the old backend prevents a replacement from starting', async () => {
  const state = fixture();
  const first = state.launch(); await tick();
  const original = state.servers[0]; original.ready.resolve(original.url); await first;
  original.stopGate = deferred();
  const restart = state.launch(); await tick(); state.quit();
  original.stopGate.resolve(); await tick();
  for (const server of state.servers) server.ready.resolve(server.url);
  await restart; await tick();
  assert.equal(state.servers.length, 1, 'quit must not create a replacement after old cleanup');
  assert.equal(state.quits(), 1);
});
