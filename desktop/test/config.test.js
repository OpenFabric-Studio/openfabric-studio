'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { loadConfig, updateConfig } = require('../src/config');

test('concurrent preference updates retain both the selected root and backend port', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'openfabric-config-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await Promise.all([updateConfig(root, { dataRoot: '/selected/root' }), updateConfig(root, { port: 9123 })]);
  assert.deepEqual(await loadConfig(root), { dataRoot: '/selected/root', port: 9123 });
  assert.deepEqual(await fs.readdir(root), ['config.json']);
});

test('invalid saved paths and ports cannot reach server startup', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'openfabric-config-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.writeFile(path.join(root, 'config.json'), JSON.stringify({ dataRoot: 9, port: 'socket-path' }));
  assert.deepEqual(await loadConfig(root), {});
});
