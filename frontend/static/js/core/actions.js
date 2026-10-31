/* EVBro — Manejadores de eventos declarativos, sin código en línea.
 *
 * La Content-Security-Policy de la app no permite `onclick="…"` ni `<script>` en línea (así un texto colado en el HTML nunca puede
 * ejecutar nada). En su lugar, los elementos declaran qué función llamar:
 *
 *   <button data-on-click="switchTab('settings'); switchSettingsSubTab('notifs')">
 *   <select data-on-change="setMemberLevel('VIN', 7, this.value)">
 *   <input data-on-input="onGeofenceRadiusSlider(this.value)" data-on-enter="searchGeofenceLocation()">
 *   <div data-on-click="closeActionSheet()" data-self-only>      ← solo si se toca el propio elemento, no un hijo
 *
 * El valor es una lista de llamadas separadas por «;». Cada llamada es `nombre(args)`; `nombre` es una función global (se admite
 * `Objeto.metodo`) y los argumentos solo pueden ser literales ('texto', "texto", números, true, false, null) o los comodines
 * `this`, `this.value`, `this.checked` y `event`. No se evalúa nada más: no hay `eval` ni `new Function`.
 *
 * Un único oyente por tipo de evento en `document` atiende también a los elementos que el JavaScript crea después.
 * (Script clásico: comparte el ámbito global con los demás ficheros; ver el orden de carga en templates/index.html.)
 */
(function () {
  'use strict';

  const EVENTS = { click: 'data-on-click', change: 'data-on-change', input: 'data-on-input' };

  /** Splits `a('x;y'); b()` into calls, respecting quotes. */
  function splitCalls(text) {
    const out = [];
    let depth = 0, quote = null, start = 0;
    for (let i = 0; i < text.length; i++) {
      const ch = text[i];
      if (quote) { if (ch === '\\') i++; else if (ch === quote) quote = null; continue; }
      if (ch === "'" || ch === '"') quote = ch;
      else if (ch === '(') depth++;
      else if (ch === ')') depth--;
      else if (ch === ';' && depth === 0) { out.push(text.slice(start, i)); start = i + 1; }
    }
    out.push(text.slice(start));
    return out.map((s) => s.trim()).filter(Boolean);
  }

  /** `name(arg, arg)` → { name, args: [raw tokens] } or null when it is not a plain call. */
  function parseCall(call) {
    const m = /^([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*\(([\s\S]*)\)\s*$/.exec(call);
    if (!m) return null;
    const tokens = [];
    let quote = null, cur = '', seen = false;
    const body = m[2];
    for (let i = 0; i < body.length; i++) {
      const ch = body[i];
      if (quote) {
        if (ch === '\\' && i + 1 < body.length) { cur += ch + body[++i]; continue; }
        cur += ch;
        if (ch === quote) quote = null;
        continue;
      }
      if (ch === "'" || ch === '"') { quote = ch; cur += ch; seen = true; continue; }
      if (ch === ',') { tokens.push(cur.trim()); cur = ''; seen = false; continue; }
      cur += ch; if (ch.trim()) seen = true;
    }
    if (seen || cur.trim()) tokens.push(cur.trim());
    return { name: m[1], args: tokens };
  }

  function argValue(token, el, event) {
    if (token === 'this') return el;
    if (token === 'this.value') return el.value;
    if (token === 'this.checked') return el.checked;
    if (token === 'event') return event;
    if (token === 'true') return true;
    if (token === 'false') return false;
    if (token === 'null') return null;
    if (/^-?\d+(\.\d+)?$/.test(token)) return Number(token);
    if (token[0] === '"') return JSON.parse(token);
    if (token[0] === "'") return JSON.parse('"' + token.slice(1, -1).replace(/\\'/g, "'").replace(/"/g, '\\"') + '"');
    throw new Error(`argumento no permitido: ${token}`);
  }

  function resolve(name) {
    let fn = window;
    for (const part of name.split('.')) { fn = fn == null ? undefined : fn[part]; }
    return typeof fn === 'function' ? fn : null;
  }

  /** Runs the calls declared in `text` for element `el`. Returns the value of the last call. */
  function run(text, el, event) {
    let result;
    for (const call of splitCalls(text)) {
      const parsed = parseCall(call);
      if (!parsed) { console.error('[actions] expresión no admitida:', call); continue; }
      const fn = resolve(parsed.name);
      if (!fn) { console.error('[actions] función desconocida:', parsed.name); continue; }
      const path = parsed.name.split('.');
      const owner = path.length > 1 ? path.slice(0, -1).reduce((o, k) => (o == null ? o : o[k]), window) : window;
      result = fn.apply(owner, parsed.args.map((t) => argValue(t, el, event)));
    }
    return result;
  }

  function handle(event, attr) {
    const el = event.target && event.target.closest ? event.target.closest(`[${attr}]`) : null;
    if (!el) return;
    if (el.hasAttribute('data-self-only') && event.target !== el) return;
    run(el.getAttribute(attr), el, event);
  }

  for (const [type, attr] of Object.entries(EVENTS)) {
    document.addEventListener(type, (event) => handle(event, attr));
  }
  // Enter in a field: `data-on-enter` (the default form submission is cancelled)
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    const el = event.target && event.target.closest ? event.target.closest('[data-on-enter]') : null;
    if (!el) return;
    event.preventDefault();
    run(el.getAttribute('data-on-enter'), el, event);
  });

  /** Helper for the few handlers that removed an ancestor: `data-on-click="removeClosest(this, '.toast')"`. */
  window.removeClosest = function (el, selector) {
    const target = el && el.closest ? el.closest(selector) : null;
    if (target) target.remove();
  };
  window.EbroActions = { run, parseCall };
})();
