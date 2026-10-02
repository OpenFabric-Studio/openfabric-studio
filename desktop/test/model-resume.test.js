'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { promisify } = require('node:util');
const { execFile } = require('node:child_process');
const { applyGitPatch } = require('../src/bootstrap/patch');

test('pinned YuE model manager resumes verified content and serializes recovery and cleanup', async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'remiqora-model-resume-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, 'tools'));
  await fs.copyFile(path.join(__dirname, 'fixtures', 'model_manager_v2.py'), path.join(root, 'tools', 'model_manager_v2.py'));
  const patch = await fs.readFile(path.join(__dirname, '..', '..', 'external', 'patches', 'yue-model-resume.patch'), 'utf8').catch(() => '');
  if (patch) await applyGitPatch(patch, root);
  try {
    const result = await promisify(execFile)(process.env.PYTHON_BIN || (process.platform === 'win32' ? 'python' : 'python3'), [path.join(__dirname, 'fixtures', 'test_model_resume.py'), root], { timeout: 30000,
      env: { ...process.env, HOME: root, USERPROFILE: root, HF_HOME: path.join(root, 'hf-cache'), HF_TOKEN: '', HUGGING_FACE_HUB_TOKEN: '', AUDIOCPP_MS_TOKEN: '' },
    });
    assert.match(result.stderr, /OK/);
  } catch (error) { assert.fail(`${error.stdout || ''}\n${error.stderr || error.message}`); }
});
