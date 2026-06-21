/* mlb_edge service worker — KILL SWITCH (2026-06-21)
 * The first SW version shipped alongside a drawer change that broke mobile and
 * risked serving a stale cached page. This version unregisters itself and purges
 * all caches so every browser returns to plain network loading. A correct,
 * tested PWA will be reintroduced later.
 */
self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys()
      .then(function (keys) { return Promise.all(keys.map(function (k) { return caches.delete(k); })); })
      .then(function () { return self.registration.unregister(); })
      .then(function () { return self.clients.matchAll(); })
      .then(function (clients) { clients.forEach(function (c) { try { c.navigate(c.url); } catch (_) {} }); })
  );
});
