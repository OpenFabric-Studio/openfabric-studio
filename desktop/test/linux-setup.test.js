'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync, spawnSync } = require('node:child_process');

function setupFixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'remiqora-linux-setup-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.copyFileSync(path.join(__dirname, '..', '..', 'setup_linux.sh'), path.join(root, 'setup_linux.sh'));
  fs.mkdirSync(path.join(root, 'backend'));
  fs.mkdirSync(path.join(root, 'external', 'patches'), { recursive: true });
  const tools = path.join(root, 'fake-tools');
  fs.mkdirSync(tools);
  const command = (name, body) => {
    const target = path.join(tools, name);
    fs.writeFileSync(target, `#!/usr/bin/env bash\nset -eu\n${body}\n`, { mode: 0o755 });
    return target;
  };
  command('uname', 'echo Linux');
  command('git', `if [[ "$1" == clone ]]; then
  mkdir -p "$3/.git" "$3/scripts"
  printf '#!/usr/bin/env bash\\nexit 0\\n' > "$3/scripts/build_linux.sh"
  echo 'fake git clone diagnostic'
elif [[ "$3" == apply ]]; then
  if [[ "$4" == --check ]]; then exit 1; fi
  if [[ "$4" == --reverse ]]; then exit 0; fi
else echo 'fake git checkout diagnostic'; fi`);
  const realPython = execFileSync('python3', ['-c', 'import sys;print(sys.executable)'], { encoding: 'utf8' }).trim();
  const python = command('python', `if [[ "\${1:-}" == tools/model_manager_v2.py ]]; then exit 0; fi\nexec '${realPython.replace(/'/g, "'\\''")}' "$@"`);
  for (const name of ['uv', 'node', 'npm', 'cmake', 'ffmpeg', 'nvcc']) command(name, 'exit 0');
  const cuda = path.join(root, 'cuda');
  fs.mkdirSync(path.join(cuda, 'include'), { recursive: true });
  for (const name of ['cublas_v2.h', 'cufft.h']) fs.writeFileSync(path.join(cuda, 'include', name), 'fake header');
  const run = () => spawnSync('bash', [path.join(root, 'setup_linux.sh')], {
    env: { ...process.env, PATH: `${tools}${path.delimiter}${process.env.PATH}`, PYTHON_BIN: python, UV_BIN: path.join(tools, 'uv'), NODE_BIN: path.join(tools, 'node'), NPM_BIN: path.join(tools, 'npm'), CMAKE_BIN: path.join(tools, 'cmake'), NVCC_BIN: path.join(tools, 'nvcc'), CUDA_TOOLKIT_PREFIX: cuda, CUDA_LIB_DIR: path.join(cuda, 'lib') },
    encoding: 'utf8', timeout: 10000,
  });
  return { root, run };
}

test('Linux setup keeps patch and Git diagnostics out of captured engine paths', { skip: process.platform === 'win32' }, (t) => {
  const { root, run } = setupFixture(t);
  const result = run();
  assert.equal(result.status, 0, result.stderr);
  const env = fs.readFileSync(path.join(root, 'backend', '.env'), 'utf8');
  assert.ok(env.includes(`ACE_STEP_DIR=${root}/external/ACE-Step-1.5\n`));
  assert.doesNotMatch(env, /diagnostic|already applied/);
});

test('Linux setup reruns retain the complete private environment and stage defaults atomically', { skip: process.platform === 'win32' }, (t) => {
  const { root, run } = setupFixture(t);
  const original = '# private settings\nSECRET_TOKEN=private-value\nACE_STEP_DEVICE=cpu\nCUSTOM_PATH=/private/location\n';
  fs.writeFileSync(path.join(root, 'backend', '.env'), original, { mode: 0o600 });
  const result = run();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.readFileSync(path.join(root, 'backend', '.env'), 'utf8'), original);
  const proposed = fs.readFileSync(path.join(root, 'backend', '.env.setup'), 'utf8');
  assert.ok(proposed.includes(`ACE_STEP_DIR=${root}/external/ACE-Step-1.5\n`));
  assert.doesNotMatch(proposed, /SECRET_TOKEN/);
  assert.deepEqual(fs.readdirSync(path.join(root, 'backend')).sort(), ['.env', '.env.setup']);
});
