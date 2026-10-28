import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, waitFor, act, fireEvent, screen } from '@testing-library/react';
import { ThemeProvider } from '../ThemeContext';
import { useTheme } from '../theme';
import * as paletteApi from '../../api/palette';
import * as summaryApi from '../../api/workspaceSummary';
import { rampFromColor, hexToOklch } from '../../lib/palette';

function Probe() {
  const { resolvedMode, setTheme } = useTheme();
  return (
    <div>
      <div data-testid="mode">{resolvedMode}</div>
      <button type="button" onClick={() => setTheme('light')}>light</button>
      <button type="button" onClick={() => setTheme('dark')}>dark</button>
    </div>
  );
}

const cssVar = (name) => document.documentElement.style.getPropertyValue(name);
// OKLab lightness of an inline custom property, 0 (black) to 1 (white).
const lightness = (name) => hexToOklch(cssVar(name))[0];

describe('ThemeProvider palette resolution', () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute('style');
    document.documentElement.classList.remove('dark');
    // 404: no account preference to ask for (mirrors AUTH_MODE outside multi).
    vi.spyOn(paletteApi, 'getMyPreferences').mockRejectedValue({ response: { status: 404 } });
    vi.spyOn(summaryApi, 'loadWorkspaceSummary').mockRejectedValue({ response: { status: 404 } });
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

    // The dark ramp is the stylesheet's inverted one (50 deepest), and the
    // dark-mode accent alias reads shade 500, not 600 (see theme.js / palette.js).
    expect(lightness('--brand-50')).toBeLessThan(0.3);
    expect(cssVar('--brand')).toBe(cssVar('--brand-500'));
    expect(cssVar('--brand')).not.toBe(rampFromColor('#112233', { mode: 'light' })[600]);
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
    await waitFor(() => expect(summaryApi.loadWorkspaceSummary).not.toHaveBeenCalled());
    // The resolver remembers the previous mount's answer and repaints it
    // first (a remount must not flash the built-in colors), so the built-in
    // palette is what the finished resolution clears back to.
    await waitFor(() => expect(cssVar('--brand-600')).toBe(''));
  });

  it("applies the selected workspace's palette from its summary", async () => {
    localStorage.setItem('theme', 'light');
    localStorage.setItem('selectedWorkspace', 'jobs');
    summaryApi.loadWorkspaceSummary.mockResolvedValue({ name: 'jobs', palette: { brand: '#166534' } });

    render(<ThemeProvider><Probe /></ThemeProvider>);

    await waitFor(() => {
      expect(document.documentElement.style.getPropertyValue('--brand-600')).not.toBe('');
    });
    expect(summaryApi.loadWorkspaceSummary).toHaveBeenCalledWith('jobs');
    expect(document.documentElement.style.getPropertyValue('--brand-600'))
      .toBe(rampFromColor('#166534', { mode: 'light' })[600]);
  });

  it('repaints the other mode\'s ramp in the same tick as the theme toggle, before the backend answers again', async () => {
    localStorage.setItem('agents_hub_palette', JSON.stringify({ brand: '#112233', neutral: '#64748b' }));
    localStorage.setItem('theme', 'light');

    render(<ThemeProvider><Probe /></ThemeProvider>);
    await waitFor(() => expect(cssVar('--surface-page')).toBe(rampFromColor('#64748b', { mode: 'light' })[50]));

    // From now on the preferences request never answers: the repaint must
    // not depend on it.
    paletteApi.getMyPreferences.mockImplementation(() => new Promise(() => {}));

    await act(async () => { fireEvent.click(screen.getByText('dark')); });

    expect(document.documentElement.classList.contains('dark')).toBe(true);
    // The page surface is deep, not the light ramp's pale 50.
    expect(lightness('--surface-page')).toBeLessThan(0.25);
    expect(cssVar('--brand')).toBe(cssVar('--brand-500'));
  });

  it('drops a resolution overtaken by a newer one, so a slow answer cannot paint the previous mode', async () => {
    localStorage.setItem('agents_hub_palette', JSON.stringify({ brand: '#112233', neutral: '#64748b' }));
    localStorage.setItem('theme', 'dark');

    render(<ThemeProvider><Probe /></ThemeProvider>);
    await waitFor(() => expect(lightness('--surface-page')).toBeLessThan(0.25));

    // The next (light) resolution hangs on the backend; the one after it
    // (dark again) answers at once; then the slow one is released.
    let releaseSlow;
    paletteApi.getMyPreferences
      .mockImplementationOnce(() => new Promise((resolve) => { releaseSlow = resolve; }))
      .mockRejectedValue({ response: { status: 404 } });

    await act(async () => { fireEvent.click(screen.getByText('light')); });
    await act(async () => { fireEvent.click(screen.getByText('dark')); });
    await waitFor(() => expect(document.documentElement.classList.contains('dark')).toBe(true));

    await act(async () => {
      releaseSlow({ data: { palette: { brand: '#112233', neutral: '#64748b' } } });
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(lightness('--surface-page')).toBeLessThan(0.25);
  });
});
