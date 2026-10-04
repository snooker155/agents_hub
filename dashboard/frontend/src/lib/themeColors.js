/**
 * Reads the live palette (src/theme.css's custom properties, or whatever
 * src/lib/palette.js's applyPalette() has overridden them with) for the
 * canvas-style renderers that used to hold plain hex constants: reactflow,
 * three.js scenes, chart configs. Those libraries take a color as a value at
 * construction time, not a CSS class, so they need the *resolved* color
 * rather than a `var(--brand-600)` string.
 */
import { useMemo, useSyncExternalStore } from 'react';
import { PALETTE_EVENT } from './palette';

/** One custom property, resolved against `<html>` right now. `fallback` is
 * what a test DOM (no theme.css loaded) or a missing property returns. */
export function cssVar(name, fallback = '') {
  if (typeof document === 'undefined' || typeof getComputedStyle !== 'function') return fallback;
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name);
    const trimmed = (value || '').trim();
    return trimmed || fallback;
  } catch {
    return fallback;
  }
}

/**
 * `spec` is a plain `{key: [cssVarName, fallback]}` map, defined once at
 * module scope (it must stay referentially stable across renders). Returns
 * `{key: resolvedColor}`, re-read whenever a palette is applied
 * (`agents-hub-palette` on `window`, dispatched by applyPalette/clearPalette)
 * or the `<html>` element's class changes (light/dark toggling). The palette
 * is an external store, so the hook subscribes to it with
 * useSyncExternalStore and resolves the colours from the store's version.
 */
export function useThemeColors(spec) {
  const version = useSyncExternalStore(subscribeToPalette, getPaletteVersion, getPaletteVersion);
  return useMemo(() => resolveSpec(spec, version), [spec, version]);
}

/** The mode actually applied to the page right now: 'dark' when `<html>` carries
 * the `dark` class (ThemeContext.jsx sets it for dark, and for system when the
 * system is dark), else 'light'. */
export function appliedMode() {
  if (typeof document !== 'undefined' && document.documentElement.classList.contains('dark')) {
    return 'dark';
  }
  return 'light';
}

/**
 * `appliedMode()` as a hook: re-renders the caller when the mode changes. The
 * same subscription as the colours above (the `dark` class is one of the
 * attributes it watches), so a renderer that embeds a third-party canvas
 * (vega, three.js) can rebuild it on a theme toggle instead of staying in the
 * palette it mounted with.
 */
export function useAppliedMode() {
  useSyncExternalStore(subscribeToPalette, getPaletteVersion, getPaletteVersion);
  return appliedMode();
}

// `version` is only a cache key: the custom properties themselves live
// outside React, and a new version is what makes them resolve again.
function resolveSpec(spec, version) { // eslint-disable-line no-unused-vars
  const out = {};
  for (const [key, entry] of Object.entries(spec)) {
    const [varName, fallback] = entry;
    out[key] = cssVar(varName, fallback);
  }
  return out;
}

// One shared subscription: every mounted hook reads the same counter, which
// moves on a palette event or a class change on <html>.
let paletteVersion = 0;
const listeners = new Set();

function bump() {
  paletteVersion += 1;
  for (const fn of listeners) fn();
}

function getPaletteVersion() {
  return paletteVersion;
}

let observer = null;

function subscribeToPalette(listener) {
  listeners.add(listener);
  if (listeners.size === 1 && typeof window !== 'undefined') {
    window.addEventListener(PALETTE_EVENT, bump);
    if (typeof MutationObserver !== 'undefined' && typeof document !== 'undefined') {
      observer = new MutationObserver(bump);
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    }
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && typeof window !== 'undefined') {
      window.removeEventListener(PALETTE_EVENT, bump);
      if (observer) { observer.disconnect(); observer = null; }
    }
  };
}
