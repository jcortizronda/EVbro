/* EVBro — Navegación: pestañas, subpestañas y acordeones.
 * (Script clásico: comparte el ámbito global con los demás ficheros; ver el orden de carga en templates/index.html.)
 */

// Tab switcher: the desktop sidebar (`.side-item[data-tab]`) and the touch bottom dock (`#mobTab…`) share the same sections
function switchTab(tabName) {
  window.currentTabName = tabName;
  document.querySelectorAll('.tab-btn, .mobile-nav-item, .dock-item').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.side-item[data-tab]').forEach(btn => btn.classList.toggle('active', btn.dataset.tab === tabName));
  document.querySelectorAll('.tab-content').forEach(tab => tab.classList.remove('active'));

  const mobBtnId = 'mobTab' + tabName.charAt(0).toUpperCase() + tabName.slice(1);
  const tabId = 'tab' + tabName.charAt(0).toUpperCase() + tabName.slice(1);

  const mobBtn = document.getElementById(mobBtnId);
  const tab = document.getElementById(tabId);
  if (mobBtn) mobBtn.classList.add('active');
  if (tab) tab.classList.add('active');

  // Si estamos en un submenú en móvil, resaltar el botón Más si no hay botón propio
  if (['drives', 'maintenance', 'geofences', 'settings', 'costs', 'expenses', 'refuels', 'timeline', 'stats', 'vehicle'].includes(tabName)) {
    const mobMore = document.getElementById('mobTabMore');
    if (mobMore && !mobBtn) mobMore.classList.add('active');
  }

  if (tabName === 'overview') {
    setTimeout(() => {
      if (liveMap) liveMap.invalidateSize();
    }, 200);
    if (typeof refreshGpsPosition === 'function') refreshGpsPosition(true);
  } else if (tabName === 'drives') {
    loadDrives();
    if (window.Journeys) Journeys.load();                  // «Viajes largos»
    loadGeofencesForFilter();
    if (typeof loadPlaces === 'function') loadPlaces();
  } else if (tabName === 'charges') {
    loadLatestCharge();
    loadCharges();
    setTimeout(() => {
      if (chargeMiniMapInstance) chargeMiniMapInstance.invalidateSize();
      if (typeof redrawChargeCharts === 'function') redrawChargeCharts();
    }, 200);
  } else if (tabName === 'climate') {
    if (typeof syncClimateUI === 'function') syncClimateUI();
    if (window.ClimateSchedule) ClimateSchedule.load();
  } else if (tabName === 'expenses') {
    if (typeof loadExpenses === 'function') loadExpenses();
  } else if (tabName === 'costs') {
    if (typeof loadCosts === 'function') loadCosts();
  } else if (tabName === 'refuels') {
    if (typeof loadRefuels === 'function') loadRefuels();
  } else if (tabName === 'timeline') {
    if (typeof loadTimeline === 'function') loadTimeline(true);
  } else if (tabName === 'stats') {
    if (window.Stats) Stats.load();
  } else if (tabName === 'maintenance') {
    loadMaintenance();
    loadMaintenanceLogs();
  } else if (tabName === 'geofences') {
    loadGeofences();
    if (typeof loadGeofenceEvents === 'function') loadGeofenceEvents();
    loadPvpcLiveBanner();
    setTimeout(() => {
      if (typeof initGeofencesOverviewMap === 'function' && !geofencesOverviewMapInstance) {
        initGeofencesOverviewMap();
      } else if (geofencesOverviewMapInstance) {
        geofencesOverviewMapInstance.invalidateSize();
      }
    }, 200);
  } else if (tabName === 'vehicle') {                      // «Coches»: the car picker and the car sheet
    if (typeof renderOwnCars === 'function') renderOwnCars();
    if (typeof loadVehicleProfile === 'function') loadVehicleProfile();
  } else if (tabName === 'settings') {
    checkAccountStatus();
    loadNotificationConfig();
    updateThemeSettingsUI();
    switchSettingsSubTab(window.currentSettingsSubTab || 'account');   // (re)load the panel on show: MQTT, Traccar, passkeys…
  }
}

/**
 * Control de navegación de subpestañas dentro del módulo de Configuración/Ajustes.
 * Aplica diseño desacoplado y carga perezosa bajo demanda de cada sección.
 * @param {'account'|'accounts'|'notifs'|'theme'|'system'} subTabName
 */
