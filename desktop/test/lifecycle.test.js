'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { drainApplication } = require('../src/lifecycle');
const { setupCommand } = require('../scripts/setup-feature');

test('shutdown awaits aborted setup cleanup before stopping the backend', async () => {
  const abort = new AbortController();
  const order = [];
  let release;
  const setupTask = new Promise(resolve => { release = resolve; });
  const draining = drainApplication({ setupAbort: abort, setupTask, server: { stop: async () => order.push('backend stopped') } });
  assert.equal(abort.signal.aborted, true);
  assert.deepEqual(order, []);
  order.push('setup cleaned');
  release();
  await draining;
  assert.deepEqual(order, ['setup cleaned', 'backend stopped']);
});

test('fixed optional setup wrappers preserve operator flags and the video CLI contract', () => {
  const singing = setupCommand('/repo', 'singing', ['--root', '/selected', '--download-models'], { PYTHON_BIN: '/python' }, 'win32');
  assert.equal(singing.command, '/python');
  assert.deepEqual(singing.args.slice(1), ['--feature', 'singing', '--install', '--root', '/selected', '--download-models']);
  const video = setupCommand('/repo', 'video', ['--preflight', '--pack', 'ltx25'], { PYTHON_BIN: '/python' });
  assert.match(video.args[0], /setup_video\.py$/);
  assert.deepEqual(video.args.slice(1), ['--preflight', '--pack', 'ltx25']);
  assert.throws(() => setupCommand('/repo', 'arbitrary-command', [], { PYTHON_BIN: '/python' }), /Unsupported/);
  const kokoro = setupCommand('/repo', 'kokoro', [], { PYTHON_BIN: '/python' });
  assert.match(kokoro.args[0], /setup_modules\.py$/);
  assert.deepEqual(kokoro.args.slice(1), ['--feature', 'kokoro', '--install']);
  const wan = setupCommand('/repo', 'wan22', ['--download-models'], { PYTHON_BIN: '/python' });
  assert.deepEqual(wan.args.slice(1), ['--feature', 'wan22', '--install', '--download-models']);
});
