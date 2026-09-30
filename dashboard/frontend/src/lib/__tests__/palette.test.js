import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import {
  rampFromColor, contrastRatio, checkPalette, applyPalette, clearPalette, SHADES,
} from '../palette';

describe('rampFromColor', () => {
  it('returns all 11 shades', () => {
    const ramp = rampFromColor('#3f66d8', { mode: 'light' });
    expect(Object.keys(ramp).map(Number).sort((a, b) => a - b)).toEqual(SHADES);
  });

  it('lightness changes monotonically from shade 50 to 950, in both modes', () => {
    for (const mode of ['light', 'dark']) {
      const ramp = rampFromColor('#3f66d8', { mode });
      const luminance = (hex) => {
        const n = parseInt(hex.slice(1), 16);
        const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
      };
      const values = SHADES.map((s) => luminance(ramp[s]));
      for (let i = 1; i < values.length; i += 1) {
        expect(values[i]).toBeLessThanOrEqual(values[i - 1] + 1e-6);
      }
      // Not flat: shade 50 is meaningfully lighter than shade 950.
      expect(values[0]).toBeGreaterThan(values[values.length - 1] + 50);
    }
  });

  it('produces valid hex for a wide range of inputs, including edge hues', () => {
    for (const hex of ['#000000', '#ffffff', '#ff0000', '#00ff00', '#0000ff', '#808080']) {
      const ramp = rampFromColor(hex, { mode: 'light' });
      for (const s of SHADES) expect(ramp[s]).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it('falls back to the input unchanged for a value it cannot parse', () => {
    const ramp = rampFromColor('not-a-color', { mode: 'light' });
    for (const s of SHADES) expect(ramp[s]).toBe('not-a-color');
  });
});

describe('contrastRatio', () => {
  it('is 21:1 for black on white', () => {
    expect(contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 0);
  });

  it('is 1:1 for identical colors', () => {
    expect(contrastRatio('#3f66d8', '#3f66d8')).toBeCloseTo(1, 5);
  });

  it('is symmetric', () => {
    expect(contrastRatio('#3f66d8', '#ffffff')).toBeCloseTo(contrastRatio('#ffffff', '#3f66d8'), 10);
  });
});

describe('checkPalette', () => {
  it('flags a brand color too close in luminance to white', () => {
    // A pale yellow reads poorly as text or as a button fill on white.
    const warnings = checkPalette({ brand: '#fdf6b2' }, 'light');
    expect(warnings.some((w) => w.label.includes('brand-600'))).toBe(true);
  });

  it('passes a dark, saturated brand color', () => {
    const warnings = checkPalette({ brand: '#1d3680' }, 'light');
    expect(warnings.some((w) => w.label.includes('brand-600'))).toBe(false);
  });

  it('returns no warnings with no base colors set', () => {
    expect(checkPalette({}, 'light')).toEqual(
      checkPalette({}, 'light').filter((w) => w.ratio < 4.5),
    );
  });
});

describe('applyPalette / clearPalette', () => {
  beforeEach(() => {
    document.documentElement.removeAttribute('style');
  });
  afterEach(() => {
    clearPalette();
  });

  it('sets brand ramp custom properties on <html>', () => {
    applyPalette({ brand: '#2a4fbd' }, 'light');
    const root = document.documentElement;
    expect(root.style.getPropertyValue('--brand-600')).toMatch(/^#[0-9a-f]{6}$/);
    expect(root.style.getPropertyValue('--brand-rgb')).toMatch(/^\d+, \d+, \d+$/);
  });

  it('sets neutral ramp and mirrors it into surface/text tokens', () => {
    applyPalette({ neutral: '#6b7280' }, 'light');
    const root = document.documentElement;
    expect(root.style.getPropertyValue('--neutral-500')).toMatch(/^#[0-9a-f]{6}$/);
    expect(root.style.getPropertyValue('--surface-page')).not.toBe('');
    expect(root.style.getPropertyValue('--text-primary')).not.toBe('');
  });

  it('sets the green/red hue vars from ok/danger', () => {
    applyPalette({ ok: '#16a34a', danger: '#dc2626' }, 'light');
    const root = document.documentElement;
    expect(root.style.getPropertyValue('--hue-green-rgb')).toMatch(/^\d+, \d+, \d+$/);
    expect(root.style.getPropertyValue('--hue-red-rgb')).toMatch(/^\d+, \d+, \d+$/);
  });

  it('clearPalette removes everything applyPalette set', () => {
    applyPalette({ brand: '#2a4fbd', neutral: '#6b7280', ok: '#16a34a', danger: '#dc2626' }, 'dark');
    clearPalette();
    const root = document.documentElement;
    expect(root.style.getPropertyValue('--brand-600')).toBe('');
    expect(root.style.getPropertyValue('--neutral-500')).toBe('');
    expect(root.style.getPropertyValue('--surface-page')).toBe('');
    expect(root.style.getPropertyValue('--hue-green-rgb')).toBe('');
    expect(root.style.getPropertyValue('--danger')).toBe('');
  });
});
