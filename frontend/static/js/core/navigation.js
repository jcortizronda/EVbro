/* EVbro — Navigation: tabs, sub-tabs and accordions.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

// Tab switcher: the desktop sidebar (`.side-item[data-tab]`) and the touch bottom dock (`#mobTab…`) share the same sections
// Tabs that are a whole area an installation may not offer to the account (core/auth.js `featureOff`)
const TAB_AREAS = { stats: 'stats', maintenance: 'maintenance', expenses: 'costs', costs: 'costs' };

function switchTab(tabName) {
  if (TAB_AREAS[tabName] && typeof featureOff === 'function' && featureOff(TAB_AREAS[tabName])) tabName = 'overview';
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

  if (window.WidgetBoards) WidgetBoards.onTab(tabName);       // other pages stop editing; this page's widgets (maintenance, last trip…) load
  if (tabName === 'overview') {
    setTimeout(() => {
      if (liveMap) liveMap.invalidateSize();
    }, 200);
    if (typeof refreshGpsPosition === 'function') refreshGpsPosition(true);
    if (typeof showAccountChannelNotice === 'function') showAccountChannelNotice();     // the live channel may have stopped meanwhile
  } else if (tabName === 'drives') {
    loadDrives();
    if (window.Journeys) Journeys.load();                  // «Viajes largos»
    loadGeofencesForFilter();
    if (typeof loadPlaces === 'function') loadPlaces();
  } else if (tabName === 'charges') {
    // Charge control: what EVbro stores paints the state and the buttons at once; the car's real schedule (a cloud query,
    // seconds) completes it after. It used to wait for the 3 s refresh timer and then for that query.
    if (window.ChargeControl) ChargeControl.load(false).then(() => ChargeControl.load(true));
    loadLatestCharge();
    loadCharges();
    setTimeout(() => {
      if (chargeMiniMapInstance) chargeMiniMapInstance.invalidateSize();
      if (typeof redrawChargeCharts === 'function') redrawChargeCharts();
    }, 200);
  } else if (tabName === 'climate') {
    if (typeof syncClimateUI === 'function') syncClimateUI();
    if (window.ClimateSchedule) ClimateSchedule.load();
    if (window.Departure) Departure.load();
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
 * remembered on this device. A section this device has not seen yet starts as the page says (`data-open-default` = open: «Cuenta
 * de Ebro», the only card of Compartir and «Este dispositivo» of Avisos); the rest show just their title (and status badge). */
const SETTINGS_FOLD_KEY = 'evbro_settings_open';
const SETTINGS_SEEN_KEY = 'evbro_settings_seen';
function initSettingsFolds() {
  let open = null;
  let seen = null;
  try {
    open = JSON.parse(localStorage.getItem(SETTINGS_FOLD_KEY));
    seen = JSON.parse(localStorage.getItem(SETTINGS_SEEN_KEY));
  } catch (e) { /* no storage */ }
  const sections = [...document.querySelectorAll('#tabSettings .set-section')];
  if (!Array.isArray(open)) open = null;
  if (!Array.isArray(seen)) {
    // A device that remembered its sections before this list existed had seen every section that starts closed, and «ebro»
    seen = open ? sections.filter((d) => !d.hasAttribute('data-open-default') || d.dataset.key === 'ebro').map((d) => d.dataset.key) : [];
  }
  sections.forEach((d) => {
    d.open = open && seen.includes(d.dataset.key) ? open.includes(d.dataset.key) : d.hasAttribute('data-open-default');
  });
  try { localStorage.setItem(SETTINGS_SEEN_KEY, JSON.stringify(sections.map((d) => d.dataset.key))); } catch (e) { /* does not matter */ }
  sections.forEach((d) => {
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
    document.getElementById('appVersion').textContent = `EVbro ${data.version}`;
    const commit = document.getElementById('appCommit');
    if (data.commit) document.getElementById('appCommitHash').textContent = data.commit;
    commit.hidden = !data.commit;
    appVersionLoaded = true;
  } catch (e) { /* offline: try again when coming back to the tab */ }
}

/**
 * Carga: one charge's detail and the charge history are two widgets now (they used to be sub-tabs of one accordion).
 * Kept for older calls: it brings the asked one into view and loads what it shows.
 * @param {'details'|'history'} subTabName
 */
function switchChargesSubTab(subTabName) {
  const key = subTabName === 'history' ? 'charges' : 'session';
  const widget = document.querySelector(`#energyWidgets > .home-widget[data-widget="${key}"]`);
  if (subTabName === 'history') {
    if (typeof loadCharges === 'function') loadCharges();
  } else {
    const acc = document.getElementById('accBatteryTelemetry');
    if (acc) acc.classList.add('open');
    if (!currentChargeData && typeof loadLatestCharge === 'function') loadLatestCharge(currentVin);
    setTimeout(() => {
      if (chargeMiniMapInstance) chargeMiniMapInstance.invalidateSize();
      if (typeof redrawChargeCharts === 'function') redrawChargeCharts();
    }, 150);
  }
  if (widget && !widget.hidden) widget.scrollIntoView({ block: 'start', behavior: 'smooth' });
}

/**
 * From a row of the charge history to that charge's detail and chart.
 * @param {number} chargeId - Unique id of the charging session.
 */
function viewChargeDetails(chargeId) {
  const widget = document.querySelector('#energyWidgets > .home-widget[data-widget="session"]');
  if (!widget || widget.hidden) {
    showToast(t('Para ver cada carga, añade «Detalle de la carga» con «Personalizar carga».'), 'info', 6000);
    return;
  }
  loadLatestCharge(currentVin, chargeId);
  switchChargesSubTab('details');
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
