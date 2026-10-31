/* EVBro — One period picker for every list and statistic (Estadísticas, Costes, Repostajes, Gastos, Cargas, Viajes, Cronología).
 * Preset chips («Este mes», «Año pasado», «12 meses»…) plus «Elegir…», which opens two month fields (desde / hasta, both optional).
 * The choice is remembered per view in localStorage and sent to the API as `period=<preset>` or `period=custom&from=AAAA-MM&to=AAAA-MM`
 * (backend/src/app/services/periods.py). (Classic script: shares the global scope; load it before the features that use it.)
 */
window.PeriodPicker = (function () {
  'use strict';

  const LABELS = {
    all: t('Todo'), month: t('Este mes'), last_month: t('Mes pasado'), year: t('Este año'), last_year: t('Año pasado'),
    '6m': t('6 meses'), '12m': t('12 meses'), '7d': t('7 días'), '30d': t('30 días'), '90d': t('3 meses'), custom: t('Elegir…'),
  };
  const MONTHS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'];
  const monthText = (ym) => { const [y, m] = ym.split('-'); const n = parseInt(m, 10); return n >= 1 && n <= 12 ? `${MONTHS[n - 1]} ${y}` : ym; };
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  function create(hostId, options) {
    const host = document.getElementById(hostId);
    if (!host) return null;
    const presets = options.presets || ['all', 'year', '12m', 'month'];
    const storageKey = options.storageKey || `evbro_period_${hostId}`;
    let state = { period: options.defaultPeriod || presets[0], from: '', to: '' };
    try {
      const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
      if (saved && (presets.includes(saved.period) || saved.period === 'custom')) state = { period: saved.period, from: saved.from || '', to: saved.to || '' };
    } catch (e) { /* no storage: the default stays */ }

    host.classList.add('period-picker');
    host.innerHTML = `
      <div class="pp-chips" role="group" aria-label="Periodo">
        ${presets.map((p) => `<button type="button" class="costs-period-btn" data-period="${p}">${LABELS[p] || p}</button>`).join('')}
        <button type="button" class="costs-period-btn pp-custom-btn" data-period="custom">${LABELS.custom}</button>
      </div>
      <div class="pp-custom" hidden>
        <label class="pp-field"><span>Desde</span><input type="month" class="pp-from" aria-label="Desde (mes)"></label>
        <label class="pp-field"><span>Hasta</span><input type="month" class="pp-to" aria-label="Hasta (mes)"></label>
        <button type="button" class="btn-secondary pp-apply">Aplicar</button>
      </div>
      <div class="pp-label" aria-live="polite"></div>`;
    const chips = host.querySelectorAll('.costs-period-btn');
    const custom = host.querySelector('.pp-custom');
    const fromEl = host.querySelector('.pp-from');
    const toEl = host.querySelector('.pp-to');
    const labelEl = host.querySelector('.pp-label');

    function save() { try { localStorage.setItem(storageKey, JSON.stringify(state)); } catch (e) { /* ignore */ } }
    function label() {
      if (state.period !== 'custom') return LABELS[state.period] || state.period;
      if (state.from && state.to) return state.from === state.to ? cap(monthText(state.from)) : `${cap(monthText(state.from))} – ${monthText(state.to)}`;
      if (state.from) return `Desde ${monthText(state.from)}`;
      if (state.to) return `Hasta ${monthText(state.to)}`;
      return 'Todo';
    }
    function paint() {
      chips.forEach((b) => b.classList.toggle('is-active', b.dataset.period === state.period));
      custom.hidden = state.period !== 'custom';
      fromEl.value = state.from; toEl.value = state.to;
      labelEl.textContent = state.period === 'custom' ? label() : '';
    }
    function params(usp) {
      const p = usp || new URLSearchParams();
      p.set('period', state.period);
      if (state.period === 'custom') {
        if (state.from) p.set('from', state.from); else p.delete('from');
        if (state.to) p.set('to', state.to); else p.delete('to');
      } else { p.delete('from'); p.delete('to'); }
      return p;
    }
    function query() { return params().toString(); }
    function set(next, silent) {
      state = { period: next.period || 'all', from: next.from || '', to: next.to || '' };
      save(); paint();
      if (!silent && options.onChange) options.onChange(state);
    }
    chips.forEach((b) => b.addEventListener('click', () => {
      if (b.dataset.period === 'custom') { state.period = 'custom'; paint(); fromEl.focus(); return; }   // choose months, then «Aplicar»
      set({ period: b.dataset.period });
    }));
    host.querySelector('.pp-apply').addEventListener('click', () => {
      const from = fromEl.value, to = toEl.value;
      if (from && to && to < from) { if (typeof showToast === 'function') showToast('El final es anterior al inicio.', 'warning'); return; }
      set({ period: 'custom', from, to });
    });
    [fromEl, toEl].forEach((el) => el.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') host.querySelector('.pp-apply').click(); }));
    paint();
    return { params, query, label, value: () => ({ ...state }), set, isEverything: () => state.period === 'all' };
  }

  return { create, LABELS, monthText };
})();
