import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { printHtml } from '../printView';

// A stand-in for the popup the exporter writes into.
function fakeWindow() {
  const w = {
    written: '',
    document: {
      open: vi.fn(),
      close: vi.fn(),
      write: vi.fn(function write(html) { w.written += html; }),
    },
    focus: vi.fn(),
    print: vi.fn(),
  };
  return w;
}

describe('printHtml', () => {
  let alertSpy;

  beforeEach(() => {
    vi.useFakeTimers();
    alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});
  });
  afterEach(() => vi.useRealTimers());

  it('writes a complete standalone document', () => {
    const w = fakeWindow();
    vi.spyOn(window, 'open').mockReturnValue(w);

    printHtml('My View', '<h1>Hi</h1>');

    expect(w.document.open).toHaveBeenCalled();
    expect(w.document.close).toHaveBeenCalled();
    expect(w.written).toContain('<!doctype html>');
    expect(w.written).toContain('<meta charset="utf-8">');
    expect(w.written).toContain('<title>My View</title>');
    expect(w.written).toContain('<h1>Hi</h1>');
    expect(w.written).toContain('@page');   // the print stylesheet came along
  });

  it('escapes the title so a view name cannot break out of the tag', () => {
    const w = fakeWindow();
    vi.spyOn(window, 'open').mockReturnValue(w);

    printHtml('</title><script>alert(1)</script>', '');

    expect(w.written).not.toContain('<script>');
    expect(w.written).toContain('&lt;/title&gt;&lt;script&gt;');
  });

  it('falls back to a generic title', () => {
    const w = fakeWindow();
    vi.spyOn(window, 'open').mockReturnValue(w);
    printHtml('', '<p/>');
    expect(w.written).toContain('<title>Document</title>');
  });

  it("appends the view's own css after the base stylesheet", () => {
    const w = fakeWindow();
    vi.spyOn(window, 'open').mockReturnValue(w);

    printHtml('t', '<p/>', '.slide { color: red; }');

    expect(w.written.indexOf('@page')).toBeLessThan(w.written.indexOf('color: red'));
  });

  it('prints once the new document has had a tick to lay out', () => {
    const w = fakeWindow();
    vi.spyOn(window, 'open').mockReturnValue(w);

    printHtml('t', '<p/>');
    expect(w.focus).toHaveBeenCalled();
    expect(w.print).not.toHaveBeenCalled();

    vi.runAllTimers();
    expect(w.print).toHaveBeenCalledTimes(1);
  });

  it('survives a popup that refuses to print', () => {
    const w = fakeWindow();
    w.print = vi.fn(() => { throw new Error('blocked'); });
    vi.spyOn(window, 'open').mockReturnValue(w);

    printHtml('t', '<p/>');
    expect(() => vi.runAllTimers()).not.toThrow();
  });

  it('tells the user to allow pop-ups when the window is blocked', () => {
    vi.spyOn(window, 'open').mockReturnValue(null);

    printHtml('t', '<p/>');

    expect(alertSpy).toHaveBeenCalledWith('Allow pop-ups to export as PDF.');
  });
});
