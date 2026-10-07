# Phone and installed app

The dashboard is a progressive web app: on a phone (or a desktop) it installs
from the browser as an app with its own icon, opens full screen without the
browser's bars, and starts on the [Assistant](assistant.md). Every page works
at phone width; the ones built for it are the Assistant and [Chat](chat.md).

## Install it

The hub must be served over **HTTPS** (see [HTTPS on a server](#https-on-a-server)).
Browsers only install an app, run its service worker and give a page the
microphone on a secure origin; `http://localhost` counts as one, a LAN address
or a bare server IP does not.

- **Android, Chrome or Edge:** open the hub, then the menu (☰ top left) and
  **Install app** at its foot. The browser's own menu has the same item
  (Install app, or Add to Home screen).
- **iPhone and iPad, Safari:** Share, then **Add to Home Screen**. Safari has no
  install dialog a page can open, so the **Install app** item in the menu shows
  these two steps instead.
- **Desktop Chrome or Edge:** the install icon in the address bar, or **Install
  app** at the foot of the sidebar.

The item is not shown once the hub runs as an installed app, or in a browser
that offers no install at all (Firefox on a desktop).

The installed app starts on `/assistant`. A long press on its icon (Android) or
the app's shortcuts menu offers Assistant, Chat and Dashboard directly.

## On a phone

- **Menu.** The sidebar is a drawer: ☰ opens it, a tap outside it or any
  navigation closes it. Theme, language and sign out sit at its foot, since the
  header has no room for them.
- **Header.** The workspace picker, the model chip (its icon; the full name is in
  the picker it opens), watchers, notifications, the live dot and Help. The
  model picker, notifications and watchers open as sheets under the header.
- **Chat.** The conversation list is a drawer, opened by the first button of the
  chat's top bar and closed by picking a conversation. The top bar wraps to two
  rows with icons only. Process, Artifacts and Code each cover the screen; their
  X closes them.
- **Assistant.** Hold the talk button, or switch to Conversation or Wake phrase.
  The transcript and history column needs a screen 1024px wide; on a phone,
  "show on screen" opens the page itself, and past assistant conversations are
  readable in Chat.
- **Fields** use 16px text on a phone, so iOS does not zoom into a field when it
  gets focus.
- **The notch and the home indicator.** The installed app draws under them and
  pads the header, the drawers and the panels with the safe-area insets.

## What works offline

The service worker (`public/sw.js`) keeps the app shell: the last good
`index.html` and the hashed bundle files. With no network, or a network that
has not answered in four seconds, the installed app still opens, moves between
the pages whose code it has loaded before and reports the backend offline in
the header. Data
is never cached: `/api` (live state, streams, sign in), and the backend's own
pages under `/preview`, `/apps` and `/consent` always go to the network. A deploy
is picked up on the next open, because page loads go to the network first.

The worker is registered only by a production build on a secure origin: not by
the Vite dev server, and not by the [demo](demo.md) build, whose mock service
worker owns the same scope.

## HTTPS on a server

**The quickstart compose file** (`deploy/quickstart/docker-compose.yml`) has an
`https` profile: [Caddy](https://caddyserver.com) in front of the dashboard,
which gets and renews a Let's Encrypt certificate by itself. Point a domain's
DNS record at the server, open ports 80 and 443, and add to `.env`:

```
COMPOSE_PROFILES=https
HUB_DOMAIN=hub.example.com
AUTH_MODE=multi
```

then `docker compose up -d`. With the `postgres` profile on as well, list both:
`COMPOSE_PROFILES=postgres,https`.

**Any other reverse proxy** works the same way: terminate TLS and forward
everything to the dashboard container (nginx on port 80 inside it), which
already routes `/api` and the WebSockets. Keep response buffering off on the
proxy for `/api`, or streamed replies arrive all at once.

**Without a public domain**, a private network that issues certificates does
it too, for example `tailscale serve --bg 8080`, which publishes the dashboard
at `https://<machine>.<tailnet>.ts.net` to your own devices only.

A hub reachable from the internet must not run in the default `single` mode,
which has no login: use `AUTH_MODE=multi` (named accounts, a login screen) or at
least `token` ([identity](identity.md)).

## Gotchas

- **Plain HTTP on a LAN address installs nothing.** The page works, but there
  is no Install item, no offline shell and no microphone. Use HTTPS.
- **iOS keeps the installed app's storage apart from Safari's.** Signing in in
  Safari does not sign the installed app in; sign in once inside the app.
- **The service worker outlives a rollback of itself.** It is served with
  `no-cache` (docker/nginx.conf), so the browser checks it on every open; a
  changed `sw.js` takes over on the next load. To drop it on one device, clear
  the site's data in the browser settings.
- **The icons are drawn from `public/logo.svg`.** `public/icons/` holds the PNG
  renders the manifest and iOS need; render them again when the mark changes.
