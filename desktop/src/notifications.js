'use strict';

/** Only the current app's top frame may request OS notifications. No links, icons, or actions cross IPC. */
function notificationHandlers({ Notification, getWindow, getOrigin }) {
  function trusted(event) {
    const window = getWindow();
    if (!window || event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) return false;
    try {
      const expected = new URL(getOrigin());
      const actual = new URL(event.senderFrame.url);
      return expected.protocol === 'http:' && expected.hostname === '127.0.0.1' && actual.origin === expected.origin;
    } catch { return false; }
  }
  function capability(event) { return { supported: trusted(event) && Notification.isSupported() }; }
  async function notify(event, payload) {
    if (!trusted(event)) return { shown: false, reason: 'untrusted' };
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)
      || typeof payload.title !== 'string' || !payload.title.trim() || payload.title.length > 120
      || typeof payload.body !== 'string' || payload.body.length > 500
      || /[\u0000-\u001f\u007f]/.test(payload.title) || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(payload.body)) {
      return { shown: false, reason: 'invalid' };
    }
    if (!Notification.isSupported()) return { shown: false, reason: 'unsupported' };
    return new Promise((resolve) => {
      let notification;
      let timer;
      const finish = (shown) => {
        clearTimeout(timer);
        notification?.removeAllListeners('show');
        notification?.removeAllListeners('failed');
        resolve(shown ? { shown: true } : { shown: false, reason: 'unavailable' });
      };
      try {
        notification = new Notification({ title: payload.title.trim(), body: payload.body });
        notification.once('show', () => finish(true));
        notification.once('failed', () => finish(false));
        timer = setTimeout(() => finish(false), 5000);
        notification.show();
      } catch { finish(false); }
    });
  }
  return { capability, notify };
}

module.exports = { notificationHandlers };
