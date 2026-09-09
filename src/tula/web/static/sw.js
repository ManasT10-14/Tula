// Service worker for the installed console.
//
// This worker caches the application shell and nothing else. Every response
// outside /static/ carries "Cache-Control: no-store, private" because it may
// contain label images, recognised text or inspection records, and the Cache
// API would happily store them anyway -- it does not honour no-store. Writing
// casework to a handset that can be lost or shared is exactly what that header
// exists to prevent, so the fetch handler below refuses to cache anything but
// same-origin /static/ assets, and never touches a non-GET request.
//
// The practical effect: the app installs, launches offline to an explanatory
// page, and starts faster on a slow field connection. It does not work offline
// in the sense of carrying out inspections, and it is not supposed to.

const VERSION = 'tula-shell-v2';
const OFFLINE_URL = '/static/offline.html';

// Assets referenced by every page. Template URLs carry a ?v= content hash, so
// these bare paths are warmed here and the hashed variants are cached on first
// use; a content change produces a new URL rather than a stale hit.
const SHELL = [
  OFFLINE_URL,
  '/static/console.css',
  '/static/console.js',
  '/static/htmx.min.js',
  '/static/tabs.js',
  '/static/icons/icon-192.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(VERSION);
    // Individually, so one 404 cannot fail the whole installation.
    await Promise.all(SHELL.map((url) => cache.add(url).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((n) => n !== VERSION).map((n) => caches.delete(n)));
    await self.clients.claim();
  })());
});

function isShellAsset(url) {
  return url.origin === self.location.origin && url.pathname.startsWith('/static/');
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Shell assets: cache first. Their URLs are content-hashed by asset_url(), so
  // a hit is never stale for a changed file.
  if (isShellAsset(url)) {
    event.respondWith((async () => {
      const hit = await caches.match(request);
      if (hit) return hit;
      const response = await fetch(request);
      if (response.ok && response.type === 'basic') {
        const cache = await caches.open(VERSION);
        cache.put(request, response.clone());
      }
      return response;
    })());
    return;
  }

  // Everything else is casework. Go to the network and never store the result.
  // A failed navigation gets the offline page so the installed app explains
  // itself instead of showing the browser's error; a failed sub-resource or API
  // call is left to fail, because the page's own error handling is better than
  // anything a worker can invent.
  if (request.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        return await fetch(request);
      } catch (error) {
        return (await caches.match(OFFLINE_URL))
          || new Response('Offline.', { status: 503, headers: { 'Content-Type': 'text/plain' } });
      }
    })());
  }
});
