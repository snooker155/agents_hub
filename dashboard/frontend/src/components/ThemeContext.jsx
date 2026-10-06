import React, { useState, useEffect } from 'react';
import { ThemeContext, WORKSPACE_EVENT, effectiveMode, resolvePalette } from './theme';

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'dark') {
    root.classList.add('dark');
  } else if (theme === 'light') {
    root.classList.remove('dark');
  } else {
    // system
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    if (prefersDark) root.classList.add('dark');
    else root.classList.remove('dark');
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
  const [theme, setThemeState] = useState(() => localStorage.getItem('theme') || 'system');

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Watch system preference changes when theme === 'system'
  useEffect(() => {
    if (theme !== 'system') return;
    const mq = window.matchMedia('(prefers-color-scheme: dark)');
    const handler = () => { applyTheme('system'); syncThemeColor(); };
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, [theme]);

  // The resolved palette (personal preference, then the workspace default,
  // then the built-in one) needs the dark/light ramp a mode switch picks, so
  // it is re-resolved on mount and whenever `theme` changes. `resolvePalette`
  // (theme.js) is the same function Account.jsx and WorkspaceDetails.jsx call
  // again right after they save or reset a palette.
  useEffect(() => {
    // The palette may repaint the surfaces, so the color is read after it.
    Promise.resolve(resolvePalette(theme)).catch(() => {}).finally(syncThemeColor);
  }, [theme]);

  // ThemeProvider sits above WorkspaceProvider in main.jsx, so it cannot read
  // `useWorkspace()` to notice a workspace switch on its own. WorkspaceContext
  // dispatches WORKSPACE_EVENT on window whenever the selection changes, and
  // the palette is re-resolved then, since a workspace may carry a default
  // palette of its own.
  useEffect(() => {
    const onWorkspace = () => resolvePalette(theme);
    window.addEventListener(WORKSPACE_EVENT, onWorkspace);
    return () => window.removeEventListener(WORKSPACE_EVENT, onWorkspace);
  }, [theme]);

  const setTheme = (t) => {
    setThemeState(t);
    localStorage.setItem('theme', t);
  };

  return (
    <ThemeContext.Provider value={{ theme, setTheme, resolvedMode: effectiveMode(theme) }}>
      {children}
    </ThemeContext.Provider>
  );
}
