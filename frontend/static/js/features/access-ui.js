/* EVBro — Interfaz según el nivel de acceso de la cuenta sobre el coche actual y su rol.
 * El SERVIDOR lo impone todo (backend/src/app/security/access.py); esto solo oculta lo que respondería 403.
 * (Script clásico: comparte el ámbito global con los demás ficheros; ver el orden de carga en templates/index.html.)
 */

// A level is a set of permissions (mirror of LEVEL_GRANTS in security/access.py): «ver y controlar» does not include editing.
const LEVEL_GRANTS = {
  view: ['view'], edit: ['view', 'edit'], view_control: ['view', 'control'], control: ['view', 'edit', 'control'],
  owner: ['view', 'edit', 'control', 'owner'],
};

// Qué elementos exigen cada nivel (por selector: así no hay que marcar el HTML uno a uno).
const ACCESS_SELECTORS = {
  control: [
    '[data-on-click*="promptCommand"]', '[data-on-click*="toggleLockState"]', '[data-on-click*="openWindowsActionSheet"]', '[data-on-click*="openTrunkActionSheet"]',
    '[data-on-click*="openSunroofActionSheet"]', '[data-on-click*="comfortPreset"]', '[data-on-click*="toggleComfort"]', "[data-on-click*=\"switchTab('climate')\"]",
    '[data-on-click*="pollCurrentVehicle"]', '#chargeControl', '#chargeSchedulePanel', '#climateSchedulePanel', '#tabClimate',
  ],
  edit: [
    '[data-on-click*="openExpenseModal"]', '[data-on-click*="openRefuelModal"]', '[data-on-click*="proposePart"]', '[data-on-click*="openPartModal"]', '[data-on-click*="showAddMaintenanceModal"]',
    '[data-on-click*="showMaintEventModal"]', '[data-on-click*="showRegisterOfficialRevisionModal"]', '[data-on-click*="showItvModal"]',
    '#btnEditChargeCost', '#maintBaselineCard', '.maint-card-actions', '.maintenance-card button',
  ],
  owner: ['[data-on-click*="openVehicleSettings"]', '[data-on-click*="openEditVehicleModal"]', '#btnSettingsSubVehicle'],
  root: ['#btnSettingsSubAccount', '#btnSettingsSubAccounts', '[data-on-click*="openCreateGeofenceModal"]',
    '[data-on-click*="triggerVehicleDiscovery"]'],
};

function currentRole() { return (window.accountInfo && window.accountInfo.role) || null; }

function currentLevel() {
  const v = (typeof currentVehicle === 'function') ? currentVehicle() : null;
  if (v) return v.access_level || 'owner';
  return currentRole() === 'member' ? 'view' : 'owner';          // without a car: a member can do nothing; owners can still add one
}

function tagAccessElements() {
  for (const [need, selectors] of Object.entries(ACCESS_SELECTORS)) {
    document.querySelectorAll(selectors.join(',')).forEach((el) => el.classList.add(`needs-${need}`));
  }
}

/** Sets body classes from the role and the level on the current car; CSS hides what each level cannot use. */
function applyAccessLevel() {
  const grants = LEVEL_GRANTS[currentLevel()] || ['view'];
  const role = currentRole();
  const b = document.body;
  b.classList.toggle('can-edit', grants.includes('edit'));
  const veh = (typeof currentVehicle === 'function') ? currentVehicle() : null;
  b.classList.toggle('can-control', grants.includes('control') && !(veh && veh.is_manual));     // a manual car has no connection: no orders
  b.classList.toggle('can-owner', grants.includes('owner'));
  b.classList.toggle('is-root', role === 'admin' || role === 'manager');
  b.dataset.level = currentLevel();
}

document.addEventListener('DOMContentLoaded', () => { tagAccessElements(); applyAccessLevel(); });
