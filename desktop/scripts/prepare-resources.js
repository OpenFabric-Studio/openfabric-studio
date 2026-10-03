'use strict';
// Copies what the installed app needs next to its executable: the backend sources, the built frontend and the
// engine patches. Run by `npm run dist`. Never ships anything from a developer's machine (.env, database, venv).
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const root = path.resolve(__dirname, '..', '..');
const out = path.resolve(__dirname, '..', 'resources');
const skipFrontendBuild = process.argv.includes('--skip-frontend-build');

if (!skipFrontendBuild) {
  // vue-tsc + vite build; needs `npm ci` in frontend/ first.
  const [cmd, args] = process.platform === 'win32'
    ? [process.env.ComSpec || 'cmd.exe', ['/d', '/s', '/c', 'npm run build']]
    : ['npm', ['run', 'build']];
  execFileSync(cmd, args, { cwd: path.join(root, 'frontend'), stdio: 'inherit' });
}

fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(out, { recursive: true });

// Top-level backend entries that are local state, not source.
const LOCAL_ONLY = new Set(['.venv', '.env', '.env.setup', 'data', 'logs', 'run.bat', 'run.sh']);
const backendSrc = path.join(root, 'backend');
fs.cpSync(backendSrc, path.join(out, 'backend'), {
  recursive: true,
  filter: (src) => {
    const rel = path.relative(backendSrc, src);
    if (!rel) return true;
    if (LOCAL_ONLY.has(rel.split(path.sep)[0]) || rel.split(path.sep)[0].startsWith('.env.setup.')) return false;
    return !rel.split(path.sep).includes('__pycache__') && !rel.endsWith('.pyc');
  },
});
// The backend wizard and desktop share one reviewed pin catalog.
fs.mkdirSync(path.join(out, 'backend', 'app'), { recursive: true });
fs.copyFileSync(path.join(root, 'desktop', 'manifest.json'), path.join(out, 'backend', 'app', 'module_assets.json'));

const dist = path.join(root, 'frontend', 'dist');
if (!fs.existsSync(path.join(dist, 'index.html'))) throw new Error('frontend/dist is missing: build the frontend first');
fs.cpSync(dist, path.join(out, 'frontend', 'dist'), { recursive: true });

fs.mkdirSync(path.join(out, 'patches'), { recursive: true });
fs.copyFileSync(path.join(root, 'external', 'patches', 'ace-step.patch'), path.join(out, 'patches', 'ace-step.patch'));
fs.copyFileSync(path.join(root, 'external', 'patches', 'yue-model-resume.patch'), path.join(out, 'patches', 'yue-model-resume.patch'));

// The optional native build verifier resolves these relative to backend/scripts,
// so installed resources retain the source checkout's external/patches layout.
const nativePatches = path.join(out, 'external', 'patches');
fs.mkdirSync(nativePatches, { recursive: true });
for (const name of ['yue-workspace-release.patch', 'yue-progress.patch', 'README.md']) {
  fs.copyFileSync(path.join(root, 'external', 'patches', name), path.join(nativePatches, name));
}

// Guard: fail the build rather than ship personal data.
const forbidden = [];
(function scan(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) { if (['.venv', '__pycache__'].includes(e.name)) forbidden.push(p); else scan(p); }
    else if (e.name === '.env' || /\.(db|sqlite3?)$/i.test(e.name)) forbidden.push(p);
  }
})(out);
if (forbidden.length) throw new Error(`refusing to package local files:\n${forbidden.join('\n')}`);

console.log(`resources ready in ${out}`);
