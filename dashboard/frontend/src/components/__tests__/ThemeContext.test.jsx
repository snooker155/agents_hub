import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { ThemeProvider } from '../ThemeContext';
import { useTheme } from '../theme';
import * as paletteApi from '../../api/palette';
import * as api from '../../api';
import { rampFromColor } from '../../lib/palette';

function Probe() {
  const { resolvedMode } = useTheme();
  return <div data-testid="mode">{resolvedMode}</div>;
}

describe('ThemeProvider palette resolution', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('style');
    document.documentElement.classList.remove('dark');
    // 404: no account preference to ask for (mirrors AUTH_MODE outside multi).
    vi.spyOn(paletteApi, 'getMyPreferences').mockRejectedValue({ response: { status: 404 } });
    vi.spyOn(api, 'getWorkspaceSettingsOverrides').mockRejectedValue({ response: { status: 404 } });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    document.documentElement.removeAttribute('style');
    document.documentElement.classList.remove('dark');
  });

  it('applies the dark-mode ramp when the theme is dark, from a localStorage personal preference', async () => {
    localStorage.setItem('agents_hub_palette', JSON.stringify({ brand: '#112233' }));
    localStorage.setItem('theme', 'dark');

    render(<ThemeProvider><Probe /></ThemeProvider>);

    await waitFor(() => {
      expect(document.documentElement.style.getPropertyValue('--brand-500')).not.toBe('');
    });

    const expected = rampFromColor('#112233', { mode: 'dark' })[500];
    expect(document.documentElement.style.getPropertyValue('--brand-500')).toBe(expected);
    // The dark-mode accent alias reads shade 500, not 600 (see theme.js / palette.js).
    expect(document.documentElement.style.getPropertyValue('--brand')).toBe(expected);
    expect(document.documentElement.classList.contains('dark')).toBe(true);
  });

  it('applies the light-mode ramp (a different chroma) when the theme is light', async () => {
    localStorage.setItem('agents_hub_palette', JSON.stringify({ brand: '#112233' }));
    localStorage.setItem('theme', 'light');

    render(<ThemeProvider><Probe /></ThemeProvider>);

    await waitFor(() => {
      expect(document.documentElement.style.getPropertyValue('--brand-600')).not.toBe('');
    });

    const expected = rampFromColor('#112233', { mode: 'light' })[600];
    expect(document.documentElement.style.getPropertyValue('--brand-600')).toBe(expected);
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('leaves the built-in palette alone with no saved preference anywhere', async () => {
    localStorage.setItem('theme', 'light');

    render(<ThemeProvider><Probe /></ThemeProvider>);

    await waitFor(() => expect(paletteApi.getMyPreferences).toHaveBeenCalled());
    await waitFor(() => expect(api.getWorkspaceSettingsOverrides).not.toHaveBeenCalled());
    expect(document.documentElement.style.getPropertyValue('--brand-600')).toBe('');
  });
});
