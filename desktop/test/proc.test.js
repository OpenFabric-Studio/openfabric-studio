'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { runCommand, spawnTree, killTree } = require('../src/proc');

test('an already cancelled command never starts a process', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'openfabric-proc-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const marker = path.join(root, 'started');
  const abort = new AbortController();
  abort.abort(new Error('cancelled before spawn'));
  await assert.rejects(runCommand(process.execPath, ['-e', 'require("fs").writeFileSync(process.argv[1],"started")', marker], { signal: abort.signal }), /cancelled before spawn/);
  assert.equal(fs.existsSync(marker), false);
});

test('command timeout terminates a child that ignores SIGTERM', { timeout: 7000 }, async () => {
  const started = Date.now();
  await assert.rejects(runCommand(process.execPath, ['-e', 'process.on("SIGTERM",()=>{}); setInterval(()=>{},1000)'], { timeoutMs: 100 }), /timed out/);
  assert.ok(Date.now() - started < 5000);
});

test('tree termination awaits and escalates for a SIGTERM-resistant descendant', { skip: process.platform === 'win32', timeout: 7000 }, async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'openfabric-tree-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const heartbeat = path.join(root, 'heartbeat');
  const child = spawnTree(process.execPath, ['-e', 'const c=require("child_process").spawn(process.execPath,["-e",`process.on("SIGTERM",()=>{});setInterval(()=>require("fs").appendFileSync(process.argv[1],"."),30)`,process.argv[1]],{stdio:"ignore"});process.on("SIGTERM",()=>{});setInterval(()=>{},1000)', heartbeat], { stdio: 'ignore' });
  t.after(() => { try { process.kill(-child.pid, 'SIGKILL'); } catch {} });
  for (let n = 0; n < 100 && !fs.existsSync(heartbeat); n++) await new Promise(r => setTimeout(r, 20));
  assert.ok(fs.existsSync(heartbeat), 'descendant has started');
  await killTree(child, { graceMs: 100 });
  assert.notEqual(child.exitCode ?? child.signalCode, null, 'leader exit was awaited');
  const stopped = fs.readFileSync(heartbeat, 'utf8');
  await new Promise(r => setTimeout(r, 150));
  assert.equal(fs.readFileSync(heartbeat, 'utf8'), stopped, 'descendant no longer writes');
});

test('an unwritable log prevents the command from starting', async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'openfabric-log-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const marker = path.join(root, 'started');
  await assert.rejects(runCommand(process.execPath, ['-e', 'require("fs").writeFileSync(process.argv[1],"started")', marker], { logFile: root }), /EISDIR|directory|permission/i);
  assert.equal(fs.existsSync(marker), false);
});

test('a leader exiting drains its owned descendants before a later stop can target a reused PID', { skip: process.platform === 'win32', timeout: 7000 }, async (t) => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'openfabric-exit-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const heartbeat = path.join(root, 'heartbeat');
  const child = spawnTree(process.execPath, ['-e', 'require("child_process").spawn(process.execPath,["-e",`process.on("SIGTERM",()=>{});setInterval(()=>require("fs").appendFileSync(process.argv[1],"."),30)`,process.argv[1]],{stdio:"ignore"});setTimeout(()=>process.exit(0),150)', heartbeat], { stdio: 'ignore' });
  t.after(() => { try { process.kill(-child.pid, 'SIGKILL'); } catch {} });
  await new Promise(resolve => child.once('close', resolve));
  await new Promise(resolve => setTimeout(resolve, 1900));
  const stopped = fs.readFileSync(heartbeat, 'utf8');
  await new Promise(resolve => setTimeout(resolve, 100));
  assert.equal(fs.readFileSync(heartbeat, 'utf8'), stopped, 'leader exit did not abandon its group');
  await killTree(child);
});
