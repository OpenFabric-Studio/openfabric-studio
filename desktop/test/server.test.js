'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const http = require('node:http');
const manifest = require('../manifest.json');
const { layout } = require('../src/paths');
const { BackendServer, verifyReadiness } = require('../src/server');

const nonce = 'a'.repeat(64);
const ready = { service: 'openfabric-studio', nonce, pid: 123 };

test('readiness requires bounded JSON for this exact service, nonce and PID', async () => {
  assert.equal(await verifyReadiness(new Response(JSON.stringify(ready)), nonce, 123), true);
  for (const body of ['<html>unrelated service</html>', 'null', '[]', '{}', JSON.stringify({ ...ready, service: 'other' }), JSON.stringify({ ...ready, nonce: 'b'.repeat(64) }), JSON.stringify({ ...ready, pid: 124 }), JSON.stringify({ ...ready, pid: '123' }), JSON.stringify({ ...ready, padding: 'x'.repeat(2000) })]) {
    assert.equal(await verifyReadiness(new Response(body), nonce, 123), false, body.slice(0, 80));
  }
  assert.equal(await verifyReadiness(new Response(JSON.stringify(ready), { status: 201 }), nonce, 123), false);
  assert.equal(await verifyReadiness(new Response(JSON.stringify(ready), { headers: { 'content-length': '2000' } }), nonce, 123), false);
});

async function serverFixture(t, { delayMs = 0 } = {}) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'openfabric-backend-identity-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const L = layout(root, 'darwin-arm64', manifest);
  await fs.mkdir(path.dirname(L.backendPython), { recursive: true });
  await fs.writeFile(L.backendPython, `#!${process.execPath}
const http = require('node:http');
const port = Number(process.argv[process.argv.indexOf('--port') + 1]);
const workersIndex = process.argv.indexOf('--workers');
const workers = workersIndex < 0 ? Number(process.env.WEB_CONCURRENCY || 1) : Number(process.argv[workersIndex + 1]);
const reload = process.env.UVICORN_RELOAD === 'true';
let server;
process.on('SIGTERM', () => server ? server.close(() => process.exit(0)) : process.exit(0));
setTimeout(() => {
  server = http.createServer((request, response) => {
    response.setHeader('content-type', 'application/json');
    response.end(request.url === '/api/desktop/ready'
      ? JSON.stringify({ service: 'openfabric-studio', nonce: process.env.OPENFABRIC_DESKTOP_STARTUP_NONCE, pid: process.pid + (workers > 1 || reload ? 1 : 0) }) : '{}');
  });
  server.on('error', () => process.exit(1));
  server.listen(port, '127.0.0.1');
}, ${delayMs});
`, { mode: 0o755 });
  const server = new BackendServer({ L, manifest, platform: 'darwin-arm64', resources: { backend: root } });
  t.after(() => server.stop());
  return server;
}

test('backend restart retains its origin and uses a fresh startup identity', { skip: process.platform === 'win32' }, async (t) => {
  const server = await serverFixture(t);
  const first = await server.start({ timeoutMs: 5000 });
  const firstIdentity = await (await fetch(`${first}api/desktop/ready`)).json();
  assert.match(firstIdentity.nonce, /^[0-9a-f]{64}$/);
  assert.equal(firstIdentity.pid, server.child.pid);
  const port = server.port;
  await server.stop();
  assert.equal(await server.start({ timeoutMs: 5000, preferredPort: port }), first);
  const secondIdentity = await (await fetch(`${first}api/desktop/ready`)).json();
  assert.notEqual(secondIdentity.nonce, firstIdentity.nonce);
  assert.equal(secondIdentity.pid, server.child.pid);
});

test('desktop owns one backend worker despite inherited WEB_CONCURRENCY', { skip: process.platform === 'win32' }, async (t) => {
  const previous = process.env.WEB_CONCURRENCY;
  process.env.WEB_CONCURRENCY = '2';
  t.after(() => { if (previous === undefined) delete process.env.WEB_CONCURRENCY; else process.env.WEB_CONCURRENCY = previous; });
  const server = await serverFixture(t);
  await server.start({ timeoutMs: 1000 });
  assert.equal((await (await fetch(`${server.url}api/desktop/ready`)).json()).pid, server.child.pid);
});

test('desktop disables inherited Uvicorn reload mode for backend process identity', { skip: process.platform === 'win32' }, async (t) => {
  const previous = process.env.UVICORN_RELOAD;
  process.env.UVICORN_RELOAD = 'true';
  t.after(() => { if (previous === undefined) delete process.env.UVICORN_RELOAD; else process.env.UVICORN_RELOAD = previous; });
  const server = await serverFixture(t);
  await server.start({ timeoutMs: 1000 });
  assert.equal((await (await fetch(`${server.url}api/desktop/ready`)).json()).pid, server.child.pid);
});

test('a foreign service capturing the released port cannot satisfy startup readiness', { skip: process.platform === 'win32' }, async (t) => {
  const server = await serverFixture(t, { delayMs: 800 });
  const original = net.createServer;
  let foreign;
  const paths = [];
  t.after(() => { net.createServer = original; foreign?.closeAllConnections(); foreign?.close(); });
  net.createServer = (...args) => {
    const probe = original(...args);
    const close = probe.close.bind(probe);
    probe.close = callback => {
      const port = probe.address().port;
      net.createServer = original;
      return close(() => {
        foreign = http.createServer((request, response) => {
          paths.push(request.url);
          response.end(JSON.stringify({ ...ready, nonce: 'b'.repeat(64) }));
        });
        foreign.listen(port, '127.0.0.1', callback);
      });
    };
    return probe;
  };
  await assert.rejects(server.start({ timeoutMs: 1500 }), /backend exited|did not answer/);
  assert.equal(server.url, null);
  assert.equal(server.child, null);
  assert.ok(paths.includes('/api/desktop/ready'));
  assert.ok(paths.every(requestPath => requestPath === '/api/desktop/ready'), 'foreign services receive no shutdown mutation');
  assert.ok(paths.every(requestPath => !requestPath.includes('nonce')), 'nonce is never sent to an unverified server');
});
