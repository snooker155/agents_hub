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
 * order instead of three separate copies of it.
 */
import { createContext, useContext } from 'react';
import { applyPalette, clearPalette } from '../lib/palette';
import { getMyPreferences } from '../api/palette';
import { loadWorkspaceSummary } from '../api/workspaceSummary';

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

/**
 * Resolve and apply the palette for the given theme setting: a personal
 * preference first, then the current workspace's default, then nothing (the
 * built-in palette baked into src/theme.css). Safe to call as often as
 * needed — it is the single place this order is decided.
 */
export async function resolvePalette(theme) {
  const mode = effectiveMode(theme);
  const personal = await personalPalette();
  const palette = hasAnyColor(personal) ? personal : await workspacePalette();
  if (hasAnyColor(palette)) applyPalette(palette, mode);
  else clearPalette();
  return { palette: hasAnyColor(palette) ? palette : null, mode };
}
