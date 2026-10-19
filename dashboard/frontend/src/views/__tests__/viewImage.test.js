import { describe, expect, it, vi } from 'vitest';

vi.mock('html-to-image', () => ({ toBlob: vi.fn(() => Promise.resolve(new Blob(['png'], { type: 'image/png' }))) }));

import { toBlob } from 'html-to-image';
import { ViewImageError, captureViewImage, imageFilter, sanitizeFilename } from '../viewImage';

describe('sanitizeFilename', () => {
  it('drops path characters and adds .png', () => {
    expect(sanitizeFilename('Q3 / sales: "plan"?')).toBe('Q3 sales plan.png');
  });
  it('never returns an empty or hidden name', () => {
    expect(sanitizeFilename('')).toBe('view.png');
    expect(sanitizeFilename('///', 'chart')).toBe('chart.png');
    expect(sanitizeFilename('..secret')).toBe('secret.png');
  });
  it('caps the length', () => {
    expect(sanitizeFilename('a'.repeat(300)).length).toBeLessThanOrEqual(84);
  });
});

describe('imageFilter', () => {
  it('drops chrome elements and keeps the rest', () => {
    const chrome = document.createElement('div');
    chrome.setAttribute('data-view-chrome', '');
    expect(imageFilter(chrome)).toBe(false);
    expect(imageFilter(document.createElement('div'))).toBe(true);
    expect(imageFilter(document.createTextNode('x'))).toBe(true);
  });
});

describe('captureViewImage', () => {
  it('renders at pixel ratio 2 with the chrome filter', async () => {
    const el = document.createElement('div');
    const blob = await captureViewImage(el, { kind: 'chart' });
    expect(blob.size).toBeGreaterThan(0);
    const opts = toBlob.mock.calls.at(-1)[1];
    expect(opts.pixelRatio).toBe(2);
    expect(opts.filter).toBe(imageFilter);
    expect(opts.backgroundColor).toBeTruthy();
  });

  it('captures a slide at full stage size without the scale transform', async () => {
    const root = document.createElement('div');
    root.innerHTML = '<div data-testid="slide-stage"><div><div id="inner"></div></div></div>';
    await captureViewImage(root, { kind: 'slides' });
    const [node, opts] = toBlob.mock.calls.at(-1);
    expect(node.id).toBe('inner');
    expect([opts.width, opts.height]).toEqual([1280, 720]);
    expect(opts.style.transform).toBe('none');
  });

  it('refuses an iframe view with a clear code', async () => {
    const root = document.createElement('div');
    root.innerHTML = '<iframe></iframe>';
    await expect(captureViewImage(root, { kind: 'html' })).rejects.toMatchObject({ code: 'iframe' });
  });

  it('reports nothing rendered', async () => {
    await expect(captureViewImage(null)).rejects.toBeInstanceOf(ViewImageError);
  });
});
