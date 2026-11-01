/* EVBro — Statistics (Más → Estadísticas): charts of the car's history from GET /api/vehicles/{vin}/stats.
 * Bars and lines are drawn on canvas with the theme's accent colour; nothing external. Everything drawn is computed by the server
 * (services/stats.py); here it is only drawn and labelled. A car without data shows «Sin datos» on each card.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */
(function () {
  'use strict';

  const el = (id) => document.getElementById(id);
  let picker = null;
  const pickerOf = () => picker || (picker = PeriodPicker.create('statsPeriod', { storageKey: 'evbro_period_stats', presets: ['12m', 'year', 'last_year', 'all'], defaultPeriod: '12m', onChange: load }));
  let lastData = null;
  const MONTHS = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
  const monthLabel = (key) => { const [y, m] = key.split('-'); return `${MONTHS[Number(m) - 1]} ${y.slice(2)}`; };
  const num = (v, d = 1) => (v === null || v === undefined ? '--' : Number(v).toLocaleString(I18n.locale, { minimumFractionDigits: d, maximumFractionDigits: d }));

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  /** Prepares a canvas for the device pixel ratio and returns its 2D context with CSS-pixel coordinates. */
  function setup(canvas, height) {
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.parentElement.clientWidth || 320;
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    return { ctx, width, height };
  }

  function empty(canvas, text = 'Sin datos todavía') {
    const { ctx, width, height } = setup(canvas, 120);
    ctx.fillStyle = css('--text-muted'); ctx.font = '600 13px system-ui, sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(text, width / 2, height / 2);
  }

  /** Vertical bars (one or two series) with month labels; `series` = [{values, color}], values aligned with `labels`. */
  function bars(canvas, labels, series, unit, height = 180) {
    if (!labels.length) { empty(canvas); return; }
    const { ctx, width } = setup(canvas, height);
    const pad = { l: 36, r: 8, t: 14, b: 26 };
    const plotW = width - pad.l - pad.r, plotH = height - pad.t - pad.b;
    const max = Math.max(1, ...series.flatMap((s) => s.values.map((v) => v || 0)));
    const groupW = plotW / labels.length, barW = Math.max(3, (groupW * 0.7) / series.length);
    ctx.font = '11px system-ui, sans-serif'; ctx.fillStyle = css('--text-muted'); ctx.strokeStyle = css('--line'); ctx.lineWidth = 1;
    for (let i = 0; i <= 3; i++) {
      const y = pad.t + plotH - (plotH * i) / 3;
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(width - pad.r, y); ctx.stroke();
      ctx.textAlign = 'right'; ctx.fillText(num((max * i) / 3, max >= 100 ? 0 : 1), pad.l - 4, y + 4);
    }
    labels.forEach((label, i) => {
      series.forEach((s, k) => {
        const v = s.values[i] || 0;
        const h = (v / max) * plotH;
        const x = pad.l + i * groupW + (groupW - barW * series.length) / 2 + k * barW;
        ctx.fillStyle = s.color;
        ctx.beginPath(); ctx.roundRect(x, pad.t + plotH - h, barW - 1, h, 3); ctx.fill();
      });
      if (labels.length <= 13 || i % Math.ceil(labels.length / 12) === 0) {
        ctx.fillStyle = css('--text-muted'); ctx.textAlign = 'center';
        ctx.fillText(label, pad.l + i * groupW + groupW / 2, height - 8);
      }
    });
    if (unit) { ctx.textAlign = 'left'; ctx.fillStyle = css('--text-muted'); ctx.fillText(unit, pad.l, 10); }
  }

  /** A line with points, for a trend (capacity, cost per km). `points` = [{label, value}]. */
  function line(canvas, points, unit, color, height = 170, decimals = 1) {
    const valid = points.filter((p) => p.value !== null && p.value !== undefined);
    if (valid.length < 2) { empty(canvas, valid.length ? 'Hace falta más de un punto' : 'Sin datos todavía'); return; }
    const { ctx, width } = setup(canvas, height);
    const pad = { l: 40, r: 10, t: 14, b: 26 };
    const plotW = width - pad.l - pad.r, plotH = height - pad.t - pad.b;
    const values = valid.map((p) => p.value);
    let min = Math.min(...values), max = Math.max(...values);
    if (max === min) { max += 1; min -= 1; }
    const span = max - min; min -= span * 0.1; max += span * 0.1;
    const x = (i) => pad.l + (plotW * i) / (valid.length - 1);
    const y = (v) => pad.t + plotH - ((v - min) / (max - min)) * plotH;
    ctx.font = '11px system-ui, sans-serif'; ctx.strokeStyle = css('--line'); ctx.lineWidth = 1; ctx.fillStyle = css('--text-muted');
    for (let i = 0; i <= 3; i++) {
      const v = min + ((max - min) * i) / 3, yy = y(v);
      ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(width - pad.r, yy); ctx.stroke();
      ctx.textAlign = 'right'; ctx.fillText(num(v, decimals), pad.l - 4, yy + 4);
    }
    ctx.strokeStyle = color; ctx.lineWidth = 2.2; ctx.lineJoin = 'round';
    ctx.beginPath(); valid.forEach((p, i) => (i ? ctx.lineTo(x(i), y(p.value)) : ctx.moveTo(x(i), y(p.value)))); ctx.stroke();
    ctx.fillStyle = color;
    valid.forEach((p, i) => { ctx.beginPath(); ctx.arc(x(i), y(p.value), 3.2, 0, Math.PI * 2); ctx.fill(); });
    ctx.fillStyle = css('--text-muted'); ctx.textAlign = 'center';
    valid.forEach((p, i) => { if (valid.length <= 8 || i % Math.ceil(valid.length / 8) === 0) ctx.fillText(p.label, x(i), height - 8); });
    if (unit) { ctx.textAlign = 'left'; ctx.fillText(unit, pad.l, 10); }
  }

  /** Horizontal bars with a text label per row (buckets, places). `rows` = [{label, value, extra}]. */
  function hbars(canvas, rows, unit, color, decimals = 1) {
    if (!rows.length) { empty(canvas); return; }
    const rowH = 26, height = rows.length * rowH + 12;
    const { ctx, width } = setup(canvas, height);
    const labelW = Math.min(150, width * 0.38);
    const max = Math.max(1, ...rows.map((r) => r.value || 0));
    ctx.font = '12px system-ui, sans-serif';
    // the value text goes after the bar: measure the longest one so it never runs off the canvas (the extra is dropped when tight)
    const narrow = width < 480;
    const texts = rows.map((r) => `${num(r.value, decimals)} ${unit}${r.extra && !narrow ? ` · ${r.extra}` : ''}`);
    const textW = Math.max(...texts.map((t) => ctx.measureText(t).width)) + 10;
    rows.forEach((r, i) => {
      const yy = 6 + i * rowH;
      ctx.fillStyle = css('--text-primary'); ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
      const label = r.label.length > 22 ? `${r.label.slice(0, 21)}…` : r.label;
      ctx.fillText(label, 0, yy + rowH / 2);
      const w = ((r.value || 0) / max) * Math.max(20, width - labelW - textW);
      ctx.fillStyle = color; ctx.beginPath(); ctx.roundRect(labelW, yy + 6, Math.max(2, w), rowH - 12, 4); ctx.fill();
      ctx.fillStyle = css('--text-secondary'); ctx.textAlign = 'left';
      ctx.fillText(texts[i], labelW + Math.max(2, w) + 6, yy + rowH / 2);
    });
  }

  function render(d) {
    lastData = d;
    const accent = css('--accent') || '#2997ff', green = css('--accent-green') || '#30d158', amber = css('--accent-amber') || '#ff9f0a';
    const red = css('--accent-red') || '#ff453a';
    const months = d.monthly.map((m) => m.month);
    const labels = months.map(monthLabel);
    bars(el('stKm'), labels, [{ values: d.monthly.map((m) => m.km), color: accent }], 'km');
    bars(el('stEnergy'), labels, [{ values: d.monthly.map((m) => m.kwh_charged), color: green }, { values: d.monthly.map((m) => m.liters), color: amber }], 'kWh cargados · litros');
    bars(el('stCost'), labels, [{ values: d.monthly.map((m) => m.cost_total), color: red }], '€');
    line(el('stCostKm'), d.monthly.map((m) => ({ label: monthLabel(m.month), value: m.cost_per_km })), '€/km', red, 170, 3);
    line(el('stEff'), d.monthly.map((m) => ({ label: monthLabel(m.month), value: m.kwh_per_100km })), 'kWh/100 km', accent);
    line(el('stCapacity'), d.battery_health.map((b) => ({ label: monthLabel(b.month), value: b.capacity_kwh })), 'kWh útiles estimados', green);
    hbars(el('stSpeed'), d.consumption_by_speed.map((b) => ({ label: b.label, value: b.kwh_per_100km, extra: `${b.drives} viajes` })), 'kWh/100 km', accent);
    hbars(el('stPlaces'), d.charges_by_place.map((p) => ({ label: p.address, value: p.kwh, extra: `${p.count} cargas${p.cost ? ` · ${num(p.cost, 2)} €` : ''}` })), 'kWh', green);
    line(el('stDrain'), d.idle_drain.map((m) => ({ label: monthLabel(m.month), value: m.pct_per_day })), '% al día aparcado', amber, 150, 2);
    const t = d.totals || {};
    el('stTotals').innerHTML = [
      ['Km', num(t.km, 0)], ['kWh a la batería', num(t.kwh_charged)], ['Litros', num(t.liters)], ['Gasto total', `${num(t.cost_total, 2)} €`],
      ['€/km', t.cost_per_km === null || t.cost_per_km === undefined ? '--' : num(t.cost_per_km, 3)],
      ['Viajes', String(t.drives || 0)], ['Cargas', String(t.charges || 0)],
    ].map(([k, v]) => `<div><small>${k}</small><strong>${v}</strong></div>`).join('');
    el('stHint').textContent = d.notes || '';
  }

  async function load() {
    if (typeof currentVin === 'undefined' || !currentVin || !el('tabStats')) return;
    try {
      const res = await fetch(`/api/vehicles/${currentVin}/stats?${pickerOf().query()}`);
      if (!res.ok) throw new Error(res.status);
      render(await res.json());
    } catch (e) {
      showToast('No se pudieron cargar las estadísticas.', 'error');
    }
  }

  function setPeriod(p) {
    pickerOf().set({ period: p });                                     // the picker calls load()
  }

  window.addEventListener('resize', () => { if (lastData && el('tabStats').classList.contains('active')) render(lastData); });
  document.addEventListener('evbro:accent', () => { if (lastData) render(lastData); });
  window.Stats = { load, setPeriod };
})();
