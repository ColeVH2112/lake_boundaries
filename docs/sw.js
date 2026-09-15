// Offline support: cache-first with background refresh. Any water you open gets
// cached (payload + imagery), so it keeps working with no signal on the lake.
const CACHE = 'lakezones-v1';
const SHELL = ['./', './index.html', './manifest.webmanifest',
               './data/manifest.json', './data/sources.json'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE)
    .then(c => Promise.allSettled(SHELL.map(u => c.add(u))))
    .then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(caches.open(CACHE).then(async c => {
    const hit = await c.match(e.request, { ignoreSearch: false });
    const refresh = fetch(e.request).then(async r => {
      if (r && (r.ok || r.type === 'opaque')) await c.put(e.request, r.clone()).catch(() => {});
      return r;
    }).catch(() => hit);
    if (hit) {
      // keep the SW alive until the background refresh lands, or cache-first
      // never actually updates (Safari kills the worker after respondWith)
      e.waitUntil(refresh.then(() => {}, () => {}));
      return hit;
    }
    return refresh;
  }));
});
