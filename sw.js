/* LeafGreen Maximizer service worker: makes the app installable and usable
   offline. The whole app is one self-contained index.html (~12 MB).

   The PAGE itself is served network-first: an active user (and anyone who just
   got a fix) sees the newest deploy on a plain refresh, not a cached build. When
   the deploy is unchanged GitHub Pages answers the conditional request with a
   fast 304, so this stays cheap; offline, it falls back to the cached copy. The
   shell assets (manifest, icons) rarely change and are served cache-first for an
   instant install. Only same-origin GETs are touched; the sync Worker and Google
   Fonts (cross-origin) pass straight through. */
const CACHE = "lgmax-v2";
const SHELL = ["./", "./manifest.webmanifest", "./icon-192.png", "./icon-512.png", "./icon-180.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

function cachePut(req, res) {
  if (res && res.ok && res.type === "basic") {
    const copy = res.clone();
    caches.open(CACHE).then(c => c.put(req, copy));
  }
  return res;
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  if (new URL(req.url).origin !== self.location.origin) return;   // fonts, sync Worker
  // The page load: network-first so fixes show on a normal refresh, cache only
  // as the offline fallback (the cached "./" covers a bare navigation too).
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).then(res => cachePut(req, res))
      .catch(() => caches.match(req).then(c => c || caches.match("./"))));
    return;
  }
  // Shell assets: cache-first, refreshed in the background (stale-while-revalidate).
  e.respondWith(caches.match(req).then(cached => {
    const network = fetch(req).then(res => cachePut(req, res)).catch(() => cached);
    return cached || network;
  }));
});
