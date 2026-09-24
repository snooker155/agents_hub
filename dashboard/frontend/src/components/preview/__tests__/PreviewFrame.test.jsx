import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '../../../i18n';

// A preview ticket lives ten minutes and is only checked when the iframe
// makes a request, so PreviewFrame renews it (and swaps the src, which
// reloads the page) only when the current one would lapse before the next
// check, not on every tick.

const ok = (data) => Promise.resolve({ data });
const mintPreviewTicket = vi.fn(() => ok({ url: '/preview/T1/', expires_in: 600 }));
const renewPreviewTicket = vi.fn(() => ok({ url: '/preview/T2/', expires_in: 600 }));
vi.mock('../../../api/preview', () => ({
  mintPreviewTicket: (...a) => mintPreviewTicket(...a),
  renewPreviewTicket: (...a) => renewPreviewTicket(...a),
}));

import PreviewFrame, { RENEW_EVERY_MS } from '../PreviewFrame';

const TARGET = { kind: 'project', project_id: 'p1' };

beforeEach(() => {
  vi.useFakeTimers();
  mintPreviewTicket.mockClear();
  renewPreviewTicket.mockClear();
});

afterEach(() => vi.useRealTimers());

const src = (container) => container.querySelector('iframe')?.getAttribute('src');

describe('PreviewFrame renewal', () => {
  it('keeps the iframe until the ticket nears expiry, then swaps it once', async () => {
    const { container } = render(<I18nProvider><PreviewFrame target={TARGET} /></I18nProvider>);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(src(container)).toBe('/preview/T1/');

    // Six minutes left after the first tick: more than a tick and a margin.
    await act(async () => { await vi.advanceTimersByTimeAsync(RENEW_EVERY_MS); });
    expect(renewPreviewTicket).not.toHaveBeenCalled();
    expect(src(container)).toBe('/preview/T1/');

    // Two minutes left after the second: it would lapse before the third.
    await act(async () => { await vi.advanceTimersByTimeAsync(RENEW_EVERY_MS); });
    expect(renewPreviewTicket).toHaveBeenCalledWith({ ticket: 'T1', ...TARGET });
    expect(src(container)).toBe('/preview/T2/');
  });
});
