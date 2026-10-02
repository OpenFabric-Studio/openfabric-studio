// Remiqora landing: focus a stem lane, copy install commands. No tracking, no external calls.
(() => {
  const lanes = document.getElementById('lanes');
  if (lanes) {
    lanes.addEventListener('click', (e) => {
      const btn = e.target.closest('.lane__label');
      if (!btn) return;
      const lane = btn.closest('.lane');
      const wasSolo = lane.classList.contains('is-solo');
      lanes.querySelectorAll('.lane').forEach((l) => l.classList.remove('is-solo'));
      lanes.querySelectorAll('.lane__label').forEach((b) => b.setAttribute('aria-pressed', 'false'));
      lanes.classList.toggle('has-solo', !wasSolo);
      if (!wasSolo) {
        lane.classList.add('is-solo');
        btn.setAttribute('aria-pressed', 'true');
      }
    });
  }

  const status = document.getElementById('copy-status');
  document.querySelectorAll('.copy').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const pre = document.getElementById(btn.dataset.target);
      if (!pre) return;
      const text = pre.textContent.trim();
      try {
        await navigator.clipboard.writeText(text);
      } catch {
        const r = document.createRange();
        r.selectNodeContents(pre);
        const s = window.getSelection();
        s.removeAllRanges();
        s.addRange(r);
        document.execCommand && document.execCommand('copy');
      }
      const label = btn.dataset.label;
      btn.textContent = btn.dataset.copied;
      if (status) status.textContent = btn.dataset.copied;
      setTimeout(() => { btn.textContent = label; if (status) status.textContent = ''; }, 1800);
    });
  });
})();
