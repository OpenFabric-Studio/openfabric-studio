'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { isAllowedNavigation, trustedSetupSender } = require('../src/navigation');

const setupPage = path.resolve('/repo/desktop/renderer/index.html');
const setupUrl = pathToFileURL(setupPage).href;

test('navigation allows the setup document and exact backend origin', () => {
  const options = { setupPage, backendUrl: 'http://127.0.0.1:9000/' };
  for (const url of [setupUrl, `${setupUrl}?state=crashed#details`, 'http://127.0.0.1:9000/voice?test=value']) {
    assert.equal(isAllowedNavigation(url, options), true, url);
  }
  for (const url of [pathToFileURL(path.resolve('/repo/private.html')).href, 'https://example.com', 'http://127.0.0.1:90001/', 'http://127.0.0.1:9000@evil.example/', 'http://127.0.0.1:9000.evil.example/', 'http://user:password@127.0.0.1:9000/', 'not a URL']) {
    assert.equal(isAllowedNavigation(url, options), false, url);
  }
  assert.equal(isAllowedNavigation('http://127.0.0.1:9000/', { setupPage }), false);
});

test('setup IPC requires this window top frame and the exact setup document', () => {
  const frame = { url: `${setupUrl}?state=crashed` };
  const window = { webContents: { mainFrame: frame } };
  const event = { sender: window.webContents, senderFrame: frame };
  assert.equal(trustedSetupSender(event, window, setupPage), true);
  assert.equal(trustedSetupSender(event, null, setupPage), false);
  assert.equal(trustedSetupSender({ ...event, sender: {} }, window, setupPage), false);
  assert.equal(trustedSetupSender({ ...event, senderFrame: { url: setupUrl } }, window, setupPage), false);
  for (const url of ['http://127.0.0.1:9000/', pathToFileURL(path.resolve('/repo/private.html')).href]) {
    frame.url = url;
    assert.equal(trustedSetupSender(event, window, setupPage), false);
  }
});
