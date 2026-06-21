/* mlb_edge service worker — 2026-06-21
 * Strategy: NETWORK-FIRST for same-origin requests (so the dashboard + slate
 * data are always fresh when online — avoids the classic stale-PWA trap), with
 * a Cache-API fallback so the page + last-seen data still load OFFLINE.
 * Cross-origin calls (statsapi, mlbstatic, counterapi, etc.) are NOT touched —
 * they pass straight to the network and degrade via the app's localStorage caches.
 */
var CACHE = "mlbedge-v1";
var SHELL = ["./", "./index.html", "./manifest.json"];

self.addEventListener("install", function (e) {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE).then(function (c) { return c.addAll(SHELL).catch(function () {}); })
  );
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys()
      .then(function (keys) {
        return Promise.all(keys.filter(function (k) { return k !== CACHE; })
                               .map(function (k) { return caches.delete(k); }));
      })
      .then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function (e) {
  var req = e.request;
  if (req.method !== "GET") return;
  var url;
  try { url = new URL(req.url); } catch (_) { return; }
  // Only manage our own origin; let third-party APIs hit the network directly.
  if (url.origin !== self.location.origin) return;

  e.respondWith(
    fetch(req)
      .then(function (res) {
        if (res && res.ok) {
          var copy = res.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); }).catch(function () {});
        }
        return res;
      })
      .catch(function () {
        return caches.match(req).then(function (m) {
          return m || caches.match("./index.html");
        });
      })
  );
});
