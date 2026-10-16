const CACHE = 'bop-play-9ac74a18dfdd77d5';
const BASE = new URL('./', self.location.href);
const SHELL = [ './', './index.html', './play/app.mjs', './play/style.css',
  './play/scene.mjs', './play/mic.mjs', './play/analysis-client.mjs',
  './play/analysis.worker.mjs', './play/analysis.mjs', './play/export-video.mjs',
  './play/offline.mjs', './play/media-utils.mjs', './play/webm-finalize.mjs',
  './core.mjs', './production/assets/bop-bounce.svg',
  './production/assets/bop-fly.svg', './production/assets/bop-orbit.svg' ];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('bop-play-') && key !== CACHE)
    .map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
function cacheable(url) {
  if (url.origin !== BASE.origin || !url.pathname.startsWith(BASE.pathname)) return false;
  const path = url.pathname.slice(BASE.pathname.length);
  return path === '' || path === 'index.html' || path === 'core.mjs' ||
    path.startsWith('play/') || path.startsWith('browser-proof/vendor/') ||
    path === 'browser-proof/manifest.json' || path.startsWith('production/assets/') ||
    path.startsWith('production/audio/');
}
self.addEventListener('fetch', event => {
  const request = event.request, url = new URL(request.url);
  if (request.method !== 'GET' || !cacheable(url)) return;
  // Do not cache partial media responses or user/private sources.
  event.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const appCode = /\.(?:m?js|css)$/.test(url.pathname) && !url.pathname.includes('/vendor/');
    if (request.mode === 'navigate' || appCode) {
      try {
        const response = await fetch(request);
        if (response.status === 200) {
          try { await cache.put(request, response.clone()); } catch { /* Quota is optional. */ }
        }
        return response;
      } catch {
        return (await cache.match(request)) || (request.mode === 'navigate' && await cache.match(new URL('./index.html', BASE))) || Response.error();
      }
    }
    const stored = await cache.match(request);
    if (stored) return stored;
    const response = await fetch(request);
    if (response.status === 200) {
      try { await cache.put(request, response.clone()); } catch { /* Quota is optional. */ }
    }
    return response;
  })());
});
