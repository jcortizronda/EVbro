/* EVBro — Navigation: tabs, sub-tabs and accordions.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
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

  // In a submenu on a phone, highlight the Más button if there is no button of its own
  if (['drives', 'maintenance', 'geofences', 'settings', 'costs', 'expenses', 'refuels', 'timeline', 'stats', 'vehicle'].includes(tabName)) {
    const mobMore = document.getElementById('mobTabMore');
    if (mobMore && !mobBtn) mobMore.classList.add('active');
  }

  if (tabName !== 'overview' && window.HomeWidgets) HomeWidgets.stopEditing();
  if (tabName === 'overview') {
    if (window.HomeWidgets) HomeWidgets.refresh();          // the new widgets (maintenance, last trip…) read their data now
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
 * Sub-tab navigation inside the Ajustes (settings) module.
 * Each section is decoupled and lazy-loaded on demand.
 * @param {'account'|'accounts'|'notifs'|'connections'|'system'} subTabName
 */
function switchSettingsSubTab(subTabName) {
  if (subTabName === 'theme') subTabName = 'system';      // «Tema» became the «Apariencia» section of Sistema
  window.currentSettingsSubTab = subTabName;
  const validSubTabs = ['account', 'accounts', 'notifs', 'connections', 'system'];
  if (!validSubTabs.includes(subTabName)) {
    console.warn(`[Ajustes] Unknown sub-tab: ${subTabName}`);
    return;
  }

  // Deactivate the submenu buttons
  document.querySelectorAll('.settings-sub-nav .sub-nav-btn').forEach(btn => btn.classList.remove('active'));

  // Hide every sub-panel
  validSubTabs.forEach(tab => {
    const panelId = 'settingsSubTab' + tab.charAt(0).toUpperCase() + tab.slice(1);
    const panel = document.getElementById(panelId);
    if (panel) panel.classList.remove('active');
  });

  // Activate the selected button and panel
  const btnId = 'btnSettingsSub' + subTabName.charAt(0).toUpperCase() + subTabName.slice(1);
  const panelId = 'settingsSubTab' + subTabName.charAt(0).toUpperCase() + subTabName.slice(1);

  const btn = document.getElementById(btnId);
  const panel = document.getElementById(panelId);
  if (btn) btn.classList.add('active');
  if (panel) panel.classList.add('active');

  // Load the section on demand
  if (subTabName === 'account') {
    if (typeof checkAccountStatus === 'function') checkAccountStatus();
    if (window.EbroPasskeys) EbroPasskeys.refresh();        // «Acceso con huella» lives here
    if (typeof loadStorageUsage === 'function') loadStorageUsage();   // «Espacio usado» for documents, invoices and photos
  } else if (subTabName === 'accounts') {
    if (typeof loadAccounts === 'function') loadAccounts();
  } else if (subTabName === 'notifs') {
    if (typeof loadNotificationConfig === 'function') loadNotificationConfig();
  } else if (subTabName === 'connections') {
    if (typeof loadCarChannelStatus === 'function') loadCarChannelStatus();   // the live channel with the car
    if (window.MqttHa) MqttHa.load();                   // the manager's own broker for Home Assistant
    if (window.Traccar) Traccar.load();                 // «Posición por GPS (Traccar)» of the chosen car
  } else if (subTabName === 'system') {
    if (typeof updateThemeSettingsUI === 'function') updateThemeSettingsUI();   // «Apariencia»
    loadAppVersion();
  }
}

/* Ajustes sections (<details class="set-section">): every card folds like the car sheet's, and which ones are open is
 * remembered on this device. By default only «Cuenta de Ebro» is open; the rest show just their title (and status badge). */
const SETTINGS_FOLD_KEY = 'evbro_settings_open';
function initSettingsFolds() {
  let open = ['ebro'];
  try { const v = JSON.parse(localStorage.getItem(SETTINGS_FOLD_KEY)); if (Array.isArray(v)) open = v; } catch (e) { /* no storage */ }
  const sections = document.querySelectorAll('#tabSettings .set-section');
  sections.forEach((d) => {
    d.open = open.includes(d.dataset.key);
    d.addEventListener('toggle', () => {
      const now = [...document.querySelectorAll('#tabSettings .set-section[open]')].map((s) => s.dataset.key);
      try { localStorage.setItem(SETTINGS_FOLD_KEY, JSON.stringify(now)); } catch (e) { /* does not matter */ }
    });
  });
}
document.addEventListener('DOMContentLoaded', initSettingsFolds);

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
  } catch (e) { /* offline: try again when coming back to the tab */ }
}

/**
 * Sub-tab navigation in the charges module (details vs history).
 * @param {'details'|'history'} subTabName
 */
function switchChargesSubTab(subTabName) {
  const validSubTabs = ['details', 'history'];
  if (!validSubTabs.includes(subTabName)) {
    console.warn(`[Recargas] Unknown sub-tab: ${subTabName}`);
    return;
  }

  // Deactivate the charges submenu buttons
  document.querySelectorAll('.charges-sub-nav .sub-nav-btn').forEach(btn => btn.classList.remove('active'));

  // Hide the sub-panels
  validSubTabs.forEach(tab => {
    const panelId = 'chargesSubTab' + tab.charAt(0).toUpperCase() + tab.slice(1);
    const panel = document.getElementById(panelId);
    if (panel) panel.classList.remove('active');
  });

  // Activate the selected button and panel
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
 * Goes from a row of the charge history straight to that session's detail and chart.
 * @param {number} chargeId - Unique id of the charging session.
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
