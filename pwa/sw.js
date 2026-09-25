/* LeafGreen Maximizer service worker: makes the app installable and usable
   offline. The whole app is one self-contained index.html (~11 MB), so we cache
   the app shell and serve stale-while-revalidate — instant and offline-capable,
   with the newest deploy picked up on the next load. Only same-origin GETs are
   touched; the sync Worker and Google Fonts (cross-origin) pass straight through. */
const CACHE = "lgmax-v1";
const SHELL = ["./", "./manifest.webmanifest", "./icon-192.png", "./icon-512.png", "./icon-180.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  if (new URL(req.url).origin !== self.location.origin) return;   // fonts, sync Worker
  e.respondWith(caches.match(req).then(cached => {
    const network = fetch(req).then(res => {
      if (res && res.ok && res.type === "basic") {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy));
      }
      return res;
    }).catch(() => cached);
    return cached || network;
  }));
});
