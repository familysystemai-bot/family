const CACHE_VERSION = "family-pwa-v1";
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const STATIC_ASSETS = [
  "/",
  "/static/manifest.json",
  "/static/js/pwa-register.js",
  "/static/pwa/icon-192.png",
  "/static/pwa/icon-512.png",
  "/static/pwa/icon-maskable-192.png",
  "/static/pwa/icon-maskable-512.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.map((k) => {
          if (!k.startsWith(CACHE_VERSION)) {
            return caches.delete(k);
          }
          return Promise.resolve();
        })
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // لا نكاش APIs ولا endpoints حساسة/ديناميكية.
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/wa_") ||
    url.pathname.startsWith("/chat_query") ||
    url.pathname.startsWith("/webhook") ||
    url.pathname.startsWith("/login") ||
    url.pathname.startsWith("/logout")
  ) {
    return;
  }

  // للملفات الثابتة: cache-first.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then((cached) => {
        if (cached) return cached;
        return fetch(req).then((resp) => {
          if (resp && resp.status === 200) {
            const copy = resp.clone();
            caches.open(STATIC_CACHE).then((cache) => cache.put(req, copy));
          }
          return resp;
        });
      })
    );
    return;
  }

  // صفحات التنقل: network-first مع fallback لنسخة / فقط (آمن وبسيط).
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match("/") || caches.match("/index.html") || Response.error())
    );
  }
});
