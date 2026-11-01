/* EVBro — Interface according to the account's access level on the current car and its role.
 * The SERVER enforces everything (backend/src/app/security/access.py); this only hides what would answer 403.
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

// A level is a set of permissions (mirror of LEVEL_GRANTS in security/access.py): «ver y controlar» does not include editing.
const LEVEL_GRANTS = {
  view: ['view'], edit: ['view', 'edit'], view_control: ['view', 'control'], control: ['view', 'edit', 'control'],
  owner: ['view', 'edit', 'control', 'owner'],
};

// Which elements each level requires (by selector: so the HTML need not be tagged one by one).
const ACCESS_SELECTORS = {
  control: [
    '[data-on-click*="promptCommand"]', '[data-on-click*="toggleLockState"]', '[data-on-click*="openWindowsActionSheet"]', '[data-on-click*="openTrunkActionSheet"]',
    '[data-on-click*="openSunroofActionSheet"]', '[data-on-click*="comfortPreset"]', '[data-on-click*="toggleComfort"]', "[data-on-click*=\"switchTab('climate')\"]",
    '[data-on-click*="pollCurrentVehicle"]', '#chargeControl', '#chargeSchedulePanel', '#climateSchedulePanel', '#tabClimate',
  ],
  edit: [
    '[data-on-click*="openExpenseModal"]', '[data-on-click*="openRefuelModal"]', '[data-on-click*="proposePart"]', '[data-on-click*="openPartModal"]', '[data-on-click*="showAddMaintenanceModal"]',
    '[data-on-click*="showMaintEventModal"]', '[data-on-click*="showRegisterOfficialRevisionModal"]', '[data-on-click*="showItvModal"]',
    '#btnEditChargeCost', '#btnChargeMeter', '#maintBaselineCard', '.maint-card-actions', '.maintenance-card button',
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
