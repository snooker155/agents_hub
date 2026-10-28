/**
 * Theme context and its hook, split out so `ThemeContext.jsx` exports only the
 * provider component (see `stream.js` for the same split).
 *
 * Also the palette resolution logic: `resolvePalette` decides which of a
 * signed-in person's saved preference, their workspace's default, or the
 * built-in palette applies, and hands the answer to `src/lib/palette.js`'s
 * `applyPalette`/`clearPalette`. It lives here rather than in `palette.js`
 * because it is the one piece that needs to know the current light/dark
 * mode (a ramp is picked per mode) and reach the backend — `palette.js`
 * itself stays pure color math plus DOM application, no networking.
 *
 * `ThemeProvider` calls this on mount and whenever `theme` changes; Account.jsx
 * and WorkspaceDetails.jsx call it again after saving or resetting a palette,
 * so every place a palette can change funnels through the same resolution
 * order instead of three separate copies of it. `repaintPalette` is the
 * synchronous half: the last answer, re-applied for the other mode the
 * moment the theme toggles, so nothing waits on the network to look right.
 */
import { createContext, useContext } from 'react';
import { applyPalette, clearPalette } from '../lib/palette';
import { getMyPreferences } from '../api/palette';
import { checkStoredWorkspace, loadWorkspaceSummary } from '../api/workspaceSummary';

// A default value so a component that reads the theme outside the provider
// (a page rendered on its own in a test) sees the built-in setting instead
// of undefined.
export const ThemeContext = createContext({
  theme: 'system', setTheme: () => {}, resolvedMode: 'light',
});

/** Dispatched on `window` by WorkspaceContext when the selected workspace
 * changes, so the palette can be re-resolved against the new workspace's
 * default without polling. */
export const WORKSPACE_EVENT = 'agents-hub-workspace';

export const useTheme = () => useContext(ThemeContext);

/** Where this browser keeps a personal palette when there is no account to
 * save one against (AUTH_MODE single/token), or as a fallback whenever the
 * preferences endpoint is unreachable (signed out, offline). */
export const LOCAL_PALETTE_KEY = 'agents_hub_palette';

/** 'dark' | 'light', resolving 'system' against the OS preference right now. */
export function effectiveMode(theme) {
  if (theme === 'dark') return 'dark';
  if (theme === 'light') return 'light';
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

function hasAnyColor(palette) {
  return Boolean(palette) && ['brand', 'neutral', 'ok', 'danger'].some((k) => palette[k]);
}

function readLocalPalette() {
  try {
    const raw = window.localStorage.getItem(LOCAL_PALETTE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

/** Mirrors `WorkspaceContext.jsx`'s own persisted key: this module cannot use
 * `useWorkspace()` since `ThemeProvider` sits above `WorkspaceProvider` in
 * main.jsx, so it reads the same localStorage entry directly instead. */
function readSelectedWorkspace() {
  try {
    return window.localStorage.getItem('selectedWorkspace') || '';
  } catch {
    return '';
  }
}

async function personalPalette() {
  try {
    const { data } = await getMyPreferences();
    if (hasAnyColor(data?.palette)) return data.palette;
    if (data && 'palette' in data) return null; // an account with no preference set
  } catch {
    // 404 outside multi mode, 401 signed out, or offline: fall through.
  }
  return readLocalPalette();
}

async function workspacePalette() {
  // A remembered workspace this backend does not have is forgotten first,
  // rather than asked about.
  await checkStoredWorkspace();
  const workspace = readSelectedWorkspace();
  if (!workspace) return null;
  try {
    const summary = await loadWorkspaceSummary(workspace);
    const palette = summary?.palette;
    return hasAnyColor(palette) ? palette : null;
  } catch {
    return null;
  }
}

// The palette the last finished resolution landed on (`null` = the built-in
// one), kept so a light/dark switch can repaint at once with the other
// mode's ramp. Without it the `dark` class flips immediately while the
// inline custom properties keep the previous mode's values until the
// backend has answered again, and a dark page sits on light-grey surfaces
// (or a light page on dark ones) for that whole round trip; a failed or
// slow request left it that way. `known` stays false until the first
// resolution, when there is nothing to repaint yet and the stylesheet's own
// per-mode defaults are already right.
let lastResolved = { palette: null, known: false };

// Resolutions overlap (a theme toggle while the previous fetch is in
// flight, a workspace switch right after a save): only the newest one may
// paint, or the older answer lands last with the wrong mode's ramp.
let resolveSeq = 0;

function paint(palette, mode) {
  if (hasAnyColor(palette)) applyPalette(palette, mode);
  else clearPalette();
}

/**
 * Repaint the last resolved palette for the given theme setting, right now
 * and without asking anyone. Called by `ThemeProvider` in the same tick it
 * toggles the `dark` class, so the surfaces never show one mode's ramp
 * under the other mode's class. A no-op before the first resolution.
 */
export function repaintPalette(theme) {
  const mode = effectiveMode(theme);
  if (lastResolved.known) paint(lastResolved.palette, mode);
  return mode;
}

/**
 * Resolve and apply the palette for the given theme setting: a personal
 * preference first, then the current workspace's default, then nothing (the
 * built-in palette baked into src/theme.css). Safe to call as often as
 * needed — it is the single place this order is decided. A call overtaken
 * by a newer one returns `{ stale: true }` and paints nothing.
 */
export async function resolvePalette(theme) {
  resolveSeq += 1;
  const seq = resolveSeq;
  const personal = await personalPalette();
  const palette = hasAnyColor(personal) ? personal : await workspacePalette();
  // The mode is read after the awaits: with `system` the OS may have flipped
  // while the backend was answering, and the ramp must match what is on
  // the page now.
  const mode = effectiveMode(theme);
  const resolved = hasAnyColor(palette) ? palette : null;
  if (seq !== resolveSeq) return { palette: resolved, mode, stale: true };
  lastResolved = { palette: resolved, known: true };
  paint(resolved, mode);
  return { palette: resolved, mode };
}
