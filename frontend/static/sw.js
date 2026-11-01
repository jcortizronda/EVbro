// EVBro Service Worker - v2.5.0
const CACHE_NAME = 'evbro-cache-v1';
// Last good copy of the API reads that paint the app, served with `X-EVBro-Offline` when there is no network (core/offline.js)
const API_CACHE = 'evbro-api-v1';
const OFFLINE_API = [/^\/api\/auth\/status$/, /^\/api\/account\/status$/, /^\/api\/vehicles(\/|$)/, /^\/api\/geofences(\/|$)/, /^\/api\/commands\/catalog$/, /^\/api\/version$/];
const STATIC_ASSETS = [
  '/',
  '/static/css/base/tokens.css',
  '/static/css/base/reset.css',
  '/static/css/layout/header.css',
  '/static/css/components/badges.css',
  '/static/css/components/energy-cards.css',
  '/static/css/components/modals.css',
  '/static/css/components/buttons.css',
  '/static/css/features/drives.css',
  '/static/css/features/journeys.css',
  '/static/css/features/history-list.css',
  '/static/css/features/maintenance.css',
  '/static/css/features/maintenance-checklist.css',
  '/static/css/features/maintenance-plan.css',
  '/static/css/features/vehicle-profile.css',
  '/static/css/features/costs.css',
  '/static/css/features/expenses.css',
  '/static/css/features/refuels.css',
  '/static/css/features/timeline.css',
  '/static/css/features/stats.css',
  '/static/css/features/period-picker.css',
  '/static/css/features/parts.css',
  '/static/css/features/vehicle-documents.css',
  '/static/css/features/accounts.css',
  '/static/css/layout/mobile-nav.css',
  '/static/css/features/invoices.css',
  '/static/css/features/theme-settings.css',
  '/static/css/features/charge-sessions.css',
  '/static/css/features/geofence-map.css',
  '/static/css/layout/sub-nav.css',
  '/static/css/layout/native-app.css',
  '/static/css/features/notifications.css',
  '/static/css/components/toast.css',
  '/static/css/components/action-sheet.css',
  '/static/css/features/geofences.css',
  '/static/css/features/geofence-cards.css',
  '/static/css/base/touch.css',
  '/static/css/features/login.css',
  '/static/css/layout/mobile-layer.css',
  '/static/css/features/comfort.css',
  '/static/css/features/charge-control.css',
  '/static/css/components/tables-cards.css',
  '/static/css/features/home-tiles.css',
  '/static/css/features/home-widgets.css',
  '/static/css/features/sensors.css',
  '/static/css/features/car-figure.css',
  '/static/css/layout/page-head.css',
  '/static/css/features/push.css',
  '/static/css/features/settings.css',
  '/static/css/layout/dock-desktop.css',
  '/static/css/layout/desktop.css',
  '/static/css/features/charge-schedule.css',
  '/static/css/features/charge-target.css',
  '/static/css/components/icons.css',
  '/static/css/features/live-map.css',
  '/static/css/layout/touch-rendering.css',
  '/static/css/features/passkeys.css',
  '/static/css/base/utilities.css',
  '/static/js/core/auth.js',
  '/static/js/core/state.js',
  '/static/js/core/icons.js',
  '/static/js/core/feedback.js',
  '/static/js/core/offline.js',
  '/static/js/core/actions.js',
  '/static/js/core/theme-boot.js',
  '/static/js/core/i18n.js',
  '/static/js/core/pwa.js',
  '/static/js/core/navigation.js',
  '/static/js/core/map.js',
  '/static/js/core/vehicles.js',
  '/static/js/core/telemetry.js',
  '/static/js/core/commands.js',
  '/static/js/core/websocket.js',
  '/static/js/features/history-list.js',
  '/static/js/features/drives.js',
  '/static/js/features/journeys.js',
  '/static/js/features/maintenance.js',
  '/static/js/features/maintenance-official.js',
  '/static/js/features/maintenance-invoices.js',
  '/static/js/features/charge-sessions.js',
  '/static/js/features/geofences.js',
  '/static/js/features/account.js',
  '/static/js/features/notifications.js',
  '/static/js/features/theme.js',
  '/static/js/features/polling.js',
  '/static/js/features/sensors.js',
  '/static/js/features/car-channel.js',
  '/static/js/features/vehicle-profile.js',
  '/static/js/features/access-ui.js',
  '/static/js/features/accounts.js',
  '/static/js/features/parts.js',
  '/static/js/features/vehicle-documents.js',
  '/static/js/features/expenses.js',
  '/static/js/features/refuels.js',
  '/static/js/features/timeline.js',
  '/static/js/features/stats.js',
  '/static/js/features/period-picker.js',
  '/static/js/features/costs.js',
  '/static/js/core/bootstrap.js',
  '/static/js/core/ui.js',
  '/static/js/features/comfort.js',
  '/static/js/features/climate-schedule.js',
  '/static/js/features/traccar.js',
  '/static/js/features/account-data.js',
  '/static/js/features/car-book.js',
  '/static/js/features/charge-control.js',
  '/static/js/features/home.js',
  '/static/js/features/home-widgets.js',
  '/static/js/features/app-shortcuts.js',
  '/static/js/features/car-figure.js',
  '/static/js/features/push.js',
  '/static/js/features/passkeys.js',
  '/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/shortcut-lock.png',
  '/static/icons/shortcut-climate.png',
  '/static/icons/shortcut-energy.png',
  '/static/icons/shortcut-trips.png',
  '/static/icons/favicon.svg',
  'https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
  'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'
];

