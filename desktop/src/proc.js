'use strict';
const { spawn, execFile } = require('node:child_process');
const readline = require('node:readline');
const fs = require('node:fs');
const path = require('node:path');
const { IS_WINDOWS } = require('./paths');
const treeDrains = new WeakMap();

/** spawn() that puts the child in its own process group on POSIX, so killTree() can reach everything it starts. */
function spawnTree(cmd, args, options = {}) {
  const child = spawn(cmd, args, { windowsHide: true, detached: !IS_WINDOWS, ...options });
  // Drain while group identity is still owned; later stop calls reuse this task
  // instead of sending signals to a numeric PID that might have been recycled.
  // `close` also waits for inherited pipes held by descendants. Begin draining
  // as soon as the leader exits, so those pipes cannot keep ownership stuck.
  if (!IS_WINDOWS) child.once('exit', () => { void killTree(child).catch(() => {}); });
  return child;
}

/** Kills a process and its descendants (the backend starts uv, which starts Python, which starts the model server). */
function killTree(child, options = {}) {
  if (!child || child.pid === undefined) return Promise.resolve();
  const existing = treeDrains.get(child);
  if (existing) return existing;
  const task = terminateTree(child, options);
  treeDrains.set(child, task);
  return task;
}

async function terminateTree(child, { graceMs = 1500 } = {}) {
  const exited = child.exitCode !== null || child.signalCode !== null;
  const closed = exited ? Promise.resolve() : new Promise(resolve => child.once('close', resolve));
  if (IS_WINDOWS) {
    if (!exited) await new Promise(resolve => execFile('taskkill', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, timeout: 10000 }, resolve));
    await closed;
    return;
  }
  const groupAlive = () => { try { process.kill(-child.pid, 0); return true; } catch { return false; } };
  const send = signal => { try { process.kill(-child.pid, signal); } catch { if (!exited) { try { child.kill(signal); } catch {} } } };
  send('SIGTERM');
  const deadline = Date.now() + graceMs;
  while (groupAlive() && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 25));
  // The leader may exit before its descendants. Always inspect the group.
  if (groupAlive()) send('SIGKILL');
  await closed;
}

/** Environment for child processes: the app's own, minus the switch that would turn Electron binaries into plain Node. */
function cleanEnv(extra = {}) {
  const env = { ...process.env, ...extra };
  delete env.ELECTRON_RUN_AS_NODE;
  return env;
}

/**
 * Runs a command to completion, streaming output lines to `onLine` and an optional log file.
 * Rejects with the last lines of output on a non-zero exit; an abort kills the whole tree.
 */
function runCommand(cmd, args, { cwd, env, onLine = () => {}, signal, logFile, timeoutMs } = {}) {
  if (signal?.aborted) return Promise.reject(signal.reason);
  return new Promise((resolve, reject) => {
    // Open before spawning: an inaccessible log must not orphan work.
    const log = logFile ? fs.createWriteStream(logFile, { fd: fs.openSync(logFile, 'a') }) : null;
    const child = spawnTree(cmd, args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'] });
    const tail = [];
    log?.write(`\n$ ${cmd} ${args.join(' ')}\n`);

    const handle = (line) => {
      tail.push(line);
      if (tail.length > 30) tail.shift();
      log?.write(line + '\n');
      onLine(line);
    };
    readline.createInterface({ input: child.stdout }).on('line', handle);
    readline.createInterface({ input: child.stderr }).on('line', handle);

    let termination = null;
    let timeoutError = null;
    let logError = null;
    const onAbort = () => { termination ||= killTree(child); };
    log?.on('error', error => { logError = error; onAbort(); });
    signal?.addEventListener('abort', onAbort, { once: true });
    if (signal?.aborted) onAbort();
    const timer = timeoutMs === undefined ? null : setTimeout(() => {
      timeoutError = new Error(`${path.basename(cmd)} timed out after ${timeoutMs} ms`);
      onAbort();
    }, timeoutMs);
    const cleanup = () => { if (timer !== null) clearTimeout(timer); signal?.removeEventListener('abort', onAbort); log?.end(); };

    child.on('error', (err) => { cleanup(); reject(err); });
    child.on('close', async (code) => {
      cleanup();
      try { await (termination || killTree(child)); }
      catch (error) { return reject(error); }
      if (signal?.aborted) return reject(signal.reason);
      if (timeoutError) return reject(timeoutError);
      if (logError) return reject(logError);
      if (code === 0) return resolve();
      reject(new Error(`${path.basename(cmd)} ${args[0] ?? ''} exited with code ${code}\n${tail.slice(-12).join('\n')}`));
    });
  });
}

module.exports = { spawnTree, killTree, cleanEnv, runCommand };
