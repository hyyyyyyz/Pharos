/*
 * Pharos static-shell service worker.
 *
 * Scope of ambition: an installed Android-tablet PWA opens instead of showing
 * a dinosaur when the network is gone, and updates land without the user
 * having to know what a service worker is. Nothing more.
 *
 * Hard rules, in priority order:
 *   1. NEVER cache `/api/`. Every backend answer is owner-scoped (JWT) and
 *      session-shaped; a cached paper list or chat transcript served after a
 *      sign-out would show one account another's research. The main thread
 *      already clears react-query on sign-out — caching API responses here
 *      would silently defeat it.
 *   2. NEVER cache non-GET, cross-origin, or Range requests (pdf.js streams
 *      PDFs with Range; answering from cache would corrupt a document).
 *   3. Navigations are network-first so a new deploy wins on the next reload,
 *      with the last-seen shell as the offline fallback.
 *   4. Static assets are stale-while-revalidate: instant paint, silent refresh.
 *
 * There is no precache manifest to keep in step with the build — hashed Vite
 * asset names are discovered on first request, and index.html is fetched the
 * same way. One less thing to desynchronise from dist/.
 */

const VERSION = "pharos-shell-v1";
const STATIC_CACHE = `${VERSION}-static`;

self.addEventListener("install", () => {
  // The worker has no precache list, so there is nothing to wait for; take
  // over as soon as plausible and let the activate step retire the old cache.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names.filter((n) => n !== STATIC_CACHE).map((n) => caches.delete(n)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  if (req.headers.has("range")) return; // pdf.js page streaming stays untouched

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(STATIC_CACHE).then((c) => c.put(req, copy));
          return res;
        })
        .catch(async () =>
          (await caches.match(req)) ?? (await caches.match("/")) ?? Response.error(),
        ),
    );
    return;
  }

  event.respondWith(
    caches.match(req).then((hit) => {
      const network = fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(STATIC_CACHE).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() => hit);
      return hit ?? network;
    }),
  );
});
