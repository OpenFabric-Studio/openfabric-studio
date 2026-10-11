'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const path = require('node:path');
const { createRequire } = require('node:module');
const { spawn } = require('node:child_process');

test('the builder Electron downloader still proxies HTTP and honours NO_PROXY', { timeout: 10000 }, async (t) => {
  const received = [];
  const proxy = http.createServer((request, response) => {
    received.push(request.url);
    response.end('via proxy');
  });
  const direct = http.createServer((_request, response) => response.end('direct'));
  t.after(() => Promise.all([proxy, direct].map(server => new Promise(resolve => {
    server.closeAllConnections();
    server.close(resolve);
  }))));
  await Promise.all([proxy, direct].map(server => new Promise(resolve => server.listen(0, '127.0.0.1', resolve))));
  const proxyAddress = proxy.address(), directAddress = direct.address();
  if (!proxyAddress || typeof proxyAddress === 'string' || !directAddress || typeof directAddress === 'string') throw new Error('Missing fixture listeners');
  const builderRequire = createRequire(require.resolve('app-builder-lib/package.json'));
  const getEntry = builderRequire.resolve('@electron/get');
  const script = `
    const assert = require('node:assert/strict');
    const http = require('node:http');
    require(process.argv[1]).initializeProxy();
    function read(url) { return new Promise((resolve, reject) => {
      http.get(url, response => { let body = ''; response.on('data', chunk => body += chunk); response.on('end', () => resolve(body)); }).on('error', reject);
    }); }
    (async () => {
      assert.equal(await read('http://fixture.invalid/asset'), 'via proxy');
      assert.equal(await read(process.argv[2]), 'direct');
    })().catch(error => { console.error(error.message); process.exitCode = 1; });
  `;
  const child = spawn(process.execPath, ['-e', script, getEntry, `http://127.0.0.1:${directAddress.port}/asset`], {
    cwd: path.resolve(__dirname, '..'),
    env: { ...process.env, GLOBAL_AGENT_HTTP_PROXY: `http://127.0.0.1:${proxyAddress.port}`, GLOBAL_AGENT_HTTPS_PROXY: '', GLOBAL_AGENT_NO_PROXY: '127.0.0.1', GLOBAL_AGENT_ENVIRONMENT_VARIABLE_NAMESPACE: 'GLOBAL_AGENT_', ELECTRON_GET_USE_PROXY: '', ROARR_LOG: 'false' },
    stdio: ['ignore', 'ignore', 'pipe'],
  });
  t.after(() => { if (child.exitCode === null) child.kill(); });
  let errors = '';
  child.stderr.on('data', chunk => { errors += chunk; });
  const code = await new Promise((resolve, reject) => { child.once('error', reject); child.once('exit', resolve); });
  assert.equal(code, 0, errors);
  assert.deepEqual(received, ['http://fixture.invalid/asset']);
});
