/*
 * The dashboard's service worker (docs/pwa.md). Two jobs: make the dashboard
 * installable on a phone or a desktop, and let the installed app open its
 * shell when the network is slow or gone.
 *
 * What it touches, and how:
 *   - page loads (navigations): network first, so a deploy is picked up on the
 *     next open; the last good index.html when the network fails.
 *   - /assets/*: cache first. Vite fingerprints these, so a URL never changes
 *     content; the cache is trimmed to the newest entries.
 *   - the few unhashed shell files (logo, icons, manifest): served from the
 *     cache and refreshed behind it.
 *   - the pages a phone opens most (Assistant, Chat, Dashboard): their chunks
 *     are fetched ahead, from the build's asset-manifest.json, when the worker
 *     installs and again after each deploy, so they open offline even if they
 *     were never visited. Other pages work offline once they have been opened.
 *
 * What it never touches: /api (live state, SSE streams, auth), the backend's
 * own pages (/preview, /apps, /consent), other origins and anything but GET.
 * Those go straight to the network as if there were no worker.
 *
 * Paths are relative to the worker's scope, so a build under a base path
 * works the same way.
 */
const VERSION = 'v1';
const SHELL_CACHE = `ah-shell-${VERSION}`;
const ASSET_CACHE = `ah-assets-${VERSION}`;
const MAX_ASSETS = 300;
const NAV_TIMEOUT_MS = 4000;
// A server's Vary (Origin from Vite, Accept-Encoding from nginx gzip) would
// make a module script's request miss the entry the worker stored itself.
// These files are the same whatever asked for them.
const MATCH = { ignoreVary: true };
// Source files whose chunks (with their imports and styles) are kept ready.
const WARM_ENTRIES = ['index.html', 'src/pages/Assistant.jsx', 'src/pages/Chat.jsx', 'src/pages/Dashboard.jsx'];

const scope = new URL(self.registration.scope);
const at = (path) => new URL(path, scope).href;
const INDEX = at('index.html');
const SHELL_FILES = [INDEX, at('logo.svg'), at('manifest.webmanifest'), at('icons/icon-192.png')];
const BACKEND_PREFIXES = ['api/', 'preview/', 'apps/', 'consent/'].map(at);

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    // `reload` skips the HTTP cache: the shell kept here is the server's.
    await Promise.all(SHELL_FILES.map(async (url) => {
      try {
        const res = await fetch(url, { cache: 'reload' });
        if (res.ok) await cache.put(url, res);
      } catch { /* offline at install: filled on the next good load */ }
    }));
    await warm();
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keep = new Set([SHELL_CACHE, ASSET_CACHE]);
    for (const key of await caches.keys()) {
      if (key.startsWith('ah-') && !keep.has(key)) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});

async function trim(cache) {
  const keys = await cache.keys();
  // Insertion order: the oldest entries come first.
  for (const req of keys.slice(0, Math.max(0, keys.length - MAX_ASSETS))) await cache.delete(req);
}

// Fetch the chunks of WARM_ENTRIES that are not cached yet. Best effort: a
// failure leaves those pages to be cached the first time they are opened.
async function warm() {
  try {
    const res = await fetch(at('asset-manifest.json'), { cache: 'no-cache' });
    if (!res.ok) return;
    const manifest = await res.json();
    const files = new Set();
    const walk = (key) => {
      const chunk = manifest[key];
      if (!chunk || chunk.seen) return;
      chunk.seen = true;
      if (chunk.file && chunk.file.startsWith('assets/')) files.add(chunk.file);
      (chunk.css || []).forEach((f) => files.add(f));
      (chunk.imports || []).forEach(walk);
    };
    WARM_ENTRIES.forEach(walk);
    const cache = await caches.open(ASSET_CACHE);
    for (const file of files) {
      const url = at(file);
      if (await cache.match(url, MATCH)) continue;
      const r = await fetch(url);
      if (r.ok) await cache.put(url, r);
    }
    await trim(cache);
  } catch { /* offline or no manifest (dev build) */ }
}

async function page(request, event) {
  const cache = await caches.open(SHELL_CACHE);
  const network = fetch(request).then(async (res) => {
    // Every client route answers with index.html; keep the latest one.
    if (res.ok && (res.headers.get('content-type') || '').includes('text/html')) {
      const before = await cache.match(INDEX, MATCH);
      const html = await res.clone().text();
      await cache.put(INDEX, res.clone());
      // A new index.html is a deploy: warm the new chunks behind the page.
      if (!before || (await before.text()) !== html) {
        const warming = warm();
        // Throws once the response has gone out on the slow path; the warm-up
        // still runs while the worker stays alive.
        try { event.waitUntil(warming); } catch { /* see above */ }
      }
    }
    return res;
  });
  network.catch(() => { /* answered from the cache below */ });
  // A phone on a weak signal: after a few seconds the kept shell opens, and
  // the network answer still refreshes the cache for the next time.
  const slow = new Promise((resolve) => setTimeout(resolve, NAV_TIMEOUT_MS));
  try {
    const first = await Promise.race([network, slow]);
    if (first) return first;
    const cached = await cache.match(INDEX, MATCH);
    return cached || (await network);
  } catch (err) {
    const cached = await cache.match(INDEX, MATCH);
    if (cached) return cached;
    throw err;
  }
}

async function asset(request) {
  const cache = await caches.open(ASSET_CACHE);
  const cached = await cache.match(request, MATCH);
  if (cached) return cached;
  const res = await fetch(request);
  if (res.ok) {
    await cache.put(request, res.clone());
    trim(cache);
  }
  return res;
}

async function shellFile(request) {
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(request, MATCH);
  const fresh = fetch(request).then(async (res) => {
    if (res.ok) await cache.put(request, res.clone());
    return res;
  }).catch(() => null);
  return cached || (await fresh) || Response.error();
}

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== scope.origin || !url.href.startsWith(scope.href)) return;
  if (BACKEND_PREFIXES.some((p) => url.href.startsWith(p))) return;

  if (request.mode === 'navigate') {
    event.respondWith(page(request, event));
  } else if (url.href.startsWith(at('assets/'))) {
    event.respondWith(asset(request));
  } else if (SHELL_FILES.includes(url.href) || url.href.startsWith(at('icons/'))) {
    event.respondWith(shellFile(request));
  }
});
