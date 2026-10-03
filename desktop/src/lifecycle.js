'use strict';

/** Abort setup, await its owned work, and only then release the backend. */
async function drainApplication({ setupAbort, setupTask, server }) {
  setupAbort?.abort(new Error('quit'));
  if (setupTask) await Promise.allSettled([setupTask]);
  if (server) await server.stop();
}

module.exports = { drainApplication };
