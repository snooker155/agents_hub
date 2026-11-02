import React, { useState, useEffect } from 'react';
import { ThemeContext, WORKSPACE_EVENT, repaintPalette, resolvePalette } from './theme';

function applyTheme(theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark');
}

const DARK_QUERY = '(prefers-color-scheme: dark)';

function systemMode() {
  try {
    return window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

// Only 'light' or 'dark' is a choice; anything else (nothing stored, or the
// 'system' an older build kept) means no choice yet, and the OS decides.
function storedChoice() {
  try {
    const v = localStorage.getItem('theme');
    return v === 'light' || v === 'dark' ? v : null;
  } catch {
    return null;
  }
}

// The phone's status bar and the installed app's title bar take this color
// (docs/pwa.md): the header's own surface, so the bar and the header read as one.
function syncThemeColor() {
  const meta = document.querySelector('meta[name="theme-color"]');
  if (!meta) return;
  const color = getComputedStyle(document.documentElement).getPropertyValue('--surface-card').trim();
  if (color) meta.setAttribute('content', color);
}

export function ThemeProvider({ children }) {
  // The theme is light or dark. Until the person picks one, it follows the OS.
  const [choice, setChoice] = useState(storedChoice);
  const [system, setSystem] = useState(systemMode);
  const theme = choice || system;

  // One effect, in this order: the `dark` class, then the last known palette
  // repainted for the new mode in the same tick (otherwise the inline
  // custom properties keep the old mode's ramp, and a dark page shows
  // light-grey surfaces until the backend has answered), then the full
  // re-resolution (personal preference, then the workspace default, then
  // the built-in one; `resolvePalette` in theme.js is the same function
  // Account.jsx and WorkspaceDetails.jsx call after a save or a reset).
  useEffect(() => {
    applyTheme(theme);
    repaintPalette(theme);
    syncThemeColor();
    // The palette may repaint the surfaces, so the color is read again after it.
    Promise.resolve(resolvePalette(theme)).catch(() => {}).finally(syncThemeColor);
  }, [theme]);

  // With no choice made, an OS switch between light and dark switches the page.
  useEffect(() => {
    if (choice) return undefined;
    let mq;
    try { mq = window.matchMedia(DARK_QUERY); } catch { return undefined; }
    const handler = () => setSystem(systemMode());
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, [choice]);

  // ThemeProvider sits above WorkspaceProvider in main.jsx, so it cannot read
  // `useWorkspace()` to notice a workspace switch on its own. WorkspaceContext
  // dispatches WORKSPACE_EVENT on window whenever the selection changes, and
  // the palette is re-resolved then, since a workspace may carry a default
  // palette of its own.
  useEffect(() => {
    const onWorkspace = () => { Promise.resolve(resolvePalette(theme)).catch(() => {}); };
    window.addEventListener(WORKSPACE_EVENT, onWorkspace);
    return () => window.removeEventListener(WORKSPACE_EVENT, onWorkspace);
  }, [theme]);

  // 'light' or 'dark' is kept as the choice; any other value drops it, so the
  // OS decides again.
  const setTheme = (t) => {
    const next = t === 'light' || t === 'dark' ? t : null;
    setChoice(next);
    try {
      if (next) localStorage.setItem('theme', next);
      else localStorage.removeItem('theme');
    } catch { /* this tab keeps it */ }
  };

  return (
    <ThemeContext.Provider value={{ theme, setTheme, resolvedMode: theme }}>
      {children}
    </ThemeContext.Provider>
  );
}
