'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { notificationHandlers } = require('../src/notifications');
const { versionedDocumentUrl } = require('../src/navigation');

test('native notifications validate sender, origin, top frame, and bounded plain text', async () => {
  const shown = [];
  class NativeNotification extends EventEmitter {
    static isSupported() { return true; }
    constructor(options) { super(); shown.push(options); }
    show() { this.emit('show'); }
  }
  const frame = { url: 'http://127.0.0.1:9000/?desktopVersion=old' };
  const window = { webContents: { mainFrame: frame } };
  const handlers = notificationHandlers({ Notification: NativeNotification, getWindow: () => window, getOrigin: () => 'http://127.0.0.1:9000' });
  const event = { sender: window.webContents, senderFrame: frame };
  assert.deepEqual(handlers.capability(event), { supported: true });
  assert.deepEqual(await handlers.notify(event, { title: 'Finished', body: 'Saved track' }), { shown: true });
  assert.deepEqual(await handlers.notify({ ...event, sender: {} }, { title: 'Finished', body: '' }), { shown: false, reason: 'untrusted' });
  assert.deepEqual(await handlers.notify({ ...event, senderFrame: { url: frame.url } }, { title: 'Finished', body: '' }), { shown: false, reason: 'untrusted' });
  frame.url = 'http://127.0.0.1:90001';
  assert.deepEqual(handlers.capability(event), { supported: false });
  frame.url = 'http://127.0.0.1:9000';
  for (const payload of [null, { title: 'x'.repeat(121), body: '' }, { title: 'x', body: 'x'.repeat(501) }, { title: 'x\u0000', body: '' }]) {
    assert.deepEqual(await handlers.notify(event, payload), { shown: false, reason: 'invalid' });
  }
  assert.equal(shown.length, 1);
});

test('native notification capability handles unsupported platforms and delivery failure', async () => {
  const frame = { url: 'http://127.0.0.1:9000' };
  const window = { webContents: { mainFrame: frame } };
  const event = { sender: window.webContents, senderFrame: frame };
  class FailedNotification extends EventEmitter {
    static isSupported() { return true; }
    show() { this.emit('failed'); }
  }
  const handlers = notificationHandlers({ Notification: FailedNotification, getWindow: () => window, getOrigin: () => frame.url });
  assert.deepEqual(await handlers.notify(event, { title: 'Finished', body: '' }), { shown: false, reason: 'unavailable' });
  FailedNotification.isSupported = () => false;
  assert.deepEqual(handlers.capability(event), { supported: false });
  assert.deepEqual(await handlers.notify(event, { title: 'Finished', body: '' }), { shown: false, reason: 'unsupported' });
});

test('desktop document versions change the query while retaining the backend origin', () => {
  const old = 'http://127.0.0.1:9000/?keep=value';
  const next = versionedDocumentUrl(old, '0.3.0-dev.0');
  assert.equal(new URL(next).origin, new URL(old).origin);
  assert.equal(new URL(next).searchParams.get('desktopVersion'), '0.3.0-dev.0');
  assert.equal(new URL(next).searchParams.get('keep'), 'value');
});
