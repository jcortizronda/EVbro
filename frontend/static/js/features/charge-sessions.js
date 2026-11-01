/* EVBro — Energía: charging sessions (history and charts), charts, mini map and PVPC banner.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

let chargesPage = { offset: 0, months: {}, lastMonth: null, gen: 0 };

const CHARGE_MODE_LABEL = { pvpc: 'PVPC', time_of_use: '2.0TD', manual: 'Manual', free: 'Gratis' };

let chargesPicker = null;
const chargesPickerOf = () => chargesPicker || (chargesPicker = PeriodPicker.create('chgPeriod', { storageKey: 'evbro_period_charges', presets: ['all', 'month', 'last_month', 'year', '12m'], onChange: loadCharges }));

/** Reloads the charge history from the first page; the cards above add up every charge (server side), the list follows the period picker. */
async function loadCharges() {
  if (!currentVin) return;
  const list = document.getElementById('chargesList');
  list.innerHTML = '<p class="hist-empty">Cargando cargas...</p>';
  historyMore(document.getElementById('chargesMore'), false);
  chargesPage = { offset: 0, months: {}, lastMonth: null, gen: chargesPage.gen + 1 };
  const gen = chargesPage.gen;
  const vin = currentVin;
  try {
    const res = await fetch(`/api/vehicles/${vin}/charges/summary`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const sum = await res.json();
    if (gen !== chargesPage.gen) return;                  // a newer load (or another car) took over
    document.getElementById('chargesTotalKwh').innerText = `${historyNum(sum.kwh_added)} kWh`;
    document.getElementById('chargesTotalCost').innerText = `${historyNum(sum.cost, 2)} €`;
    document.getElementById('chargesTotalCount').innerText = sum.count;
    (sum.months || []).forEach((m) => { chargesPage.months[m.month] = m; });
    if (!sum.count) { list.innerHTML = '<p class="hist-empty">No hay sesiones de recarga registradas aún.</p>'; return; }
  } catch (err) {
    list.innerHTML = `<p class="hist-empty is-error">${t('Error al cargar recargas: {p0}', {p0: escapeHtml(err.message || err)})}</p>`;
    return;
  }
  await fetchChargesPage(true);
}

async function loadMoreCharges() {
  const btn = document.getElementById('chargesMore');
  if (btn) btn.disabled = true;
  await fetchChargesPage(false);
}

async function fetchChargesPage(first) {
  const list = document.getElementById('chargesList');
  const vin = currentVin;
  const gen = chargesPage.gen;
  try {
    // One extra row tells whether there is another page
    const res = await fetch(`/api/vehicles/${vin}/charges?limit=${HISTORY_PAGE_SIZE + 1}&offset=${chargesPage.offset}&${chargesPickerOf().query()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const rows = await res.json();
    if (gen !== chargesPage.gen) return;
    const charges = rows.slice(0, HISTORY_PAGE_SIZE);
    if (first) list.innerHTML = '';
    charges.forEach((c) => {
      historyMaybeMonth(list, chargesPage, c.start_time, (key) => {
        const m = chargesPage.months[key];
        if (!m) return '';
        const cost = m.cost ? ` · ${historyNum(m.cost, 2)} €` : '';
        return `${m.count} ${m.count === 1 ? 'carga' : 'cargas'} · ${historyNum(m.kwh_added)} kWh${cost}`;
      });
      list.appendChild(chargeRow(c));
    });
    chargesPage.offset += charges.length;
    historyMore(document.getElementById('chargesMore'), rows.length > HISTORY_PAGE_SIZE);
  } catch (err) {
    if (first) list.innerHTML = `<p class="hist-empty is-error">${t('Error al cargar recargas: {p0}', {p0: escapeHtml(err.message || err)})}</p>`;
    else showToast('No se pudieron cargar más cargas.', 'error');
    historyMore(document.getElementById('chargesMore'), !first);
  }
}

function chargeRow(c) {
  const pct = (v) => (v === null || v === undefined ? null : Math.round(v));
  const start = pct(c.start_battery_pct);
  const end = pct(c.end_battery_pct);
  const added = start !== null && end !== null ? end - start : null;
  const cost = c.charge_cost !== null && c.charge_cost !== undefined ? c.charge_cost : null;
  const costText = c.cost_mode === 'free' ? 'Gratis' : (cost ? `${historyNum(cost, 2)} €` : '');
  const figs = [added !== null ? `+${added} %` : '', costText].filter(Boolean).join(' · ') || historyDuration(c.duration_seconds);

  const dc = c.charge_type === 'dc';                       // AC unless the car (or the user) said DC
  const head = {
    when: escapeHtml(historyWhen(c.start_time)),
    place: `<span class="hist-tag ${dc ? 'is-dc' : 'is-ac'}">${dc ? 'DC' : 'AC'}</span>${escapeHtml(c.address || 'Ubicación de recarga')}`,
    big: `+${escapeHtml(historyNum(c.kwh_added))} kWh`,
    small: escapeHtml(figs),
  };

  const facts = historyFacts([
    ['Duración', historyDuration(c.duration_seconds)],
    ['Batería', start !== null && end !== null ? `${start} % → ${end} %` : null],
    ['A la batería', t('{p0} kWh (estimada)', {p0: historyNum(c.kwh_added)})],
    ['De la red', c.kwh_used ? `${historyNum(c.kwh_used)} kWh` : null],
    ['Tipo', dc ? 'Corriente continua (rápida)' : 'Corriente alterna'],
    ['Potencia máx.', c.max_charge_power_kw ? `${historyNum(c.max_charge_power_kw)} kW` : null],
    ['Coste', costText || null],
    ['Precio', c.effective_cost_per_kwh ? `${historyNum(c.effective_cost_per_kwh, 3)} €/kWh` : null],
    ['Tarifa', CHARGE_MODE_LABEL[c.cost_mode] || null],
    ['Fin', c.end_time ? historyWhen(c.end_time) : 'En curso'],
  ]);
  const detail = `
    ${facts}
    <div class="hist-actions">
      <button type="button" class="btn-secondary btn-sm" data-on-click="viewChargeDetails(${Number(c.id)})">Ver detalle y gráfica</button>
      <button type="button" class="btn-secondary btn-sm" data-on-click="setChargeType(${Number(c.id)}, '${dc ? 'ac' : 'dc'}')">${dc ? 'Marcar como AC' : 'Marcar como DC rápida'}</button>
    </div>`;
  const row = historyRow(head, detail);
  row.querySelector('.hist-big').classList.add('is-added');
  return row;
}

let currentChargeData = null;

let chargeMiniMapInstance = null;

let chargeMiniMapMarker = null;

async function loadLatestCharge(vin = currentVin, chargeId = null) {
  if (!vin) return;
  try {
    const url = chargeId ? `/api/charges/${chargeId}` : `/api/vehicles/${vin}/charges/latest`;
    const res = await fetch(url);
    if (!res.ok) return;
    const data = await res.json();
    currentChargeData = data;

    // Session picker
    const select = document.getElementById('chargeSessionSelect');
    if (select && data.recent_sessions && data.recent_sessions.length > 0) {
      select.innerHTML = '';
      data.recent_sessions.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.id;
        const d = serverDate(s.start_time);
        const dateStr = d.toLocaleDateString(I18n.locale, { day: '2-digit', month: 'short' });
        const timeStr = d.toLocaleTimeString(I18n.locale, { hour: '2-digit', minute: '2-digit' });
        const statusIcon = s.is_active ? '[En Curso] ' : '';
        const kwh = s.kwh_added ? ` · +${s.kwh_added} kWh` : '';
        opt.innerText = `${statusIcon}${dateStr} ${timeStr} · ${s.address || 'Carga'}${kwh}`;
        if (data.charge && s.id === data.charge.id) {
          opt.selected = true;
        }
        select.appendChild(opt);
      });
    } else if (select) {
      select.innerHTML = '<option value="">(Sin sesiones registradas)</option>';
    }

    if (!data.charge) {
      const badge = document.getElementById('chargeStatusBadge');
      if (badge) {
        badge.className = 'status-badge parked';
        badge.innerText = 'Sin Datos';
      }
      document.getElementById('chargeLocationText').innerText = 'Conecta tu vehículo para registrar sesiones';
      document.getElementById('chargeChartTimeRange').innerText = '-- : --';
      document.getElementById('kpiCost').innerText = '0.00 €';
      document.getElementById('kpiCostMode').innerText = 'Tarifa: Fija';
      document.getElementById('kpiDuration').innerText = '00m:00s';
      document.getElementById('kpiDurationDates').innerText = '-- : --';
      document.getElementById('kpiEnergyAdded').innerText = '--';
      document.getElementById('kpiEnergyUsed').innerText = '--';
      document.getElementById('kpiEfficiency').innerText = '--';
      document.getElementById('kpiEnergyNote').innerText = '';
      document.getElementById('btnEditChargeCost').hidden = true;
      window.currentChargeDetail = null;
      document.getElementById('kpiBatStart').innerText = '--%';
      document.getElementById('kpiBatAdded').innerText = '+0%';
      document.getElementById('kpiBatEnd').innerText = '--%';
      document.getElementById('kpiAvgPower').innerText = '0.0 kW';
      document.getElementById('kpiMaxPower').innerText = '0.0 kW';
      document.getElementById('kpiRangeStart').innerText = '-- km';
      document.getElementById('kpiRangeAdded').innerText = '+0 km';
      document.getElementById('kpiRangeEnd').innerText = '-- km';
      renderChargeTemporalChart([], null);
      renderChargingCurveChart([]);
      return;
    }

    const c = data.charge;

    // Status (live vs completed)
    const badge = document.getElementById('chargeStatusBadge');
    if (badge) {
      if (c.is_active) {
        badge.className = 'status-badge charging';
        badge.innerText = 'En Curso';
      } else {
        badge.className = 'status-badge parked';
        badge.innerText = 'Completada';
      }
    }

    // Location
    const locElem = document.getElementById('chargeLocationText');
    if (locElem) {
      locElem.innerText = `${c.address || 'Punto de recarga'}`;
    }

    // Times and duration
    const startDate = serverDate(c.start_time);
    const endDate = c.end_time ? serverDate(c.end_time) : new Date();
    const startTimeStr = startDate.toLocaleTimeString(I18n.locale, { hour: '2-digit', minute: '2-digit' });
    const endTimeStr = endDate.toLocaleTimeString(I18n.locale, { hour: '2-digit', minute: '2-digit' });
    
    const timeRangeElem = document.getElementById('chargeChartTimeRange');
    if (timeRangeElem) {
      const dateHeader = startDate.toLocaleDateString(I18n.locale, { day: '2-digit', month: 'short' });
      timeRangeElem.innerText = `${dateHeader} · ${startTimeStr} → ${endTimeStr}`;
    }

    const durSec = c.duration_seconds || Math.max(0, Math.floor((endDate - startDate) / 1000));
    const durMin = Math.floor(durSec / 60);
    const durRemSec = durSec % 60;
    const durHours = Math.floor(durMin / 60);
    const durFormatted = durHours > 0 
      ? `${durHours}h ${durMin % 60}m` 
      : `${durMin}m:${durRemSec < 10 ? '0' : ''}${durRemSec}s`;

    document.getElementById('kpiDuration').innerText = durFormatted;
    document.getElementById('kpiDurationDates').innerText = `${startTimeStr} - ${endTimeStr}`;

    // Cost: a charge without a tariff (outside geofences and with no default price) is shown WITHOUT a cost, not as 0,00 €
    window.currentChargeDetail = c;
    const costLabels = { fixed: 'Tarifa fija', free: 'Gratis', pvpc: 'Tarifa PVPC (horaria)', time_of_use: 'Tarifa 2.0TD', default: 'Sin tarifa (0 €/kWh)' };
    const effectiveCost = c.effective_cost_per_kwh ? ` (${c.effective_cost_per_kwh.toFixed(3).replace('.', ',')} €/kWh)` : '';
    document.getElementById('kpiCost').innerText = c.has_cost ? `${(c.charge_cost || 0).toFixed(2).replace('.', ',')} €` : '-- €';
    document.getElementById('kpiCostMode').innerText = c.has_cost
      ? `${costLabels[c.cost_mode] || c.cost_source}${effectiveCost}` : 'Sin tarifa: toca «Editar coste»';
    const editBtn = document.getElementById('btnEditChargeCost');
    editBtn.hidden = !!c.is_active;                              // a charge in progress cannot be priced yet
    editBtn.textContent = c.has_cost ? 'Editar coste' : 'Añadir coste';

    // Energy. The battery energy is ESTIMATED (% gained × usable battery); grid energy and efficiency only exist if you enter the meter kWh
    const fmtKwh = (n) => `${n.toFixed(1).replace('.', ',')} kWh`;
    document.getElementById('kpiEnergyAdded').innerText = fmtKwh(c.kwh_added || 0);
    document.getElementById('kpiEnergyUsed').innerText = c.kwh_used ? fmtKwh(c.kwh_used) : '--';
    document.getElementById('kpiEfficiency').innerText = c.efficiency_pct ? `${c.efficiency_pct.toFixed(1).replace('.', ',')} %` : '--';
    const cap = c.battery_capacity_kwh ? String(c.battery_capacity_kwh).replace('.', ',') : '19,27';
    document.getElementById('kpiEnergyNote').innerText = c.kwh_used
      ? `Eficiencia = energía a la batería ÷ energía de la red. La de la batería es estimada: ${Math.round(c.added_battery_pct || 0)} % ganado × ${cap} kWh de batería útil.`
      : t('La energía a la batería es una estimación: {p0} % ganado × {p1} kWh de batería útil. La eficiencia solo se puede calcular si indicas los kWh de tu contador (Editar coste).', {p0: Math.round(c.added_battery_pct || 0), p1: cap});

    // Battery
    document.getElementById('kpiBatStart').innerText = `${Math.round(c.start_battery_pct || 0)}%`;
    document.getElementById('kpiBatAdded').innerText = `+${Math.round(c.added_battery_pct || 0)}%`;
    document.getElementById('kpiBatEnd').innerText = `${Math.round(c.end_battery_pct || 0)}%`;

    // Power
    document.getElementById('kpiAvgPower').innerText = `${(c.avg_charge_power_kw || 0).toFixed(1)} kW`;
    document.getElementById('kpiMaxPower').innerText = `${(c.max_charge_power_kw || 0).toFixed(1)} kW`;

    // Ranges
    document.getElementById('kpiRangeStart').innerText = `${Math.round(c.start_range_km || 0)} km`;
    document.getElementById('kpiRangeAdded').innerText = `+${Math.round(c.added_range_km || 0)} km`;
    document.getElementById('kpiRangeEnd').innerText = `${Math.round(c.end_range_km || 0)} km`;

    // Leaflet mini map
    if (c.latitude && c.longitude) {
      initOrUpdateChargeMiniMap(c.latitude, c.longitude, c.address);
    }

    // Render the canvases
    renderChargeTemporalChart(data.curve || [], c);
    renderChargingCurveChart(data.curve || []);

  } catch (err) {
    console.error("Error loading the charge details:", err);
  }
}

function onChargeSessionChanged(chargeId) {
  if (chargeId) {
    loadLatestCharge(currentVin, parseInt(chargeId));
  }
}

function initOrUpdateChargeMiniMap(lat, lon, address) {
  const container = document.getElementById('chargeMiniMap');
  if (!container || !lat || !lon) return;

  const latLng = [lat, lon];
  if (!chargeMiniMapInstance) {
    chargeMiniMapInstance = L.map('chargeMiniMap', {
      zoomControl: false,
      attributionControl: false,
      dragging: false,
      scrollWheelZoom: false,
      doubleClickZoom: false,
      touchZoom: false
    }).setView(latLng, 14);

    createStandardTileLayer().addTo(chargeMiniMapInstance);
  } else {
    chargeMiniMapInstance.setView(latLng, 14);
  }

  const chargeIcon = L.divIcon({
    className: 'custom-charge-pin',
    html: `<div class="u-background-10b981_width-22px_height-22px_border-radius-50pct_border-3px-solid-ffffff_box-shadow-0-0-14px-10b981_display-flex_align-items-center_justify-content-center_color-fff">${icon('bolt', 12)}</div>`,
    iconSize: [24, 24],
    iconAnchor: [12, 12]
  });

  if (!chargeMiniMapMarker) {
    chargeMiniMapMarker = L.marker(latLng, { icon: chargeIcon }).addTo(chargeMiniMapInstance);
  } else {
    chargeMiniMapMarker.setLatLng(latLng);
    chargeMiniMapMarker.setIcon(chargeIcon);
  }

  if (address) {
    chargeMiniMapMarker.bindPopup(`<b>${escapeHtml(address)}</b>`);     // Leaflet popups are HTML; the address comes from OpenStreetMap
  }

  setTimeout(() => {
    if (chargeMiniMapInstance) chargeMiniMapInstance.invalidateSize();
  }, 150);
}

// Time chart renderer (SOC %, power kW, range km vs time)
function renderChargeTemporalChart(points, meta) {
  const canvas = document.getElementById('chargeDetailsCanvas');
  if (!canvas) return;

  const container = canvas.parentElement;
  const width = container.clientWidth;
  const height = container.clientHeight || 280;
  const dpr = window.devicePixelRatio || 1;

  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext('2d');
  ctx.resetTransform ? ctx.resetTransform() : ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(dpr, dpr);

  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  const gridColor = isLight ? 'rgba(0, 0, 0, 0.08)' : 'rgba(255, 255, 255, 0.08)';
  const textColor = isLight ? '#64748b' : '#94a3b8';

  // No points
  if (!points || points.length === 0) {
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = textColor;
    ctx.font = '500 13px Outfit, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Sin datos de telemetría registrados para esta recarga', width / 2, height / 2);
    return;
  }

  const padding = { top: 20, right: 25, bottom: 35, left: 45 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;

  // Times
  const times = points.map(p => serverDate(p.ts).getTime());
  let minT = Math.min(...times);
  let maxT = Math.max(...times);
  if (maxT === minT) maxT += 15 * 60 * 1000;

  // Value ranges
  const socs = points.map(p => p.battery_pct);
  const minSoc = Math.min(...socs);
  const maxSoc = Math.max(...socs);

  const powers = points.map(p => p.power_kw || 0);
  const minPow = Math.min(...powers);
  const maxPow = Math.max(...powers, meta ? meta.max_charge_power_kw || 0 : 10);
  const powScale = Math.max(15, maxPow * 1.1);

  const ranges = points.map(p => p.range_km || 0).filter(r => r > 0);
  const minRng = ranges.length ? Math.min(...ranges) : (minSoc * 0.8);
  const maxRng = ranges.length ? Math.max(...ranges) : (maxSoc * 0.8);
  const rngScale = Math.max(20, (maxRng - minRng) * 1.15);

  // Update the min/max legend
  const setEl = (id, val) => { const el = document.getElementById(id); if (el) el.innerText = val; };
  setEl('legSocMin', `${Math.round(minSoc)}%`);
  setEl('legSocMax', `${Math.round(maxSoc)}%`);
  setEl('legPowerMin', `${minPow.toFixed(1)} kW`);
  setEl('legPowerMax', `${maxPow.toFixed(1)} kW`);
  setEl('legRangeMin', `${Math.round(minRng)} km`);
  setEl('legRangeMax', `${Math.round(maxRng)} km`);

  function drawBaseChart(cursorX = null) {
    ctx.clearRect(0, 0, width, height);

    // Horizontal guide lines (0%, 25%, 50%, 75%, 100%)
    ctx.lineWidth = 1;
    ctx.strokeStyle = gridColor;
    ctx.fillStyle = textColor;
    ctx.font = '11px Outfit, sans-serif';
    ctx.textAlign = 'right';

    for (let pct = 0; pct <= 100; pct += 25) {
      const y = padding.top + plotH * (1 - pct / 100.0);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(padding.left + plotW, y);
      ctx.stroke();
      ctx.fillText(`${pct}%`, padding.left - 8, y + 4);
    }

    // X axis: time labels (4 to 5 ticks)
    ctx.textAlign = 'center';
    const tickCount = Math.min(5, Math.max(2, Math.floor(plotW / 85)));
    for (let i = 0; i <= tickCount; i++) {
      const t = minT + (maxT - minT) * (i / tickCount);
      const x = padding.left + plotW * (i / tickCount);
      const d = serverDate(t);
      const timeStr = d.toLocaleTimeString(I18n.locale, { hour: '2-digit', minute: '2-digit' });
      ctx.fillText(timeStr, x, height - 12);
    }

    const getX = (t) => padding.left + plotW * ((t - minT) / (maxT - minT));
    const getY_SOC = (soc) => padding.top + plotH * (1 - (soc / 100.0));
    const getY_Power = (kw) => padding.top + plotH * (1 - (kw / powScale));
    const getY_Range = (km) => padding.top + plotH * (1 - ((km - minRng) / rngScale));

    // 1. Series: power kW (yellow / gold with area)
    if (powers.some(p => p > 0)) {
      ctx.save();
      const gradPow = ctx.createLinearGradient(0, padding.top, 0, padding.top + plotH);
      gradPow.addColorStop(0, 'rgba(245, 158, 11, 0.25)');
      gradPow.addColorStop(1, 'rgba(245, 158, 11, 0.0)');

      ctx.beginPath();
      ctx.moveTo(getX(times[0]), padding.top + plotH);
      points.forEach(p => {
        const x = getX(serverDate(p.ts).getTime());
        const y = getY_Power(p.power_kw || 0);
        ctx.lineTo(x, y);
      });
      ctx.lineTo(getX(times[times.length - 1]), padding.top + plotH);
      ctx.closePath();
      ctx.fillStyle = gradPow;
      ctx.fill();

      // Line
      ctx.beginPath();
      points.forEach((p, idx) => {
        const x = getX(serverDate(p.ts).getTime());
        const y = getY_Power(p.power_kw || 0);
        if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.lineWidth = 2.2;
      ctx.strokeStyle = '#f59e0b';
      ctx.stroke();
      ctx.restore();
    }

    // 2. Series: range km (orange)
    if (ranges.length > 0) {
      ctx.save();
      ctx.beginPath();
      points.forEach((p, idx) => {
        const x = getX(serverDate(p.ts).getTime());
        const rng = p.range_km !== null && p.range_km !== undefined ? p.range_km : minRng;
        const y = getY_Range(rng);
        if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.lineWidth = 1.8;
      ctx.strokeStyle = '#f97316';
      ctx.setLineDash([4, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.restore();
    }

    // 3. Series: SOC % (emerald green with area)
    ctx.save();
    const gradSoc = ctx.createLinearGradient(0, padding.top, 0, padding.top + plotH);
    gradSoc.addColorStop(0, 'rgba(16, 185, 129, 0.28)');
    gradSoc.addColorStop(1, 'rgba(16, 185, 129, 0.02)');

    ctx.beginPath();
    ctx.moveTo(getX(times[0]), padding.top + plotH);
    points.forEach(p => {
      const x = getX(serverDate(p.ts).getTime());
      const y = getY_SOC(p.battery_pct);
      ctx.lineTo(x, y);
    });
    ctx.lineTo(getX(times[times.length - 1]), padding.top + plotH);
    ctx.closePath();
    ctx.fillStyle = gradSoc;
    ctx.fill();

    // SOC line
    ctx.beginPath();
    points.forEach((p, idx) => {
      const x = getX(serverDate(p.ts).getTime());
      const y = getY_SOC(p.battery_pct);
      if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.lineWidth = 2.6;
    ctx.strokeStyle = '#10b981';
    ctx.shadowColor = 'rgba(16, 185, 129, 0.5)';
    ctx.shadowBlur = 6;
    ctx.stroke();
    ctx.restore();

    // Interactive cursor line
    if (cursorX !== null && cursorX >= padding.left && cursorX <= padding.left + plotW) {
      ctx.save();
      ctx.lineWidth = 1;
      ctx.strokeStyle = isLight ? 'rgba(0, 0, 0, 0.35)' : 'rgba(255, 255, 255, 0.4)';
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(cursorX, padding.top);
      ctx.lineTo(cursorX, padding.top + plotH);
      ctx.stroke();
      ctx.restore();
    }
  }

  drawBaseChart();

  // Tooltip interaction
  const tooltip = document.getElementById('chargeChartTooltip');
  
  canvas.onmousemove = (e) => {
    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    if (mouseX < padding.left || mouseX > padding.left + plotW) {
      if (tooltip) tooltip.style.display = 'none';
      drawBaseChart();
      return;
    }

    const mouseT = minT + ((mouseX - padding.left) / plotW) * (maxT - minT);

    let closestPt = points[0];
    let minDiff = Math.abs(serverDate(points[0].ts).getTime() - mouseT);

    points.forEach(p => {
      const diff = Math.abs(serverDate(p.ts).getTime() - mouseT);
      if (diff < minDiff) {
        minDiff = diff;
        closestPt = p;
      }
    });

    const ptT = serverDate(closestPt.ts).getTime();
    const ptX = padding.left + plotW * ((ptT - minT) / (maxT - minT));

    drawBaseChart(ptX);

    // Draw the highlighted points
    const ptSocY = padding.top + plotH * (1 - (closestPt.battery_pct / 100.0));
    ctx.beginPath();
    ctx.arc(ptX, ptSocY, 5, 0, Math.PI * 2);
    ctx.fillStyle = '#10b981';
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 2;
    ctx.fill();
    ctx.stroke();

    if (closestPt.power_kw) {
      const ptPowY = padding.top + plotH * (1 - (closestPt.power_kw / powScale));
      ctx.beginPath();
      ctx.arc(ptX, ptPowY, 4, 0, Math.PI * 2);
      ctx.fillStyle = '#f59e0b';
      ctx.strokeStyle = '#fff';
      ctx.lineWidth = 1.5;
      ctx.fill();
      ctx.stroke();
    }

    // Position the tooltip
    if (tooltip) {
      const d = serverDate(closestPt.ts);
      const timeStr = d.toLocaleTimeString(I18n.locale, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      tooltip.innerHTML = `
        <div class="u-font-weight-700_margin-bottom-3px_border-bottom-1px-solid-rgba-255-255-255-0p1_padding-bottom-2px">${timeStr}</div>
        <div class="u-color-10b981">● Batería: <b>${Math.round(closestPt.battery_pct)}%</b></div>
        <div class="u-color-f59e0b">● Potencia: <b>${(closestPt.power_kw || 0).toFixed(1)} kW</b></div>
        ${closestPt.range_km ? `<div class="u-color-f97316">● Autonomía: <b>${Math.round(closestPt.range_km)} km</b></div>` : ''}
        ${closestPt.voltage ? `<div class="u-color-a855f7_font-size-0p75rem">● Tensión: <b>${Math.round(closestPt.voltage)} V</b></div>` : ''}
        ${closestPt.current ? `<div class="u-color-38bdf8_font-size-0p75rem">● Corriente: <b>${Math.round(closestPt.current)} A</b></div>` : ''}
      `;
      tooltip.style.display = 'block';
      let tipLeft = ptX + 15;
      if (tipLeft + 160 > width) tipLeft = ptX - 165;
      tooltip.style.left = `${tipLeft}px`;
      tooltip.style.top = `${Math.min(height - 110, Math.max(10, mouseY - 25))}px`;
    }
  };

  canvas.onmouseleave = () => {
    if (tooltip) tooltip.style.display = 'none';
    drawBaseChart();
  };
}

// Charging curve renderer (power kW vs battery %)
function renderChargingCurveChart(points) {
  const canvas = document.getElementById('chargeCurveCanvas');
  if (!canvas) return;

  const container = canvas.parentElement;
  const width = container.clientWidth;
  const height = container.clientHeight || 200;
  const dpr = window.devicePixelRatio || 1;

  canvas.width = width * dpr;
  canvas.height = height * dpr;
  const ctx = canvas.getContext('2d');
  ctx.resetTransform ? ctx.resetTransform() : ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(dpr, dpr);

  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  const gridColor = isLight ? 'rgba(0, 0, 0, 0.08)' : 'rgba(255, 255, 255, 0.08)';
  const textColor = isLight ? '#64748b' : '#94a3b8';

  if (!points || points.length === 0) {
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = textColor;
    ctx.font = '500 13px Outfit, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillText('Sin datos de curva de potencia para esta sesión', width / 2, height / 2);
    return;
  }

  // Filter and sort points by SOC
  const curvePts = points
    .filter(p => p.battery_pct !== null && p.battery_pct !== undefined)
    .sort((a, b) => a.battery_pct - b.battery_pct);

  const padding = { top: 15, right: 25, bottom: 30, left: 45 };
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;

  const minSoc = Math.max(0, Math.floor(Math.min(...curvePts.map(p => p.battery_pct)) / 10) * 10);
  const maxSoc = Math.min(100, Math.ceil(Math.max(...curvePts.map(p => p.battery_pct)) / 10) * 10);
  const socSpan = Math.max(10, maxSoc - minSoc);

  const maxKw = Math.max(15, Math.ceil(Math.max(...curvePts.map(p => p.power_kw || 0)) * 1.15));

  function drawCurveChart(hoverIdx = null) {
    ctx.clearRect(0, 0, width, height);

    // Y axis: power guides kW (0, 25%, 50%, 75%, 100%)
    ctx.lineWidth = 1;
    ctx.strokeStyle = gridColor;
    ctx.fillStyle = textColor;
    ctx.font = '11px Outfit, sans-serif';
    ctx.textAlign = 'right';

    for (let i = 0; i <= 4; i++) {
      const frac = i / 4.0;
      const kwVal = Math.round(maxKw * frac);
      const y = padding.top + plotH * (1 - frac);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(padding.left + plotW, y);
      ctx.stroke();
      ctx.fillText(`${kwVal} kW`, padding.left - 8, y + 4);
    }

    // X axis: battery %
    ctx.textAlign = 'center';
    const socSteps = Math.min(6, Math.max(3, Math.floor(socSpan / 10)));
    for (let i = 0; i <= socSteps; i++) {
      const s = minSoc + Math.round((socSpan * (i / socSteps)));
      const x = padding.left + plotW * ((s - minSoc) / socSpan);
      ctx.fillText(`${s}%`, x, height - 10);
    }

    const getX = (soc) => padding.left + plotW * ((soc - minSoc) / socSpan);
    const getY = (kw) => padding.top + plotH * (1 - ((kw || 0) / maxKw));

    // Draw the curve area (electric cyan)
    ctx.save();
    const grad = ctx.createLinearGradient(0, padding.top, 0, padding.top + plotH);
    grad.addColorStop(0, themeAccent(0.25));
    grad.addColorStop(1, themeAccent(0.01));

    ctx.beginPath();
    ctx.moveTo(getX(curvePts[0].battery_pct), padding.top + plotH);
    curvePts.forEach(p => {
      ctx.lineTo(getX(p.battery_pct), getY(p.power_kw));
    });
    ctx.lineTo(getX(curvePts[curvePts.length - 1].battery_pct), padding.top + plotH);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();

    // Curve line
    ctx.beginPath();
    curvePts.forEach((p, idx) => {
      const x = getX(p.battery_pct);
      const y = getY(p.power_kw);
      if (idx === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.lineWidth = 2.4;
    ctx.strokeStyle = themeAccent();
    ctx.shadowColor = themeAccent(isLight ? 0.5 : 0.6);
    ctx.shadowBlur = 6;
    ctx.stroke();
    ctx.restore();

    // Points
    curvePts.forEach((p, idx) => {
      const x = getX(p.battery_pct);
      const y = getY(p.power_kw);
      const isHover = (idx === hoverIdx);
      ctx.beginPath();
      ctx.arc(x, y, isHover ? 6 : 3.5, 0, Math.PI * 2);
      ctx.fillStyle = isHover ? '#fff' : themeAccent();
      ctx.strokeStyle = isLight ? '#006978' : '#ffffff';
      ctx.lineWidth = isHover ? 2.5 : 1.5;
      ctx.fill();
      ctx.stroke();
    });
  }

  drawCurveChart();

  const tooltip = document.getElementById('chargeCurveTooltip');
  canvas.onmousemove = (e) => {
    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    if (mouseX < padding.left || mouseX > padding.left + plotW) {
      if (tooltip) tooltip.style.display = 'none';
      drawCurveChart();
      return;
    }

    const mouseSoc = minSoc + ((mouseX - padding.left) / plotW) * socSpan;

    let closestIdx = 0;
    let minDiff = Math.abs(curvePts[0].battery_pct - mouseSoc);
    curvePts.forEach((p, i) => {
      const diff = Math.abs(p.battery_pct - mouseSoc);
      if (diff < minDiff) {
        minDiff = diff;
        closestIdx = i;
      }
    });

    drawCurveChart(closestIdx);

    const pt = curvePts[closestIdx];
    const ptX = padding.left + plotW * ((pt.battery_pct - minSoc) / socSpan);

    if (tooltip) {
      tooltip.innerHTML = `
        <div class="u-font-weight-700_color-var-text-primary_margin-bottom-2px">${t('Batería: {p0}%', {p0: Math.round(pt.battery_pct)})}</div>
        <div class="u-color-var-primary">● Potencia: <b>${(pt.power_kw || 0).toFixed(1)} kW</b></div>
      `;
      tooltip.style.display = 'block';
      let tipLeft = ptX + 15;
      if (tipLeft + 120 > width) tipLeft = ptX - 125;
      tooltip.style.left = `${tipLeft}px`;
      tooltip.style.top = `${Math.min(height - 60, Math.max(10, mouseY - 25))}px`;
    }
  };

  canvas.onmouseleave = () => {
    if (tooltip) tooltip.style.display = 'none';
    drawCurveChart();
  };
}

function redrawChargeCharts() {
  if (currentChargeData) {
    renderChargeTemporalChart(currentChargeData.curve || [], currentChargeData.charge);
    renderChargingCurveChart(currentChargeData.curve || []);
  }
}

window.addEventListener('resize', () => {
  if (window._chargeResizeTimeout) clearTimeout(window._chargeResizeTimeout);
  window._chargeResizeTimeout = setTimeout(redrawChargeCharts, 150);
});

// Query the live PVPC electricity price and the 2.0TD period
async function loadPvpcLiveBanner() {
  const badge = document.getElementById('geofenceLivePeriodBadge');
  const desc = document.getElementById('geofenceLivePvpcDesc');
  const priceElem = document.getElementById('geofenceLivePvpcPrice');
  if (!badge || !desc || !priceElem) return;

  try {
    const res = await fetch('/api/geofences/pvpc/current');
    if (res.ok) {
      const data = await res.json();
      const period = data.period || 'valle';
      
      let badgeStyle = "background: rgba(0, 230, 118, 0.15); color: var(--accent-green); border: 1px solid rgba(0, 230, 118, 0.3);";
      if (period === 'llano') {
        badgeStyle = "background: rgba(255, 179, 0, 0.15); color: #ffca28; border: 1px solid rgba(255, 179, 0, 0.3);";
      } else if (period === 'punta') {
        badgeStyle = "background: rgba(255, 23, 68, 0.15); color: var(--accent-red); border: 1px solid rgba(255, 23, 68, 0.3);";
      }

      badge.style.cssText = `font-size: 0.75rem; padding: 0.2rem 0.6rem; border-radius: 20px; font-weight: 700; ${badgeStyle}`;   // CSSOM: allowed without 'unsafe-inline'
      badge.textContent = `Tramo ${period.toUpperCase()}`;
      desc.innerText = `${data.period_description} • Operador ESIOS / Red Eléctrica de España`;
      priceElem.innerText = `${(data.pvpc_price_kwh || 0).toFixed(4)} €/kWh`;
    }
  } catch (err) {
    console.warn("Live PVPC query failed:", err);
  }
}


// ───────────────────────── Edit a charge's cost and energy ─────────────────────────
function openChargeCostModal() {
  const c = window.currentChargeDetail;
  if (!c) return;
  document.getElementById('chargeCostIntro').textContent =
    t('Carga del {p0}: {p1} kWh a la batería (estimados).', {p0: serverDate(c.start_time).toLocaleDateString(I18n.locale, { day: '2-digit', month: 'long' }), p1: String(c.kwh_added).replace('.', ',')});
  const manual = c.cost_mode === 'manual';
  document.querySelector('input[name="chargeCostMode"][value="price"]').checked = true;
  setChargeCostMode('price');
  document.getElementById('chargeCostPrice').value = c.effective_cost_per_kwh ?? '';
  document.getElementById('chargeCostTotal').value = manual ? c.charge_cost : '';
  document.getElementById('chargeCostMeter').value = c.kwh_used ?? '';
  document.getElementById('chargeCostModal').classList.add('open');
}

function closeChargeCostModal() {
  document.getElementById('chargeCostModal').classList.remove('open');
}

function setChargeCostMode(mode) {
  document.getElementById('chargeCostPriceBox').hidden = mode !== 'price';
  document.getElementById('chargeCostTotalBox').hidden = mode !== 'total';
}

async function saveChargeCost() {
  const c = window.currentChargeDetail;
  if (!c) return;
  const mode = document.querySelector('input[name="chargeCostMode"]:checked').value;
  const num = (id) => { const v = parseFloat(document.getElementById(id).value.replace(',', '.')); return isNaN(v) ? null : v; };
  const body = {};
  if (mode === 'price' && num('chargeCostPrice') !== null) body.price_per_kwh = num('chargeCostPrice');
  if (mode === 'total' && num('chargeCostTotal') !== null) body.total_cost = num('chargeCostTotal');
  if (num('chargeCostMeter') !== null) body.kwh_meter = num('chargeCostMeter');
  if (!Object.keys(body).length) { showToast('Indica el precio, el coste total o los kWh del contador.', 'warning'); return; }
  try {
    const res = await fetch(`/api/charges/${c.id}/cost`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(' · ') : (data.detail || res.status));
    closeChargeCostModal();
    showToast('Coste de la carga guardado', 'success');
    loadLatestCharge(); loadCharges();
    if (typeof loadCosts === 'function') loadCosts();
  } catch (e) {
    showToast(t('No se pudo guardar: {p0}', {p0: e.message || e}), 'error', 6000);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('input[name="chargeCostMode"]').forEach((r) => r.addEventListener('change', () => setChargeCostMode(r.value)));
});


/** AC or DC by hand (the detector marks DC when the car reports the fast gun or a power beyond AC; everything else is AC). */
async function setChargeType(chargeId, chargeType) {
  try {
    const res = await fetch(`/api/charges/${chargeId}/type`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ charge_type: chargeType }) });
    if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || res.status); }
    showToast(chargeType === 'dc' ? 'Carga marcada como DC rápida' : 'Carga marcada como AC', 'success');
    if (typeof loadCharges === 'function') loadCharges();
  } catch (e) {
    showToast(t('No se pudo cambiar el tipo: {p0}', {p0: e.message || e}), 'error', 6000);
  }
}