self.addEventListener('install', (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[SW] Pre-caching static assets');
      return cache.addAll(STATIC_ASSETS).catch((err) => {
        console.warn('[SW] Pre-cache warning:', err);
      });
    })
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME && key !== API_CACHE) {
            console.log('[SW] Removing old cache', key);
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // API reads that paint the app: network first; without network, the last good copy marked as offline.
  if (url.pathname.startsWith('/api')) {
    if (event.request.method === 'GET' && url.origin === self.location.origin && OFFLINE_API.some((re) => re.test(url.pathname))) {
      event.respondWith(apiNetworkFirst(event.request));
    }
    return;                                            // everything else of the API: network only
  }
  // WebSockets and the service worker itself: network only
  if (url.pathname.startsWith('/ws') || url.pathname === '/sw.js') {
    return;
  }

  // Network-First strategy: always fetch fresh from network, update cache, and fallback to cache if offline
  event.respondWith(
    fetch(event.request)
      .then((networkResponse) => {
        if (networkResponse && networkResponse.status === 200 && networkResponse.type === 'basic') {
          const responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseToCache);
          });
        }
        return networkResponse;
      })
      // Offline: a shortcut opens «/?go=…»; the page cached as «/» serves it (the query is read by the app, not the server)
      .catch(() => caches.match(event.request, event.request.mode === 'navigate' ? { ignoreSearch: true } : undefined))
  );
});

async function apiNetworkFirst(request) {
  try {
    const fresh = await fetch(request);
    if (fresh && fresh.status === 200) {
      const copy = fresh.clone();
      caches.open(API_CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
    }
    return fresh;
  } catch (err) {
    const cached = await caches.match(request, { cacheName: API_CACHE });
    if (!cached) throw err;
    const headers = new Headers(cached.headers);
    headers.set('X-EVBro-Offline', cached.headers.get('Date') || new Date().toISOString());
    return new Response(await cached.blob(), { status: 200, statusText: 'OK (offline)', headers });
  }
}

// ---- Web Push (on Android, Chrome delivers it through Google FCM) ----
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data && event.data.text() }; }
  const title = data.title || 'EVBro';
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || '',
    icon: '/static/icons/icon-192.png',
    badge: '/static/icons/icon-192.png',
    tag: data.tag || data.category || 'evbro',   // the same tag replaces the previous alert instead of stacking them
    renotify: true,
    data: { url: data.url || '/' },
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        if ('focus' in w) return w.focus();
      }
      return self.clients.openWindow(target);
    })
  );
});
