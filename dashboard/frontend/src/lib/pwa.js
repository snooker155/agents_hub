/**
 * The installable app (docs/pwa.md): the service worker, and the browser's
 * offer to install the dashboard as an app.
 *
 * The worker is registered only in a production build served from a secure
 * origin (HTTPS, or localhost): not under the Vite dev server, whose modules
 * it would cache, and not in the demo build, where the mock service worker
 * owns the same scope.
 */
import { useSyncExternalStore } from 'react';

const SW_URL = `${import.meta.env.BASE_URL}sw.js`;

/** Whether this window runs as an installed app rather than in a browser tab. */
export function isStandalone(win = typeof window !== 'undefined' ? window : undefined) {
  if (!win) return false;
  try {
    return Boolean(win.matchMedia?.('(display-mode: standalone)').matches || win.navigator?.standalone);
  } catch {
    return false;
  }
}

/** iPhone or iPad Safari, which installs only through Share, Add to Home Screen. */
export function isIos(nav = typeof navigator !== 'undefined' ? navigator : undefined) {
  if (!nav) return false;
  const ua = nav.userAgent || '';
  // iPadOS reports itself as a Mac with touch.
  return /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && (nav.maxTouchPoints || 0) > 1);
}

export function registerServiceWorker({ enabled = import.meta.env.PROD && import.meta.env.VITE_DEMO !== '1' } = {}) {
  if (!enabled || typeof window === 'undefined' || !('serviceWorker' in navigator)) return;
  if (!window.isSecureContext) return;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register(SW_URL).catch((err) => {
      console.warn('Service worker registration failed:', err);
    });
  });
}

// ---------------------------------------------------------------------------
// The install offer. Chrome and Edge fire `beforeinstallprompt` once, early,
// often before React has mounted, so it is caught here at import time and kept
// until a button asks for it.
// ---------------------------------------------------------------------------
let deferredPrompt = null;
let installed = isStandalone();
const listeners = new Set();
const notify = () => listeners.forEach((fn) => fn());

if (typeof window !== 'undefined') {
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;
    notify();
  });
  window.addEventListener('appinstalled', () => {
    deferredPrompt = null;
    installed = true;
    notify();
  });
}

function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/**
 * What the install button can do here:
 *   'prompt'  the browser offered to install; `install()` shows its dialog
 *   'ios'     Safari on iOS: the person installs through Share, Add to Home Screen
 *   null      installed already, or the browser offers nothing
 */
export function installMode() {
  if (installed) return null;
  if (deferredPrompt) return 'prompt';
  if (isIos()) return 'ios';
  return null;
}

export async function install() {
  const prompt = deferredPrompt;
  if (!prompt) return false;
  deferredPrompt = null;
  notify();
  prompt.prompt();
  const choice = await prompt.userChoice.catch(() => null);
  return choice?.outcome === 'accepted';
}

export function useInstallMode() {
  return useSyncExternalStore(subscribe, installMode, () => null);
}
