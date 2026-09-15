/* LeafGreen Maximizer service worker: makes the app installable and usable
   offline. The whole app is one self-contained index.html (~12 MB).

   The PAGE itself is served network-first AND revalidating: the navigation fetch
   is issued with {cache: "no-cache"} so it always makes a conditional request to
   the origin instead of being answered from the browser's HTTP cache inside its
   max-age window (GitHub Pages sends Cache-Control: max-age=600, which otherwise
   lets a plain refresh replay a 10-minute-old build with no network hit -- the
   bug behind "I deployed a fix but a refresh still shows the old page"). When the
   deploy is unchanged the origin answers the conditional request with a fast 304,
   so this stays cheap; offline, it falls back to the cached copy. The shell assets
   (manifest, icons) rarely change and are served cache-first for an instant
   install. Only same-origin GETs are touched; the sync Worker and Google Fonts
   (cross-origin) pass straight through. */
const CACHE = "lgmax-v3";
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
    // {cache:"no-cache"} forces a conditional request to the origin every time,
    // so a refresh after a deploy always gets the new build (a 304 when it hasn't
    // changed), never a still-fresh copy out of the browser's HTTP cache.
    e.respondWith(fetch(req, {cache: "no-cache"}).then(res => cachePut(req, res))
      .catch(() => caches.match(req).then(c => c || caches.match("./"))));
    return;
  }
  // Shell assets: cache-first, refreshed in the background (stale-while-revalidate).
  e.respondWith(caches.match(req).then(cached => {
    const network = fetch(req).then(res => cachePut(req, res)).catch(() => cached);
    return cached || network;
  }));
});
