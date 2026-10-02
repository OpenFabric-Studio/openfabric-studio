'use strict';
const { app, BrowserWindow, ipcMain, dialog, shell, Menu, Notification } = require('electron');
const path = require('node:path');
const { PLATFORM, resourcePaths, defaultDataRoot, layout } = require('./paths');
const manifest = require('../manifest.json');
const { runChecks } = require('./bootstrap/checks');
const { describePlan, runSetup, isSetupComplete } = require('./bootstrap/run');
const { BackendServer } = require('./server');
const { loadConfig, updateConfig, ensureWritableDir } = require('./config');
const { notificationHandlers } = require('./notifications');
const { versionedDocumentUrl } = require('./navigation');

// Only these links can be opened from the first-run screen.
const EXTERNAL = {
  'nvidia-drivers': 'https://www.nvidia.com/drivers',
  issues: 'https://github.com/OpenFabric-Studio/openfabric-studio/issues/new/choose',
};
const SETUP_PAGE = path.join(__dirname, '..', 'renderer', 'index.html');

// Tests point the app at throwaway folders.
if (process.env.OPENFABRIC_USER_DATA) app.setPath('userData', process.env.OPENFABRIC_USER_DATA);

let win = null;
let ctx = null;
let server = null;
let setupAbort = null;
let quitting = false;
let savedPort = null;

function buildContext(dataRoot) {
  return {
    L: layout(dataRoot, PLATFORM, manifest),
    manifest,
    platform: PLATFORM,
    resources: resourcePaths(app.isPackaged),
    // Test switch: skip the multi-gigabyte components, e.g. OPENFABRIC_SKIP_COMPONENTS=ace-step,demucs,weights
    skip: (process.env.OPENFABRIC_SKIP_COMPONENTS || '').split(',').map((s) => s.trim()).filter(Boolean),
  };
}

const send = (payload) => { if (win && !win.isDestroyed()) win.webContents.send('setup:event', payload); };

function createWindow() {
  win = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 900,
    minHeight: 620,
    show: false,
    title: 'OpenFabric Studio',
    backgroundColor: '#0f0f14',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, sandbox: true, nodeIntegration: false },
  });
  win.once('ready-to-show', () => win.show());
  win.on('closed', () => { win = null; });

  // External links open in the system browser; the window only ever shows the setup page or our own backend.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (event, url) => {
    const own = url.startsWith('file:') || (server && server.url && url.startsWith(server.url));
    if (!own) {
      event.preventDefault();
      if (/^https?:\/\//.test(url)) shell.openExternal(url);
    }
  });
  if (!app.isPackaged && process.env.OPENFABRIC_DEVTOOLS) win.webContents.openDevTools({ mode: 'detach' });
}

function showSetup(query = {}) {
  return win.loadFile(SETUP_PAGE, { query });
}

/** Starts the backend, then swaps the window over to the real app. */
async function launch() {
  send({ type: 'starting' });
  server = new BackendServer(ctx);
  server.on('exit', (code) => {
    if (quitting) return;
    showSetup({ state: 'crashed', message: `The backend stopped (exit code ${code}).` });
  });
  try {
    const url = await server.start({ preferredPort: savedPort });
    if (server.port !== savedPort) {
      savedPort = server.port;
      await updateConfig(app.getPath('userData'), { port: savedPort });
    }
    await win.loadURL(versionedDocumentUrl(url, app.getVersion()));
  } catch (err) {
    showSetup({ state: 'crashed', message: err.message });
  }
}

async function startSetup() {
  if (setupAbort) return;
  setupAbort = new AbortController();
  const run = { ...ctx, signal: setupAbort.signal };
  try {
    await runSetup(run, send);
    send({ type: 'finished', complete: await isSetupComplete(ctx) });
  } catch (err) {
    if (setupAbort.signal.aborted) send({ type: 'paused' });
    else send({ type: 'failed', componentId: err.componentId || null, message: err.message });
  } finally {
    setupAbort = null;
  }
}

