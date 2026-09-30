import { render, screen, fireEvent, act } from '@testing-library/react';
import { describe, it, expect, vi, afterEach } from 'vitest';

import { I18nProvider } from '../../../i18n';
import BrowserViewport from '../BrowserViewport';
import { toViewport } from '../viewport';

// The viewport shows the service's 1280x800 frame at whatever width the page
// gives it, so every click has to be mapped back through that ratio before it
// reaches the page: a click in the middle of a 640px wide picture is a click
// at (640, 400) in the browser.

const FRAME = { url: 'https://example.com/', title: 'Example', width: 1280, height: 800,
  image: 'data:image/jpeg;base64,AAAA' };

const show = (props) => render(
  <I18nProvider>
    <BrowserViewport frame={FRAME} {...props} />
  </I18nProvider>,
);

const sized = (el, width = 640, height = 400) => {
  el.getBoundingClientRect = () => ({ left: 100, top: 50, width, height, right: 100 + width, bottom: 50 + height });
};

afterEach(() => vi.useRealTimers());

describe('toViewport', () => {
  it('scales and clamps', () => {
    const rect = { left: 0, top: 0, width: 640, height: 400 };
    expect(toViewport(320, 200, rect)).toEqual({ x: 640, y: 400 });
    expect(toViewport(-5, 999, rect)).toEqual({ x: 0, y: 799 });
  });
});

describe('BrowserViewport', () => {
  it('maps a click to viewport coordinates and sends it', () => {
    const onInput = vi.fn();
    show({ controllable: true, onInput });
    const el = screen.getByTestId('browser-viewport');
    sized(el);
    fireEvent.click(el, { clientX: 100 + 320, clientY: 50 + 100, detail: 1 });
    expect(onInput).toHaveBeenCalledWith({ kind: 'click', x: 640, y: 200 });
    expect(screen.getByText('You are in control')).toBeTruthy();
  });

  it('sends nothing when it is only watching', () => {
    const onInput = vi.fn();
    show({ controllable: false, onInput });
    const el = screen.getByTestId('browser-viewport');
    sized(el);
    fireEvent.click(el, { clientX: 200, clientY: 100, detail: 1 });
    fireEvent.keyDown(el, { key: 'a' });
    expect(onInput).not.toHaveBeenCalled();
    expect(screen.queryByText('You are in control')).toBeNull();
  });

  it('types printable characters in a batch and presses special keys in order', () => {
    vi.useFakeTimers();
    const onInput = vi.fn();
    show({ controllable: true, onInput });
    const el = screen.getByTestId('browser-viewport');
    fireEvent.keyDown(el, { key: 'h' });
    fireEvent.keyDown(el, { key: 'i' });
    fireEvent.keyDown(el, { key: 'Enter' });
    expect(onInput.mock.calls.map((c) => c[0])).toEqual([
      { kind: 'type', text: 'hi' },
      { kind: 'key', key: 'Enter' },
    ]);
    fireEvent.keyDown(el, { key: 'x' });
    act(() => { vi.advanceTimersByTime(200); });
    expect(onInput).toHaveBeenLastCalledWith({ kind: 'type', text: 'x' });
  });

  it('keeps the last frame on screen while the next one loads', () => {
    show({ loading: true });
    expect(screen.getByAltText('Example').getAttribute('src')).toBe(FRAME.image);
  });
});
