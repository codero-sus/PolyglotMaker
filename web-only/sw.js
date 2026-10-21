/* Same-origin static assets only; user files and Blob downloads are never cached. */
'use strict';

const CACHE_NAME = 'polyglotmaker-web-only-v1';
const APP_ASSETS = [
  './',
  './index.html',
  './styles.css',
  './engine.js',
  './app.js',
  './manifest.webmanifest',
  './icon.svg',
  './icon-192.png',
  './icon-512.png',
];
const APP_PATHS = new Set(APP_ASSETS.map((asset) => new URL(asset, self.location.href).pathname));

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_ASSETS))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key.startsWith('polyglotmaker-web-only-') && key !== CACHE_NAME).map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || !APP_PATHS.has(url.pathname)) return;

  event.respondWith((async () => {
    const cache = await caches.open(CACHE_NAME);
    try {
      const response = await fetch(request);
      if (response.ok) await cache.put(request, response.clone());
      return response;
    } catch {
      return (await cache.match(request)) || (await cache.match('./index.html')) || Response.error();
    }
  })());
});