function registerIpc() {
  const notifications = notificationHandlers({ Notification, getWindow: () => win, getOrigin: () => server?.url });
  ipcMain.handle('notifications:capability', (event) => notifications.capability(event));
  ipcMain.handle('notifications:notify', (event, payload) => notifications.notify(event, payload));
  ipcMain.handle('setup:context', async () => {
    const plan = await describePlan(ctx);
    return {
      version: app.getVersion(),
      platform: PLATFORM,
      // Every preferred language, like the app itself: Russian anywhere in the list selects Russian.
      // OPENFABRIC_LANG=en forces English UI labels in the bootstrap shell when set.
      languages: process.env.OPENFABRIC_LANG ? [process.env.OPENFABRIC_LANG] : [app.getLocale(), ...app.getPreferredSystemLanguages()],
      dataRoot: ctx.L.root,
      plan,
      totalBytes: plan.filter((c) => !c.done && !c.skipped).reduce((sum, c) => sum + c.weight, 0),
    };
  });
  ipcMain.handle('setup:checks', async (_e, dataRoot) => {
    const root = dataRoot || ctx.L.root;
    return runChecks({ platform: PLATFORM, dataRoot: root, manifest, plan: await describePlan(buildContext(root)) });
  });
  ipcMain.handle('setup:choose-folder', async () => {
    const r = await dialog.showOpenDialog(win, { properties: ['openDirectory', 'createDirectory'] });
    if (r.canceled || !r.filePaths[0]) return null;
    const picked = r.filePaths[0];
    return ['remiqora', 'openfabric', 'openfabricstudio', 'openfabric-studio'].includes(path.basename(picked).toLowerCase().replace(/[\s_]/g, '')) ? picked : path.join(picked, 'OpenFabricStudio');
  });
  ipcMain.handle('setup:set-root', async (_e, dir) => {
    try {
      await ensureWritableDir(dir);
    } catch (err) {
      return { ok: false, message: err.message };
    }
    ctx = buildContext(dir);
    await updateConfig(app.getPath('userData'), { dataRoot: dir });
    return { ok: true };
  });
  ipcMain.handle('setup:start', async () => {
    // Remember the chosen folder right away: an interrupted setup resumes there on the next start.
    await ensureWritableDir(ctx.L.root);
    await updateConfig(app.getPath('userData'), { dataRoot: ctx.L.root });
    startSetup();
  });
  ipcMain.handle('setup:pause', () => { if (setupAbort) setupAbort.abort(new Error('paused')); });
  ipcMain.handle('setup:launch', () => launch());
  ipcMain.handle('setup:open-external', (_e, key) => { if (EXTERNAL[key]) shell.openExternal(EXTERNAL[key]); });
  ipcMain.handle('setup:open-logs', () => shell.openPath(ctx.L.logs));
  ipcMain.handle('setup:show-data', () => shell.openPath(ctx.L.root));
}

async function boot() {
  const config = await loadConfig(app.getPath('userData'));
  savedPort = config.port || null;
  ctx = buildContext(config.dataRoot || defaultDataRoot());
  registerIpc();
  Menu.setApplicationMenu(process.platform === 'darwin'
    ? Menu.buildFromTemplate([{ role: 'appMenu' }, { role: 'editMenu' }, { role: 'viewMenu' }, { role: 'windowMenu' }])
    : null);
  createWindow();
  // A saved data root means the user already went through the first run; go straight in when nothing is missing.
  if (config.dataRoot && (await isSetupComplete(ctx, { ignoreSkipped: true }))) await launch();
  else await showSetup();
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (!win) return;
    if (win.isMinimized()) win.restore();
    win.focus();
  });
  app.whenReady().then(boot).catch((err) => { dialog.showErrorBox('OpenFabric Studio', err.stack || String(err)); app.exit(1); });

  app.on('window-all-closed', () => app.quit());

  // Stop the model servers through the backend before the process goes away, otherwise a GPU process can be left behind.
  app.on('before-quit', (event) => {
    if (quitting) return;
    quitting = true;
    if (setupAbort) setupAbort.abort(new Error('quit'));
    if (server) {
      event.preventDefault();
      server.stop().finally(() => app.quit());
    }
  });
}
