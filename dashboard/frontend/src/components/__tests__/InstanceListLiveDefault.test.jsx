import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The instances page and a service's replicas tab open on the working
// copies only; the state chips add up, and every copy is one click away.

vi.mock('../stream', () => ({ useStreamEvent: () => {}, useLiveRefetch: () => {} }));

const getInstances = vi.fn();
vi.mock('../../api', () => ({
  getInstances: (...args) => getInstances(...args),
  stopInstance: vi.fn(),
}));

import InstanceList from '../InstanceList';

const ROWS = [
  { instance_id: 'i1', agent_id: 'coder', state: 'stopped', label: 'coder #1' },
  { instance_id: 'i2', agent_id: 'coder', state: 'failed', label: 'coder #2' },
];

const show = (props) => render(
  <MemoryRouter><I18nProvider><InstanceList {...props} /></I18nProvider></MemoryRouter>,
);

const lastParams = () => getInstances.mock.calls.at(-1)[0];

describe('InstanceList state chips', () => {
  beforeEach(() => {
    getInstances.mockReset();
    getInstances.mockImplementation((params) => {
      const wanted = params.state ? params.state.split(',') : null;
      const items = ROWS.filter((r) => !wanted || wanted.includes(r.state));
      return Promise.resolve({ data: { items, total: items.length,
        counts: { live: 0, stopped: 1, failed: 1, total: 2 } } });
    });
  });

  it('asks for the working states only and offers every copy', async () => {
    show({ defaultLiveOnly: true });
    await screen.findByText('No running instances');
    expect(lastParams().state).toBe('starting,active,standby');
    fireEvent.click(screen.getByText('Show all (2)'));
    await screen.findByText('coder #1');
    expect(lastParams().state).toBeUndefined();
  });

  it('adds a picked chip to the ones already picked', async () => {
    show({ defaultLiveOnly: true });
    await screen.findByText('No running instances');
    fireEvent.click(screen.getByText('stopped'));
    await screen.findByText('coder #1');
    expect(lastParams().state).toBe('starting,active,standby,stopped');
    fireEvent.click(screen.getByText('failed'));
    await screen.findByText('coder #2');
    expect(lastParams().state).toBe('starting,active,standby,stopped,failed');
    // A second click takes a chip back out.
    fireEvent.click(screen.getByText('live'));
    await screen.findByText('coder #1');
    expect(lastParams().state).toBe('stopped,failed');
  });

  it('lists every copy without the default', async () => {
    show({});
    await screen.findByText('coder #1');
    expect(lastParams().state).toBeUndefined();
  });

  it('keeps search and sort on a service, without the kind and grouping', async () => {
    show({ serviceId: 's1', showAgentColumn: false, defaultLiveOnly: true });
    await screen.findByText('No running instances');
    expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument();
    expect(screen.queryByText('Group by agent')).toBeNull();
  });
});
