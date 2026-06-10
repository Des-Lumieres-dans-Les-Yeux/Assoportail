/**
 * Assoportail — Service Worker
 *
 * Responsibilities:
 *  1. Cache static assets and the offline fallback page on install.
 *  2. Serve the offline page when a navigation request fails (no network).
 *  3. Handle incoming Web Push notifications.
 *  4. Handle notification clicks (focus existing window or open a new one).
 */

const CACHE_NAME = 'assoportail-v1';
const OFFLINE_URL = '/offline';

const PRECACHE_URLS = [
  OFFLINE_URL,
  '/static/css/app.css',
  '/static/js/app.js',
  '/static/icons/icon-192.png',
];

// ---------------------------------------------------------------------------
// Install — precache key assets
// ---------------------------------------------------------------------------
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE_URLS))
  );
  // Activate immediately without waiting for old tabs to close.
  self.skipWaiting();
});

// ---------------------------------------------------------------------------
// Activate — remove stale caches
// ---------------------------------------------------------------------------
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

// ---------------------------------------------------------------------------
// Fetch — network-first for navigation; serve offline page on failure
// ---------------------------------------------------------------------------
self.addEventListener('fetch', (event) => {
  if (event.request.mode !== 'navigate') return;

  event.respondWith(
    fetch(event.request).catch(() => caches.match(OFFLINE_URL))
  );
});

// ---------------------------------------------------------------------------
// Push — show a notification
// ---------------------------------------------------------------------------
self.addEventListener('push', (event) => {
  let data = { title: 'Assoportail', body: 'Nouvelle notification', url: '/' };
  if (event.data) {
    try {
      data = { ...data, ...event.data.json() };
    } catch (_) {
      data.body = event.data.text();
    }
  }

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
      data: { url: data.url },
    })
  );
});

// ---------------------------------------------------------------------------
// Notification click — focus or open the target URL
// ---------------------------------------------------------------------------
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || '/';

  event.waitUntil(
    clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then((windowClients) => {
        for (const client of windowClients) {
          if (client.url === targetUrl && 'focus' in client) {
            return client.focus();
          }
        }
        if (clients.openWindow) {
          return clients.openWindow(targetUrl);
        }
      })
  );
});
