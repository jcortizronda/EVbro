/* EVbro — Telemetry rendering (home tab, car status, tyres).
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

// Apply telemetry to the interface
function applyTelemetry(data) {
  if (!data) return;
  latestTelemetryData = data;
  if (data.vin && !currentVin) {
    currentVin = data.vin;
  }
  updateVehicleHeaderInfo();
  syncTargetTempFromCar(data);

  // 1. Circular battery gauge
  const pct = data.battery_level !== undefined && data.battery_level !== null ? Math.round(data.battery_level) : null;
  const pctElem = document.getElementById('batteryPct');
  const circle = document.getElementById('batteryCircleBar');

  if (pct !== null) {
    pctElem.innerText = `${pct}%`;
    const totalLength = 427.25;
    const offset = totalLength - (pct / 100.0) * totalLength;
    circle.style.strokeDashoffset = offset;

    if (pct > 40) {
      circle.style.stroke = "var(--primary)";
    } else if (pct > 20) {
      circle.style.stroke = "var(--accent-amber)";
    } else {
      circle.style.stroke = "var(--accent-red)";
    }
  }

  // 2. Status (badge)
  const badge = document.getElementById('vehicleStatusBadge');
  if (badge) badge.hidden = false;                        // hidden until there is a reading: no state is made up
  const state = (data.state || 'parked').toLowerCase();
  // A car whose manager has no Ebro account connected (or whose session the cloud no longer accepts) gets no new readings: say so
  // instead of showing a stale «Dormido» (api/routes_vehicles.py, `account_state`)
  const vehicle = (typeof currentVehicle === 'function') ? currentVehicle() : null;
  const accountState = vehicle ? vehicle.account_state : null;
  const unreadable = accountState === 'disconnected' || accountState === 'expired';
  badge.className = `status-badge ${unreadable ? 'asleep' : state}`;
  const stateLabels = {
    'driving': 'En Marcha',
    'ready': 'Encendido',
    'charging': 'Cargando',
    'plugged': 'Enchufado',
    'parked': 'Estacionado',
    'asleep': 'Dormido'
  };
  badge.innerText = unreadable ? (accountState === 'expired' ? 'Sesión caducada' : 'Sin cuenta Ebro') : (stateLabels[state] || state);

  // Honest freshness of the status
  const freshElem = document.getElementById('freshnessText');
  if (freshElem) {
    // The text comes from the server with a coloured circle in front (level); here it is drawn as a CSS dot, no emoji
    const fresh = data.freshness_text;
    if (unreadable) {
      freshElem.textContent = accountState === 'expired' ? 'La nube rechaza la sesión: vuelve a conectar la cuenta Ebro' : 'Sin lecturas nuevas: conecta la cuenta Ebro en Ajustes';
      freshElem.dataset.level = 'warn';
    } else {
      freshElem.textContent = fresh ? stripEmoji(fresh) : (data.online_status ? 'En vivo' : 'En reposo · Nube');
      const lvl = fresh ? statusLevel(fresh) : 'neutral';
      freshElem.dataset.level = lvl !== 'neutral' ? lvl : (data.online_status ? 'ok' : 'warn');
    }
  }

  // Cable and active charging status bar
  const chargeBar = document.getElementById('chargeActiveBar');
  const chargeKw = document.getElementById('chargePowerKw');
  const chargeCableStatus = document.getElementById('chargeCableStatus');
  const chargeCableIcon = document.getElementById('chargeCableIcon');
  const chargeStateBadge = document.getElementById('chargeStateBadge');
  const chargeVoltCurr = document.getElementById('chargeVoltageCurrent');
  const chargeRemain = document.getElementById('chargeRemainTime');

  // A charge needs the cable: on the road the engine also charges the battery and the car reports it as charging
  const charging = !!data.is_charging && !!data.cable_connected;
  const isPluggedOrCharging = charging || state === 'charging' || data.cable_connected || state === 'plugged';
  if (isPluggedOrCharging) {
    if (chargeBar) chargeBar.style.display = 'flex';
    if (chargeCableStatus) {
      if (data.fast_charging) {
        chargeCableStatus.innerText = "Toma CCS2 DC Rápida Conectada";
      } else if (data.cable_connected) {
        chargeCableStatus.innerText = "Cable Mennekes (AC) Conectado";
      } else {
        chargeCableStatus.innerText = "Cable Conectado";
      }
    }
    if (chargeCableIcon) {
      chargeCableIcon.innerHTML = icon(data.fast_charging ? 'bolt' : 'plug', 22);
    }
    if (chargeStateBadge) {
      chargeStateBadge.innerText = data.charge_state_desc || (charging ? "Cargando" : "Enchufado");
      chargeStateBadge.style.color = charging ? "var(--accent-green)" : "var(--primary)";
      chargeStateBadge.style.background = charging ? "rgba(0,230,118,0.18)" : "rgba(0,229,255,0.18)";
    }
    if (chargeKw) {
      chargeKw.innerText = data.charge_power_kw ? `${historyNum(data.charge_power_kw)} kW` : (charging ? "Activa" : `${historyNum(0)} kW`);
    }
    if (chargeVoltCurr) {
      const v = data.battery_voltage ? `${Math.round(data.battery_voltage)} V` : null;
      const a = data.battery_current !== undefined && data.battery_current !== null ? `${data.battery_current} A` : null;
      if (v && a) {
        chargeVoltCurr.innerText = `${v} · ${a}`;
      } else if (v) {
        chargeVoltCurr.innerText = v;
      } else {
        chargeVoltCurr.innerText = "-- V · -- A";
      }
    }
    if (chargeRemain) {
      // the time left and, so nobody has to add it up, the clock time it ends at
      const min = Number(data.remaining_charge_time_min);
      chargeRemain.innerText = min > 0 ? `${chargeDuration(min)} · ${chargeClock(min)}` : '-- min';
    }

    if (!window._lastChargeRefresh || Date.now() - window._lastChargeRefresh > 20000) {
      window._lastChargeRefresh = Date.now();
      if (typeof loadLatestCharge === 'function') loadLatestCharge(currentVin);
    }
  } else {
    if (chargeBar) chargeBar.style.display = 'none';
  }

  // 3. Ranges and odometers (full PHEV)
  if (data.electric_range_km !== null && data.electric_range_km !== undefined) {
    const el = document.getElementById('electricRange');
    if (el) el.innerText = `${Math.round(data.electric_range_km)}`;
  }
  if (data.fuel_range_km !== null && data.fuel_range_km !== undefined) {
    const el = document.getElementById('fuelRange');
    if (el) el.innerText = `${Math.round(data.fuel_range_km)}`;
  }
  if (data.total_range_km !== null && data.total_range_km !== undefined) {
    const el = document.getElementById('totalRange');
    if (el) el.innerText = `${Math.round(data.total_range_km)} km`;
  }
  if (data.odometer_km !== null && data.odometer_km !== undefined) {
    const el = document.getElementById('odometer');
    if (el) el.innerText = `${Math.round(data.odometer_km).toLocaleString(I18n.locale)} km`;
  }
  if (data.ev_odometer_km !== null && data.ev_odometer_km !== undefined) {
    const el = document.getElementById('evOdometer');
    if (el) el.innerText = `${Math.round(data.ev_odometer_km).toLocaleString(I18n.locale)} km`;
  }
  if (data.fuel_level !== null && data.fuel_level !== undefined) {
    const el = document.getElementById('fuelLevelPct');
    if (el) el.innerText = `${Math.round(data.fuel_level)}%`;
  }
  if (data.speed_kmh !== null && data.speed_kmh !== undefined) {
    const el = document.getElementById('speed');
    if (el) el.innerText = `${Math.round(data.speed_kmh)} km/h`;
  }

  // 4. Body & locks
  updateBodyItem('itemLock', data.is_locked, 'Bloqueado', 'Desbloqueado');   // null = no data
  updateBodyItem('itemEngine', !data.engine_state, 'Apagado', 'Encendido', data.engine_state);
  updateBodyItem('itemCable', data.cable_connected, 'Conectado', 'Desconectado');
  updateBodyItem('itemFastGun', data.fast_charging, 'CCS2 Rápida', 'Libre');
  
  const unknown = (v) => v === null || v === undefined;
  const doors = data.doors || {};
  const anyDoorOpen = anyOpen([doors.fl, doors.fr, doors.rl, doors.rr]);
  updateBodyItem('itemDoors', unknown(anyDoorOpen) ? null : !anyDoorOpen, 'Cerradas', 'Abierta', anyDoorOpen);
  updateBodyItem('itemTrunk', unknown(doors.trunk) ? null : !doors.trunk, 'Cerrado', 'Abierto', doors.trunk);
  updateBodyItem('itemHood', unknown(doors.hood) ? null : !doors.hood, 'Cerrado', 'Abierto', doors.hood);

  const windows = data.windows || {};
  const anyWindowOpen = anyOpen([windows.fl, windows.fr, windows.rl, windows.rr]);
  updateBodyItem('itemWindows', unknown(anyWindowOpen) ? null : !anyWindowOpen, 'Cerradas', 'Abierta', anyWindowOpen);
  updateBodyItem('itemSunroof', unknown(windows.sunroof) ? null : !windows.sunroof, 'Cerrado', 'Abierto', windows.sunroof);

  // 5. TPMS (pressure and temperature of the 4 tyres)
  const tpms = data.tyre_pressures || {};
  setTyre('tpmsFL', tpms.fl);
  setTyre('tpmsFR', tpms.fr);
  setTyre('tpmsRL', tpms.rl);
  setTyre('tpmsRR', tpms.rr);

  const tpmsTemps = data.tyre_temps || {};
  setTyreTemp('tpmsTempFL', tpmsTemps.fl);
  setTyreTemp('tpmsTempFR', tpmsTemps.fr);
  setTyreTemp('tpmsTempRL', tpmsTemps.rl);
  setTyreTemp('tpmsTempRR', tpmsTemps.rr);

  // 6. Map
  if (data.latitude && data.longitude) {
    updateMapPosition(data.latitude, data.longitude, data.address);
  }

  // 7. Sync the native-style mobile UI (Inicio, Energía, Clima)
  const batteryPctPill = document.getElementById('batteryPctPill');
  const batteryTrackFill = document.getElementById('batteryTrackFill');
  const energyBatteryPct = document.getElementById('energyBatteryPct');
  const energyProgressBar = document.getElementById('energyProgressBar');
  if (pct !== null) {
    if (batteryPctPill) batteryPctPill.innerText = `${pct}%`;
    if (batteryTrackFill) batteryTrackFill.style.width = `${Math.min(100, Math.max(0, pct))}%`;
    if (energyBatteryPct) energyBatteryPct.innerText = `${pct}`;
    if (energyProgressBar) energyProgressBar.style.width = `${Math.min(100, Math.max(0, pct))}%`;
  }

  const fuelPctPill = document.getElementById('fuelPctPill');
  const fuelTrackFill = document.getElementById('fuelTrackFill');
  if (data.fuel_level !== null && data.fuel_level !== undefined) {
    const fPct = Math.round(data.fuel_level);
    if (fuelPctPill) fuelPctPill.innerText = `${fPct}%`;
    if (fuelTrackFill) fuelTrackFill.style.width = `${Math.min(100, Math.max(0, fPct))}%`;
  }

  // Home tab access tiles (lock, boot, windows, sunroof): home.js, with their icon by state
  if (typeof renderHome === 'function') renderHome(data);
  if (typeof renderCarFigure === 'function') renderCarFigure(data);

  // Energía KPIs
  // Time left to 100 % (reported by the car): only meaningful while charging
  const remainLine = document.getElementById('energyRemainLine');
  if (remainLine) {
    const min = Number(data.remaining_charge_time_min);
    remainLine.hidden = !(charging && min > 0);
    if (!remainLine.hidden) {
      document.getElementById('energyRemainTime').innerText = t('{p0}, hasta las {p1}', {p0: chargeDuration(min), p1: chargeClock(min)});
    }
  }
  // With a battery target below 100 %: how long to reach it, at today's pace (an estimate: the power is taken as constant)
  const targetLine = document.getElementById('energyTargetEtaLine');
  if (targetLine) {
    const eta = charging ? chargeTargetEta(data) : null;
    targetLine.hidden = !eta;
    if (eta) {
      document.getElementById('energyTargetEtaText').innerText = t('Al objetivo ({p0} %): unos {p1}, hacia las {p2}', {p0: eta.target, p1: chargeDuration(eta.min), p2: chargeClock(eta.min)});
    }
  }
  const energyPower = document.getElementById('energyPowerKw');
  if (energyPower) {
    energyPower.innerText = data.charge_power_kw ? `${historyNum(data.charge_power_kw)} kW` : (charging ? 'Activa' : `${historyNum(0)} kW`);
  }
  refreshEvConsumptionKpi();
  const energyHeroBadge = document.getElementById('chargeHeroStateBadge');
  if (energyHeroBadge) {
    energyHeroBadge.innerText = charging ? "Cargando" : (data.cable_connected ? "Conectado" : "Desconectado");
    const tone = charging ? ['rgba(48,209,88,0.18)', 'var(--accent-green)']
      : (data.cable_connected ? ['rgba(41,151,255,0.18)', 'var(--primary)'] : ['rgba(128,128,128,0.18)', 'var(--text-secondary)']);
    energyHeroBadge.style.background = tone[0];
    energyHeroBadge.style.color = tone[1];
  }

  if (typeof renderComfort === 'function') renderComfort();
}

/** «2 h 10 min», «45 min». */
function chargeDuration(min) {
  const m = Math.max(0, Math.round(min));
  if (m < 60) return `${m} min`;
  return m % 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${Math.floor(m / 60)} h`;
}

/** The clock time `min` minutes from now («18:45»), «mañana 06:10» when it is past midnight. */
function chargeClock(min) {
  const end = new Date(Date.now() + Math.round(min) * 60000);
  const hhmm = end.toLocaleTimeString((window.I18n && I18n.locale) || 'es-ES', { hour: '2-digit', minute: '2-digit' });
  const days = Math.round((new Date(end).setHours(0, 0, 0, 0) - new Date().setHours(0, 0, 0, 0)) / 86400000);
  return days === 0 ? hhmm : days === 1 ? t('mañana {p0}', {p0: hhmm}) : end.toLocaleString((window.I18n && I18n.locale) || 'es-ES', { weekday: 'short', hour: '2-digit', minute: '2-digit' });
}

/** Minutes to the battery target set in EVbro (below 100 %), or null. The car's own time to 100 % shared out in proportion (so its
 * pace is kept); without it, the battery's capacity at the present power. Both take the power as constant: an estimate. */
function chargeTargetEta(data) {
  const target = window.ChargeControl && ChargeControl.target;
  const soc = Number(data.battery_level);
  if (!target || target >= 100 || !(soc >= 0) || soc >= target) return null;
  const toFull = Number(data.remaining_charge_time_min);
  if (toFull > 0 && soc < 100) return { target, min: toFull * (target - soc) / (100 - soc) };
  const v = typeof currentVehicle === 'function' ? currentVehicle() : null;
  const kw = Number(data.charge_power_kw), kwh = Number(v && v.battery_capacity_kwh);
  if (kw > 0.3 && kwh > 0) return { target, min: ((target - soc) / 100) * kwh / kw * 60 };
  return null;
}

/** Text of an open/closed state; with `null`/`undefined` (the car has not reported yet) it does NOT guess. */
/** «Consumo EV · 30 días» of the Energía tab: kWh/100 km of the last 30 days of drives (GET …/stats?period=30d, `totals.kwh_per_100km`:
 * drives of ≥ 2 km with measured energy). Fetched at most every 10 minutes per car; it is history, not live telemetry. */
let _evKpi = { vin: null, at: 0 };
async function refreshEvConsumptionKpi() {
  const box = document.getElementById('energyEvConsumption');
  if (!box || !currentVin || featureOff('stats')) return;
  const stale = _evKpi.vin !== currentVin || Date.now() - _evKpi.at > 10 * 60 * 1000;
  if (!stale) return;
  _evKpi = { vin: currentVin, at: Date.now() };
  try {
    const res = await fetch(`/api/vehicles/${currentVin}/stats?period=30d`);
    if (!res.ok) throw new Error(res.status);
    const v = (await res.json()).totals?.kwh_per_100km;
    box.innerText = v === null || v === undefined ? 'Sin viajes' : `${v.toLocaleString(I18n.locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} kWh/100 km`;
  } catch (e) {
    _evKpi.at = 0;                                   // try again on the next telemetry
  }
}

function openClosedText(value, openText, closedText) {
  return value === null || value === undefined ? 'Sin datos' : (value ? openText : closedText);
}

/** Is any open? `null` if none of the values is known. */
function anyOpen(values) {
  return values.every((v) => v === null || v === undefined) ? null : values.some(Boolean);
}

function updateBodyItem(id, isOk, okText, warnText, hasWarning = false) {
  const el = document.getElementById(id);
  if (!el) return;
  if (isOk === null || isOk === undefined) {
    el.className = 'body-item';
    el.title = 'Sin datos';
  } else if (isOk && !hasWarning) {
    el.className = 'body-item active';
    el.title = okText;
  } else {
    el.className = 'body-item warning';
    el.title = warnText;
  }
}

function setTyre(presId, val) {
  const pElem = document.getElementById(presId);
  if (pElem && val !== null && val !== undefined) {
    const num = Number(val);
    const bar = num > 50 ? historyNum(num / 100.0, 2) : historyNum(num, 2);
    pElem.innerText = `${bar} bar`;
  }
}

function setTyreTemp(tempId, tempC) {
  const tElem = document.getElementById(tempId);
  if (tElem) {
    if (tempC !== null && tempC !== undefined) {
      tElem.innerText = `${Math.round(tempC)} °C`;
    } else {
      tElem.innerText = `-- °C`;
    }
  }
}

// Climate control and native controls
function adjustTargetTemp(delta) {
  currentTargetTemp = Math.min(30.0, Math.max(16.0, currentTargetTemp + delta));
  syncClimateUI();
}

/** The climate card starts at the temperature set in the car. Only a NEW value from the car replaces it, so a temperature the user
 *  has just chosen is not overwritten by the next reading. */
function syncTargetTempFromCar(data) {
  const temp = Number(data.target_temp_left || data.target_temp);
  if (!Number.isFinite(temp) || temp < 16 || temp > 30 || temp === lastCarTargetTemp) return;
  lastCarTargetTemp = temp;
  currentTargetTemp = temp;
  syncClimateUI();
}

// onClimateSliderChange, toggleClimatePower and the presets live in features/comfort.js
function syncClimateUI() {
  const display = document.getElementById('targetTempDisplay');
  if (display) display.innerText = `${historyNum(currentTargetTemp, 1)}°C`;
  const dial = document.getElementById('climateDialTemp');
  if (dial) dial.innerText = `${Math.round(currentTargetTemp)}°C`;
  const slider = document.getElementById('climateSlider');
  if (slider) slider.value = currentTargetTemp;
}

function onClimateSliderInput(val) {
  currentTargetTemp = parseFloat(val);
  const dial = document.getElementById('climateDialTemp');
  if (dial) dial.innerText = `${Math.round(currentTargetTemp)}°C`;
  const display = document.getElementById('targetTempDisplay');
  if (display) display.innerText = `${historyNum(currentTargetTemp, 1)}°C`;
}
