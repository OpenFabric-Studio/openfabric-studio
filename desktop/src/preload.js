'use strict';
const { contextBridge, ipcRenderer } = require('electron');

// Completion preferences are managed by the app; the main process validates each request and reports delivery.
contextBridge.exposeInMainWorld('openfabricNotifications', {
  capability: () => ipcRenderer.invoke('notifications:capability'),
  notify: (payload) => ipcRenderer.invoke('notifications:notify', payload),
});

// The first-run screen gets a small, explicit API. It has no access to Node or to the file system.
contextBridge.exposeInMainWorld('openfabric', {
  context: () => ipcRenderer.invoke('setup:context'),
  checks: (dir) => ipcRenderer.invoke('setup:checks', dir),
  chooseFolder: () => ipcRenderer.invoke('setup:choose-folder'),
  setRoot: (dir) => ipcRenderer.invoke('setup:set-root', dir),
  start: () => ipcRenderer.invoke('setup:start'),
  pause: () => ipcRenderer.invoke('setup:pause'),
  launch: () => ipcRenderer.invoke('setup:launch'),
  openExternal: (key) => ipcRenderer.invoke('setup:open-external', key),
  openLogs: () => ipcRenderer.invoke('setup:open-logs'),
  showData: () => ipcRenderer.invoke('setup:show-data'),
  onEvent: (callback) => {
    const handler = (_event, payload) => callback(payload);
    ipcRenderer.on('setup:event', handler);
    return () => ipcRenderer.removeListener('setup:event', handler);
  },
});
