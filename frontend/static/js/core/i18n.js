/* EVBro — Language of the interface (docs/i18n.md).
 * Spanish is the source text of the whole app. The server puts the dictionary of the account's language in the
 * `#i18nData` JSON block (empty for Spanish) and sets `<html lang>`; this script, loaded in <head> before anything paints:
 *   * translates every text node and translatable attribute of the page as the parser adds them (MutationObserver), and again
 *     whatever the other scripts paint later (innerHTML, textContent, placeholders…), key = the Spanish text with its
 *     whitespace collapsed;
 *   * exposes `t(text, params)` for the texts the JS composes with values (`t('{n} viajes', {n: 3})`) and
 *     `I18n.locale` for dates and numbers.
 * Anything inside `[data-i18n-skip]`, <code>, <script>, <style> or an SVG is left alone.
 * (Classic script: shares the global scope with the other files.)
 */
(function () {
  const LOCALES = { es: 'es-ES', en: 'en-GB', pt: 'pt-PT' };
  const ATTRS = ['placeholder', 'title', 'aria-label', 'alt', 'data-tooltip', 'data-empty'];
  const SKIP_TAGS = { SCRIPT: 1, STYLE: 1, CODE: 1, NOSCRIPT: 1, svg: 1, SVG: 1, TEXTAREA: 1 };
  const LETTERS = /[A-Za-zÀ-ÿ]/;
  const root = document.documentElement;
  const lang = LOCALES[root.lang] ? root.lang : 'es';
  let dict = {};
  const block = document.getElementById('i18nData');
  if (block) {
    try { dict = JSON.parse(block.textContent || '{}') || {}; } catch (e) { dict = {}; }
  }
  const active = lang !== 'es' && Object.keys(dict).length > 0;

  const norm = (s) => String(s).replace(/\s+/g, ' ').trim();

  function t(text, params) {
    let out = dict[norm(text)] || String(text);
    if (params) out = out.replace(/\{(\w+)\}/g, (m, k) => (k in params ? params[k] : m));
    return out;
  }

  function isSkipped(el) {
    return !!el && el.nodeType === 1 && (SKIP_TAGS[el.tagName] === 1 || el.hasAttribute('data-i18n-skip'));
  }

  function translateText(node) {
    const value = node.nodeValue;
    if (!value || !LETTERS.test(value) || isSkipped(node.parentNode)) return;
    const out = dict[norm(value)];
    if (!out || out === norm(value)) return;
    node.nodeValue = value.replace(/^\s*/, '').replace(/\s*$/, '') === value ? out
      : value.match(/^\s*/)[0] + out + value.match(/\s*$/)[0];      // keeps the whitespace around the text
  }

  function translateAttrs(el) {
    for (const name of ATTRS) {
      const value = el.getAttribute(name);
      if (!value || !LETTERS.test(value)) continue;
      const out = dict[norm(value)];
      if (out && out !== value) el.setAttribute(name, out);
    }
  }

  function translateTree(node) {
    if (!node) return;
    if (node.nodeType === 3) { translateText(node); return; }
    if (node.nodeType !== 1 && node.nodeType !== 11) return;
    if (node.nodeType === 1) {
      if (isSkipped(node)) return;
      translateAttrs(node);
    }
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeType === 1 && isSkipped(n) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    let n;
    while ((n = walker.nextNode())) {
      if (n.nodeType === 1) translateAttrs(n); else translateText(n);
    }
  }

  if (active) {
    // The parser adds the body's nodes after this script runs: the observer translates them as they come, and everything the
    // app paints later. A translated text no longer matches a Spanish key, so the second pass over it does nothing.
    new MutationObserver((records) => {
      for (const r of records) {
        if (r.type === 'childList') r.addedNodes.forEach(translateTree);
        else if (r.type === 'characterData') translateText(r.target);
        else if (r.type === 'attributes') translateAttrs(r.target);
      }
    }).observe(root, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });
    document.addEventListener('DOMContentLoaded', () => translateTree(document.body));
  }

  document.addEventListener('DOMContentLoaded', () => {
    const select = document.getElementById('languageSelect');
    if (select) select.value = lang;
  });

  /** Ajustes → Sistema → Idioma: saves it in the account and reloads the page in that language. */
  async function setLanguage(next) {
    if (!LOCALES[next] || next === lang) return;
    try {
      const res = await fetch('/api/account/language', {
        method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language: next }),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      window.location.reload();
    } catch (e) {
      const select = document.getElementById('languageSelect');
      if (select) select.value = lang;
      if (typeof showToast === 'function') showToast(t('No se pudo cambiar el idioma. Inténtalo de nuevo.'), 'error');
    }
  }

  window.t = t;
  window.I18n = { lang, locale: LOCALES[lang], active, t, translateTree, setLanguage };
})();
