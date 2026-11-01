/* EVBro — Shared UI state (global variables used by the other files).
 * (Classic script: shares the global scope with the other files; see the load order in templates/index.html.)
 */

// EVBro Frontend Application
let currentVin = null;

let currentTargetTemp = 21.0;

let pendingCommand = null;

let liveMap = null;

let vehicleMarker = null;
let vehicleLatLng = null;      // last known car position [lat, lon]
let mapUserMoved = false;      // the user moved the map: do not re-centre on every reading
let mapExpanded = false;

let routeModalMap = null;

let routePolyline = null;

let routeStartMarker = null;

let routeEndMarker = null;

let ws = null;

let cachedDrives = {};

let latestOdometer = 0;

let latestModel = "";

let latestTelemetryData = null;

let cachedGeofencesList = [];

let allVehiclesList = [];
