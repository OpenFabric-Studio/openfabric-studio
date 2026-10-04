'use strict';
const fs = require('node:fs');
const path = require('node:path');
const { runCommand } = require('../src/proc');
const { sourceLayout } = require('./launch-source');

function setupCommand(root, feature, args, env = process.env, platform = process.platform) {
  if (!['speech', 'singing', 'video', 'kokoro', 'chatterbox', 'wan22', 'rvc'].includes(feature)) throw new Error('Unsupported setup feature');
  const python = env.PYTHON_BIN || sourceLayout(root, platform).python;
  if (!env.PYTHON_BIN && !fs.existsSync(python)) throw new Error('Prepare the backend first with prod_run.sh / prod_run.bat, or select a configured Python using PYTHON_BIN. Optional engines can also be installed in Settings.');
  const script = feature === 'video' ? 'setup_video.py' : 'setup_modules.py';
  return { command: python, args: [path.join(root, 'backend', 'scripts', script), ...(feature === 'video' ? [] : ['--feature', feature, '--install']), ...args] };
}

async function main() {
  const root = path.resolve(__dirname, '../..');
  const [feature, ...args] = process.argv.slice(2);
  const command = setupCommand(root, feature, args);
  const abort = new AbortController();
  const cancel = () => abort.abort(new Error('setup cancelled'));
  process.on('SIGINT', cancel);
  process.on('SIGTERM', cancel);
  try { await runCommand(command.command, command.args, { cwd: root, env: process.env, signal: abort.signal, onLine: console.log }); }
  finally { process.removeListener('SIGINT', cancel); process.removeListener('SIGTERM', cancel); }
}

if (require.main === module) main().catch(error => { console.error(error.message); process.exitCode = 1; });
module.exports = { setupCommand };
