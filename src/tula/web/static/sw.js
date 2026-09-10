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

const VERSION = 'tatva-shell-v3';
const OFFLINE_URL = '/static/offline.html';

// Assets referenced by every page. Template URLs carry a ?v= content hash, so
// these bare paths are warmed here and the hashed variants are cached on first
// use; a content change produces a new URL rather than a stale hit.
// Only the offline page and the icon are warmed by bare path. The stylesheet and
// scripts are deliberately absent: security/web.py renders the sign-in page with
// un-hashed asset URLs, and a cache-first entry against a bare path can never be
// superseded when the file changes -- an installed phone would keep the stylesheet
// it first saw for ever. They are still cached on first use via their hashed URLs,
// which do change with content.
const SHELL = [
  OFFLINE_URL,
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

function isStatic(url) {
  return url.origin === self.location.origin && url.pathname.startsWith('/static/');
}

// A URL carrying ?v=<hash> is immutable by construction: change the file and the
// URL changes with it, so a cached copy can never be stale. Anything else under
// /static/ is served by a bare path, and caching one of those first-and-for-ever
// is what froze installed handsets on the stylesheet they happened to load first.
function isImmutable(url) {
  return isStatic(url) && url.searchParams.has('v');
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Content-hashed assets: cache first, for ever. The hash guarantees freshness.
  if (isImmutable(url)) {
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

  // Un-hashed static assets -- the manifest, icons, anything a template emits by
  // bare path -- go to the network first and fall back to cache only when it is
  // unreachable. Slower by one request on a warm connection, and the difference
  // between an app that updates and one that cannot.
  if (isStatic(url)) {
    event.respondWith((async () => {
      try {
        const response = await fetch(request);
        if (response.ok && response.type === 'basic') {
          const cache = await caches.open(VERSION);
          cache.put(request, response.clone());
        }
        return response;
      } catch (error) {
        const hit = await caches.match(request);
        if (hit) return hit;
        throw error;
      }
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
