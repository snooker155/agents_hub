import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider } from '../../i18n';

// The instances page and a service's replicas tab open on the working
// copies only; the stopped ones are one click away.

vi.mock('../stream', () => ({ useStreamEvent: () => {}, useLiveRefetch: () => {} }));

const getInstances = vi.fn();
vi.mock('../../api', () => ({
  getInstances: (...args) => getInstances(...args),
  stopInstance: vi.fn(),
}));

import InstanceList from '../InstanceList';

const show = (props) => render(
  <MemoryRouter><I18nProvider><InstanceList {...props} /></I18nProvider></MemoryRouter>,
);

describe('InstanceList live by default', () => {
  beforeEach(() => {
    getInstances.mockReset();
    getInstances.mockImplementation((params) => Promise.resolve({ data: {
      items: params.live ? [] : [{ instance_id: 'i1', agent_id: 'coder', state: 'stopped', label: 'coder #1' }],
      counts: { live: 0, stopped: 2, failed: 1 }, total: params.live ? 0 : 1,
    } }));
  });

  it('asks for live copies only and offers the stopped ones', async () => {
    show({ defaultLiveOnly: true });
    await screen.findByText('No running instances');
    expect(getInstances.mock.calls[0][0].live).toBe(true);
    fireEvent.click(screen.getByText('Show stopped too (3)'));
    await screen.findByText('coder #1');
    expect(getInstances.mock.calls.at(-1)[0].live).toBeUndefined();
  });

  it('filters to the stopped copies from their chip', async () => {
    show({ defaultLiveOnly: true });
    await screen.findByText('No running instances');
    fireEvent.click(screen.getByText('stopped'));
    await screen.findByText('coder #1');
    const params = getInstances.mock.calls.at(-1)[0];
    expect(params.state).toBe('stopped');
    expect(params.live).toBeUndefined();
  });

  it('lists every copy without the default', async () => {
    show({});
    await screen.findByText('coder #1');
    await waitFor(() => expect(getInstances.mock.calls[0][0].live).toBeUndefined());
  });
});
