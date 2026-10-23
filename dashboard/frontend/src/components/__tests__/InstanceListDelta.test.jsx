import { act, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// A copy's state changes reach the list only as `instances.delta` rows: the
// row repaints in place, and a state change (counts, state filter, `live`
// order) or an unknown row also brings a refetch.

let emitDelta = null;
vi.mock('../stream', () => ({
  useStreamEvent: (_channel, type, fn) => { if (type === 'instances.delta') emitDelta = fn; },
  useLiveRefetch: () => {},
}));

const getInstances = vi.fn();
vi.mock('../../api', () => ({
  getInstances: (...args) => getInstances(...args),
  stopInstance: vi.fn(),
}));

import InstanceList from '../InstanceList';

const row = (over = {}) => ({
  instance_id: 'i1', agent_id: 'coder', workspace: 'default', kind: 'task',
  state: 'starting', label: 'coder #1', runs_count: 0, ...over,
});

const show = () => render(
  <MemoryRouter><I18nProvider><InstanceList /></I18nProvider></MemoryRouter>,
);

describe('InstanceList deltas', () => {
  beforeEach(() => {
    getInstances.mockReset();
    getInstances.mockResolvedValue({ data: { items: [row()], counts: {}, total: 1 } });
  });
  afterEach(() => { vi.useRealTimers(); });

  it('repaints the state badge and refetches once on a state change', async () => {
    show();
    await screen.findByText('coder #1');
    expect(getInstances).toHaveBeenCalledTimes(1);

    vi.useFakeTimers();
    act(() => { emitDelta({ items: [{ instance_id: 'i1', state: 'active' }] }); });
    act(() => { emitDelta({ items: [{ instance_id: 'i1', state: 'standby' }] }); });
    expect(screen.getByText('coder #1').closest('tr').textContent).toMatch(/standby/i);
    await act(async () => { vi.advanceTimersByTime(1100); });
    vi.useRealTimers();
    await waitFor(() => expect(getInstances).toHaveBeenCalledTimes(2));
  });

  it('does not refetch for activity on a held row', async () => {
    show();
    await screen.findByText('coder #1');
    vi.useFakeTimers();
    act(() => { emitDelta({ items: [{ instance_id: 'i1', state: 'starting', last_activity: 'reading' }] }); });
    await act(async () => { vi.advanceTimersByTime(1100); });
    expect(getInstances).toHaveBeenCalledTimes(1);
  });

  it('refetches for a row it does not hold', async () => {
    show();
    await screen.findByText('coder #1');
    vi.useFakeTimers();
    act(() => { emitDelta({ items: [{ instance_id: 'i2', state: 'active' }] }); });
    await act(async () => { vi.advanceTimersByTime(1100); });
    vi.useRealTimers();
    await waitFor(() => expect(getInstances).toHaveBeenCalledTimes(2));
  });
});