function switchSettingsSubTab(subTabName) {
  window.currentSettingsSubTab = subTabName;
  const validSubTabs = ['account', 'accounts', 'notifs', 'theme', 'system'];
  if (!validSubTabs.includes(subTabName)) {
    console.warn(`[Ajustes] Subpestaña no reconocida: ${subTabName}`);
    return;
  }

  // Desactivar botones de submenú
  document.querySelectorAll('.settings-sub-nav .sub-nav-btn').forEach(btn => btn.classList.remove('active'));

  // Ocultar todos los subpaneles
  validSubTabs.forEach(tab => {
    const panelId = 'settingsSubTab' + tab.charAt(0).toUpperCase() + tab.slice(1);
    const panel = document.getElementById(panelId);
    if (panel) panel.classList.remove('active');
  });

  // Activar botón y panel seleccionados
  const btnId = 'btnSettingsSub' + subTabName.charAt(0).toUpperCase() + subTabName.slice(1);
  const panelId = 'settingsSubTab' + subTabName.charAt(0).toUpperCase() + subTabName.slice(1);

  const btn = document.getElementById(btnId);
  const panel = document.getElementById(panelId);
  if (btn) btn.classList.add('active');
  if (panel) panel.classList.add('active');

  // Carga modular reactiva
  if (subTabName === 'account') {
    if (typeof checkAccountStatus === 'function') checkAccountStatus();
    if (window.EbroPasskeys) EbroPasskeys.refresh();        // «Acceso con huella» lives here
    if (typeof loadStorageUsage === 'function') loadStorageUsage();   // «Espacio usado» for documents, invoices and photos
    if (window.MqttHa) MqttHa.load();                        // the manager's own broker for Home Assistant
  } else if (subTabName === 'accounts') {
    if (typeof loadAccounts === 'function') loadAccounts();
  } else if (subTabName === 'notifs') {
    if (typeof loadNotificationConfig === 'function') loadNotificationConfig();
  } else if (subTabName === 'theme') {
    if (typeof updateThemeSettingsUI === 'function') updateThemeSettingsUI();
  } else if (subTabName === 'system') {
    if (typeof loadCarChannelStatus === 'function') loadCarChannelStatus();
    if (window.Traccar) Traccar.load();                 // «Posición por GPS (Traccar)» of the chosen car
    loadAppVersion();
  }
}

/** Ajustes → Sistema: the running version and, when the image was built with it, the commit. Asked once. */
let appVersionLoaded = false;
async function loadAppVersion() {
  if (appVersionLoaded) return;
  try {
    const res = await fetch('/api/version');
    if (!res.ok) return;
    const data = await res.json();
    document.getElementById('appVersion').textContent = `EVBro ${data.version}`;
    const commit = document.getElementById('appCommit');
    if (data.commit) document.getElementById('appCommitHash').textContent = data.commit;
    commit.hidden = !data.commit;
    appVersionLoaded = true;
  } catch (e) { /* sin conexión: se intenta otra vez al volver a la pestaña */ }
}

/**
 * Control de navegación de subpestañas en el módulo de Recargas (Detalles vs Historial).
 * @param {'details'|'history'} subTabName
 */
function switchChargesSubTab(subTabName) {
  const validSubTabs = ['details', 'history'];
  if (!validSubTabs.includes(subTabName)) {
    console.warn(`[Recargas] Subpestaña no reconocida: ${subTabName}`);
    return;
  }

  // Desactivar botones de submenú de recargas
  document.querySelectorAll('.charges-sub-nav .sub-nav-btn').forEach(btn => btn.classList.remove('active'));

  // Ocultar subpaneles
  validSubTabs.forEach(tab => {
    const panelId = 'chargesSubTab' + tab.charAt(0).toUpperCase() + tab.slice(1);
    const panel = document.getElementById(panelId);
    if (panel) panel.classList.remove('active');
  });

  // Activar botón y panel seleccionados
  const btnId = 'btnChargesSub' + subTabName.charAt(0).toUpperCase() + subTabName.slice(1);
  const panelId = 'chargesSubTab' + subTabName.charAt(0).toUpperCase() + subTabName.slice(1);

  const btn = document.getElementById(btnId);
  const panel = document.getElementById(panelId);
  if (btn) btn.classList.add('active');
  if (panel) panel.classList.add('active');

  if (subTabName === 'details') {
    if (!currentChargeData && typeof loadLatestCharge === 'function') {
      loadLatestCharge(currentVin);
    }
    setTimeout(() => {
      if (chargeMiniMapInstance) chargeMiniMapInstance.invalidateSize();
      if (typeof redrawChargeCharts === 'function') redrawChargeCharts();
    }, 150);
  } else if (subTabName === 'history') {
    if (typeof loadCharges === 'function') {
      loadCharges();
    }
  }
}

/**
 * Navega desde una fila del historial de recargas directamente al detalle y gráfica de dicha sesión.
 * @param {number} chargeId - Identificador único de la sesión de recarga.
 */
function viewChargeDetails(chargeId) {
  switchChargesSubTab('details');
  loadLatestCharge(currentVin, chargeId);
}

function toggleNativeAccordion(id) {
  const card = document.getElementById(id);
  if (card) {
    card.classList.toggle('open');
    if (id === 'accBatteryTelemetry' && card.classList.contains('open')) {
      setTimeout(() => {
        if (typeof redrawChargeCharts === 'function') redrawChargeCharts();
      }, 150);
    }
  }
}
