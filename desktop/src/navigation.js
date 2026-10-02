'use strict';

/** Version the document URL on upgrades without changing origin-based browser storage. */
function versionedDocumentUrl(url, version) {
  const result = new URL(url);
  result.searchParams.set('desktopVersion', version);
  return result.href;
}

module.exports = { versionedDocumentUrl };
