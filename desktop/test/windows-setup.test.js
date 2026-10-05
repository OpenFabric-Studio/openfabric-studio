'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

test('Windows legacy setup writes explicit quoted engine paths and preserves an existing environment', { skip: process.platform !== 'win32' }, async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'openfabric-windows-env-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const script = path.resolve(__dirname, '../../setup_models.ps1');
  const harness = path.join(root, 'verify.ps1');
  await fs.writeFile(harness, `
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($env:SETUP_SCRIPT, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Setup script has parser errors' }
$function = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Write-LegacyEnvironment' }, $true)
if (-not $function) { throw 'Explicit environment writer is missing' }
Invoke-Expression $function.Extent.Text
$file = Join-Path $env:TEST_ROOT '.env'
$values = [ordered]@{ ACE_STEP_DIR = 'C:\\Engines\\O''Neil & studio'; YUE2_DIR = 'D:\\Models\\YuE'; DEMUCS_DIR = 'D:\\Models\\Demucs'; FFMPEG_BIN_DIR = 'C:\\Media tools\\bin' }
if (-not (Write-LegacyEnvironment -FilePath $file -Values $values)) { throw 'Fresh configuration was not created' }
Copy-Item -LiteralPath $file -Destination (Join-Path $env:TEST_ROOT 'generated.txt')
$private = '# private settings' + [Environment]::NewLine + 'PRIVATE_FIXTURE_TOKEN=keep'
[IO.File]::WriteAllText($file, $private)
if (Write-LegacyEnvironment -FilePath $file -Values $values) { throw 'Existing configuration was overwritten' }
if ([IO.File]::ReadAllText($file) -ne $private) { throw 'Existing bytes changed' }
`);
  execFileSync(process.env.POWERSHELL_BIN || 'powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', harness], { env: { ...process.env, SETUP_SCRIPT: script, TEST_ROOT: root }, stdio: 'pipe' });
  const generated = await fs.readFile(path.join(root, 'generated.txt'), 'utf8');
  const values = Object.fromEntries(generated.split(/\r?\n/).filter(line => !line.startsWith('#') && line).map(line => {
    const match = /^([A-Z_]+)='(.*)'$/.exec(line);
    assert.ok(match, line);
    return [match[1], match[2].replace(/\\([\\'])/g, '$1')];
  }));
  assert.deepEqual(values, { ACE_STEP_DIR: "C:\\Engines\\O'Neil & studio", YUE2_DIR: 'D:\\Models\\YuE', DEMUCS_DIR: 'D:\\Models\\Demucs', FFMPEG_BIN_DIR: 'C:\\Media tools\\bin' });
});
