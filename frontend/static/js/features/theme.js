/* EVBro — Light/dark/automatic theme.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

function getSavedThemePreference() {
  return localStorage.getItem('evbro_theme') || 'dark';
}

// ───────────────────────── Colour accent (Ajustes → Tema → Color) ─────────────────────────
const THEME_ACCENTS = { blue: 'Azul', rose: 'Rosa', violet: 'Violeta', green: 'Verde', amber: 'Ámbar', red: 'Rojo' };

function getSavedAccent() {
  const a = localStorage.getItem('evbro_accent') || 'blue';
  return THEME_ACCENTS[a] ? a : 'blue';
}

/** Sets `data-accent` on <html> («blue» = no attribute: the default tokens) and marks the chosen swatch in Ajustes. */
function applyAccent(name) {
  if (name === 'blue') document.documentElement.removeAttribute('data-accent');
  else document.documentElement.setAttribute('data-accent', name);
  document.querySelectorAll('.accent-dot').forEach((b) => {
    const on = b.dataset.accent === name;
    b.classList.toggle('selected', on);
    b.setAttribute('aria-checked', String(on));
  });
  const label = document.getElementById('accentCurrentLabel');
  if (label) label.textContent = THEME_ACCENTS[name] || name;
  document.dispatchEvent(new CustomEvent('evbro:accent', { detail: { accent: name } }));
}

function setAccent(name) {
  if (!THEME_ACCENTS[name]) return;
  localStorage.setItem('evbro_accent', name);
  applyAccent(name);
}

/** The accent colour as CSS text, for canvas and Leaflet drawings that cannot read a CSS variable: `themeAccent()` → "rgb(…)",
 *  `themeAccent(0.25)` → "rgba(…, 0.25)". */
function themeAccent(alpha) {
  const rgb = getComputedStyle(document.documentElement).getPropertyValue('--accent-rgb').trim() || '41, 151, 255';
  return alpha === undefined ? `rgb(${rgb})` : `rgba(${rgb}, ${alpha})`;
}

function resolveEffectiveTheme(preference) {
  if (preference === 'auto') {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
  }
  return preference === 'light' ? 'light' : 'dark';
}

function applyTheme(preference) {
  const effective = resolveEffectiveTheme(preference);
  document.documentElement.setAttribute('data-theme', effective);

  // Update the theme-color meta for mobile browsers (Apple HIG)
  const metaTheme = document.querySelector('meta[name="theme-color"]');
  if (metaTheme) {
    metaTheme.setAttribute('content', effective === 'light' ? '#f5f5f7' : '#000000');
  }

  // Update the icon of the quick button in the navbar
  const iconSpan = document.getElementById('quickThemeIcon');
  if (iconSpan) {
    iconSpan.innerHTML = icon(effective === 'light' ? 'sun' : 'moon', 20);
  }
  const btnToggle = document.getElementById('btnQuickThemeToggle');
  if (btnToggle) {
    btnToggle.title = effective === 'light' ? 'Cambiar a Modo Oscuro' : 'Cambiar a Modo Claro';
  }

  // Update the UI in the Ajustes tab
  updateThemeSettingsUI(preference, effective);
}

function setThemeMode(preference) {
  localStorage.setItem('evbro_theme', preference);
  applyTheme(preference);
}

function toggleThemeQuick() {
  const current = getSavedThemePreference();
  const effective = resolveEffectiveTheme(current);
  const next = effective === 'light' ? 'dark' : 'light';
  setThemeMode(next);
}

function updateThemeSettingsUI(pref, eff) {
  const preference = pref || getSavedThemePreference();
  const effective = eff || resolveEffectiveTheme(preference);

  // Active cards
  const cardDark = document.getElementById('themeCardDark');
  const cardLight = document.getElementById('themeCardLight');
  const cardAuto = document.getElementById('themeCardAuto');

  const radioDark = document.getElementById('radioThemeDark');
  const radioLight = document.getElementById('radioThemeLight');
  const radioAuto = document.getElementById('radioThemeAuto');

  for (const [el, key] of [[cardDark, 'dark'], [cardLight, 'light'], [cardAuto, 'auto']]) if (el) el.setAttribute('aria-checked', String(preference === key));
  if (cardDark) cardDark.classList.toggle('selected', preference === 'dark');
  if (cardLight) cardLight.classList.toggle('selected', preference === 'light');
  if (cardAuto) cardAuto.classList.toggle('selected', preference === 'auto');

  if (radioDark) radioDark.checked = (preference === 'dark');
  if (radioLight) radioLight.checked = (preference === 'light');
  if (radioAuto) radioAuto.checked = (preference === 'auto');

  const badge = document.getElementById('themeCurrentBadge');
  if (badge) {
    if (preference === 'auto') {
      badge.innerText = t('Automático ({p0})', {p0: effective === 'light' ? 'Claro' : 'Oscuro'});
      badge.className = 'status-badge parked';
    } else if (preference === 'light') {
      badge.innerText = 'Modo Claro';
      badge.className = 'status-badge driving';
    } else {
      badge.innerText = 'Modo Oscuro';
      badge.className = 'status-badge parked';
    }
  }
}

function initTheme() {
  const pref = getSavedThemePreference();
  applyTheme(pref);
  applyAccent(getSavedAccent());

  // Listen for system preference changes when set to 'auto'
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
      if (getSavedThemePreference() === 'auto') {
        applyTheme('auto');
      }
    });
  }
}
