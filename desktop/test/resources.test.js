'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

async function assertDirectoryCopy(source, destination) {
  const entries = await fs.readdir(source, { withFileTypes: true });
  assert.deepEqual((await fs.readdir(destination)).sort(), entries.map(entry => entry.name).sort());
  for (const entry of entries) {
    const sourcePath = path.join(source, entry.name);
    const destinationPath = path.join(destination, entry.name);
    if (entry.isDirectory()) await assertDirectoryCopy(sourcePath, destinationPath);
    else assert.deepEqual(await fs.readFile(destinationPath), await fs.readFile(sourcePath));
  }
}

test('prepared desktop includes native helper patches at their runtime paths', async (t) => {
  const fixture = await fs.mkdtemp(path.join(os.tmpdir(), 'openfabric-packaging-'));
  t.after(() => fs.rm(fixture, { recursive: true, force: true }));
  const repo = path.resolve(__dirname, '..', '..');
  const script = path.join(fixture, 'desktop', 'scripts', 'prepare-resources.js');
  await fs.mkdir(path.dirname(script), { recursive: true });
  await fs.copyFile(path.join(repo, 'desktop', 'scripts', 'prepare-resources.js'), script);
  await fs.mkdir(path.join(fixture, 'frontend', 'dist'), { recursive: true });
  await fs.writeFile(path.join(fixture, 'frontend', 'dist', 'index.html'), '<html></html>');
  await fs.mkdir(path.join(fixture, 'backend', 'scripts'), { recursive: true });
  await fs.copyFile(path.join(repo, 'backend', 'scripts', 'setup_yue_native.py'), path.join(fixture, 'backend', 'scripts', 'setup_yue_native.py'));
  const starterAssets = path.join(repo, 'backend', 'assets', 'starter-voices');
  await fs.cp(starterAssets, path.join(fixture, 'backend', 'assets', 'starter-voices'), { recursive: true });
  const patches = ['ace-step.patch', 'yue-model-resume.patch', 'yue-workspace-release.patch', 'yue-progress.patch'];
  await fs.mkdir(path.join(fixture, 'external', 'patches'), { recursive: true });
  for (const name of [...patches, 'README.md']) {
    await fs.copyFile(path.join(repo, 'external', 'patches', name), path.join(fixture, 'external', 'patches', name));
  }
  execFileSync(process.execPath, [script, '--skip-frontend-build'], { stdio: 'pipe' });
  const resources = path.join(fixture, 'desktop', 'resources');
  const packagedStarters = path.join(resources, 'backend', 'assets', 'starter-voices');
  await assertDirectoryCopy(starterAssets, packagedStarters);
  const helper = await fs.readFile(path.join(resources, 'backend', 'scripts', 'setup_yue_native.py'), 'utf8');
  assert.match(helper, /PATCH_DIR = Path\(__file__\)\.resolve\(\)\.parents\[2\] \/ 'external\/patches'/);
  for (const name of ['yue-workspace-release.patch', 'yue-progress.patch']) {
    const packed = await fs.readFile(path.join(resources, 'external', 'patches', name));
    assert.deepEqual(packed, await fs.readFile(path.join(repo, 'external', 'patches', name)));
  }
  const builder = await fs.readFile(path.join(repo, 'desktop', 'electron-builder.yml'), 'utf8');
  assert.match(builder, /from: resources\/external\s+to: external/);
});
