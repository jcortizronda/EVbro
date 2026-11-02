// Errors of this page for the server's anonymous reports (backend services/telemetry.py, docs/telemetry.md).
// The server keeps them only while the administrator has the reports turned on; it answers `collecting: false` otherwise and this
// script then stops sending for the rest of the visit. What travels: the error type, the message (the server scrubs it again) and
// the frames as `file.js:line:column function` — never the page address, what is on screen or anything typed.
(function () {
  const MAX_PER_VISIT = 8;
  let sent = 0;
  let off = false;
  const seen = new Set();

  function frames(stack) {
    const out = [];
    for (const line of String(stack || '').split('\n')) {
      // Chrome: "at fn (https://host/static/js/x.js?v=1:10:5)"  Firefox/Safari: "fn@https://host/static/js/x.js?v=1:10:5"
      const m = line.match(/(?:at\s+([\w$.<>]+)\s+\()?(?:([\w$.<>]+)@)?\S*?\/static\/([\w./-]+\.js)(?:\?[^:]*)?:(\d+)(?::(\d+))?/);
      if (m) out.push(`${m[3]}:${m[4]}${m[5] ? ':' + m[5] : ''}${(m[1] || m[2]) ? ' ' + (m[1] || m[2]) : ''}`);
      if (out.length >= 12) break;
    }
    return out;
  }

  function report(type, message, stack) {
    if (off || sent >= MAX_PER_VISIT || navigator.onLine === false) return;
    const st = frames(stack);
    if (!st.length) return;                               // not from EVBro's own scripts (an extension, a map tile…)
    const key = `${type}|${st[0]}`;
    if (seen.has(key)) return;
    seen.add(key);
    sent += 1;
    fetch('/api/telemetry/client-error', {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type: String(type || 'Error').slice(0, 60), message: String(message || '').slice(0, 500), stack: st }),
    }).then((r) => (r.ok ? r.json() : null)).then((d) => { if (!d || d.collecting === false) off = true; })
      .catch(() => { off = true; });
  }

  window.addEventListener('error', (ev) => {
    const err = ev.error;
    report(err && err.name, err ? err.message : ev.message, err && err.stack);
  });
  window.addEventListener('unhandledrejection', (ev) => {
    const err = ev.reason;
    if (err instanceof Error) report(err.name, err.message, err.stack);
  });
})();
