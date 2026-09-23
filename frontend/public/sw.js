/* HOM service worker — makes the installed app start fast and explains
 * clearly when the HOM server can't be reached.
 *
 * Deliberately NOT cached: /api/* (leads, messages, settings and login are
 * always live from the server — nothing personal is stored on the device).
 * Pages: network first, offline page if the server is unreachable.
 * Hashed build assets (/assets/*): cache first — their names change on every
 * release, so a cached copy is never stale.
 */
const VERSION = 'hom-v1'
const SHELL = `${VERSION}-shell`
const ASSETS = `${VERSION}-assets`
const PRECACHE = ['/offline.html', '/manifest.webmanifest', '/favicon.svg',
  '/icons/icon-192.png', '/icons/icon-512.png', '/icons/apple-touch-icon.png']

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()))
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET') return
  const url = new URL(req.url)
  if (url.origin !== self.location.origin) return          // fonts etc.: browser default
  if (url.pathname.startsWith('/api/')) return             // live data: never cached

  if (req.mode === 'navigate') {
    event.respondWith(fetch(req).catch(() => caches.match('/offline.html')))
    return
  }

  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok) { const copy = res.clone(); caches.open(ASSETS).then((c) => c.put(req, copy)) }
        return res
      })),
    )
    return
  }

  if (PRECACHE.includes(url.pathname)) {
    // Serve fast from cache, refresh in the background.
    event.respondWith(
      caches.match(req).then((hit) => {
        const fresh = fetch(req).then((res) => {
          if (res.ok) { const copy = res.clone(); caches.open(SHELL).then((c) => c.put(req, copy)) }
          return res
        }).catch(() => hit)
        return hit || fresh
      }),
    )
  }
})
