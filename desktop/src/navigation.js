'use strict';
const { pathToFileURL } = require('node:url');

function isSetupDocument(url, setupPage) {
  try {
    const actual = new URL(url);
    const expected = pathToFileURL(setupPage);
    return actual.protocol === expected.protocol && actual.host === expected.host && actual.pathname === expected.pathname;
  } catch { return false; }
}

/** Only the shipped setup document or this backend's parsed origin stays in the window. */
function isAllowedNavigation(url, { setupPage, backendUrl }) {
  if (isSetupDocument(url, setupPage)) return true;
  try {
    const actual = new URL(url);
    const expected = new URL(backendUrl);
    return expected.protocol === 'http:' && expected.hostname === '127.0.0.1'
      && actual.origin === expected.origin && !actual.username && !actual.password;
  } catch { return false; }
}

/** Setup privileges belong solely to the current setup page's top frame. */
function trustedSetupSender(event, window, setupPage) {
  return !!window && event.sender === window.webContents && event.senderFrame === window.webContents.mainFrame
    && isSetupDocument(event.senderFrame.url, setupPage);
}

/** Version the document URL on upgrades without changing origin-based browser storage. */
function versionedDocumentUrl(url, version) {
  const result = new URL(url);
  result.searchParams.set('desktopVersion', version);
  return result.href;
}

module.exports = { versionedDocumentUrl, isAllowedNavigation, trustedSetupSender };
