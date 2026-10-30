// eco web service worker: caches the app shell only, so the installed app
// opens (and shows "disconnected") without a connection. Never caches values,
// SVG panels or the websocket -- a stale beamline value shown as current
// would be worse than none. Network first, so a new client version is
// picked up as soon as the gateway is reachable.
const CACHE = "eco-shell-v1";
const SHELL = ["/", "/static/app.js", "/static/style.css", "/icon.svg", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== location.origin) return;
  if (!SHELL.includes(url.pathname)) return; // /api, /ws, ...: straight to the network
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((c) => c.put(event.request, copy));
        }
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
