import { describe, it, expect } from 'vitest';
import {
  bytesToKb, clampLimit, kbToBytes, originsFromText, originsToText, previewDocument, shortVisitor,
} from '../widgetUtils';

describe('widgetUtils', () => {
  it('reads origins one per line or comma separated', () => {
    expect(originsFromText(' https://a.example\n\nhttps://b.example, http://localhost:3000 '))
      .toEqual(['https://a.example', 'https://b.example', 'http://localhost:3000']);
    expect(originsToText(['https://a.example', 'https://b.example'])).toBe('https://a.example\nhttps://b.example');
  });

  it('converts and clamps limits', () => {
    expect(bytesToKb(2 * 1024 * 1024)).toBe(2048);
    expect(kbToBytes('64')).toBe(65536);
    expect(clampLimit('500', { min: 1, max: 120, default: 6 })).toBe(120);
    expect(clampLimit('0', { min: 1, max: 120, default: 6 })).toBe(1);
    expect(clampLimit('abc', { min: 1, max: 120, default: 6 })).toBe(6);
    expect(shortVisitor('vis_abcdef123456')).toBe('abcdef');
  });

  it('builds a preview page that embeds the script with the ticket, escaped', () => {
    const doc = previewDocument({
      hub: 'http://localhost:5173', widgetId: 'wgt_1', publicKey: 'ahw_"x"', ticket: 't<1>',
      scriptPath: '/api/widgets/public/widget.js', text: '<b>preview</b>', lang: 'de',
    });
    const parsed = new DOMParser().parseFromString(doc, 'text/html');
    const script = parsed.querySelector('script');
    expect(script.getAttribute('src')).toBe('http://localhost:5173/api/widgets/public/widget.js');
    expect(script.dataset.key).toBe('ahw_"x"');
    expect(script.dataset.preview).toBe('t<1>');
    expect(script.dataset.hub).toBe('http://localhost:5173');
    expect(script.dataset.open).toBe('true');
    expect(parsed.querySelector('b')).toBeNull();
    expect(parsed.body.textContent).toContain('<b>preview</b>');
    expect(parsed.documentElement.lang).toBe('de');
  });
});
