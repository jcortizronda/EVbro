/* EVBro — Estado compartido de la interfaz (variables globales que usan los demás ficheros).
 * (Script clásico: comparte el ámbito global con los demás ficheros; ver el orden de carga en templates/index.html.)
 */

// EVBro Frontend Application
let currentVin = null;

let currentTargetTemp = 21.0;

let pendingCommand = null;

let liveMap = null;

let vehicleMarker = null;
let vehicleLatLng = null;      // última posición conocida del coche [lat, lon]
let mapUserMoved = false;      // el usuario movió el mapa: no se recentra solo con cada lectura
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
