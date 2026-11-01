import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider } from '../../i18n';

// The instance page's Refresh button: a live event can miss a carrier's
// change, so the header's state and the open tab can be read again by hand.

vi.mock('../../components/stream', () => ({
  useStream: () => ({ clientId: null }),
  useChannel: () => {},
  useStreamEvent: () => {},
}));
vi.mock('../../components/ComposerDock', () => ({ default: () => null }));
vi.mock('../../components/instances/InstanceComposer', () => ({ default: () => null }));
vi.mock('../../components/chat/AgentTranscript', () => ({ default: () => null }));
vi.mock('../../components/instances/ProcessTab', () => ({ default: () => null }));
vi.mock('../../components/instances/AccessTab', () => ({ default: () => null }));

const getInstance = vi.fn();
const getInstanceTimeline = vi.fn();
const getInstanceLogs = vi.fn();
vi.mock('../../api', () => ({
  deleteInstance: vi.fn(),
  getAgents: () => Promise.resolve({ data: [] }),
  getInstance: (...a) => getInstance(...a),
  getInstanceContext: () => Promise.resolve({ data: null }),
  getInstanceConversations: () => Promise.resolve({ data: { items: [{ conversation_id: 'main' }] } }),
  getInstanceLogs: (...a) => getInstanceLogs(...a),
  getInstanceRuns: () => Promise.resolve({ data: { items: [], total: 0 } }),
  getInstanceTimeline: (...a) => getInstanceTimeline(...a),
  interruptInstance: vi.fn(),
  messageInstance: vi.fn(),
  renameInstance: vi.fn(),
  restartInstance: vi.fn(),
  stopInstance: vi.fn(),
}));

import InstanceDetail from '../InstanceDetail';

const show = () => render(
  <MemoryRouter initialEntries={['/instances/inst-1']}>
    <I18nProvider>
      <Routes><Route path="/instances/:instanceId" element={<InstanceDetail />} /></Routes>
    </I18nProvider>
  </MemoryRouter>,
);

describe('InstanceDetail refresh', () => {
  beforeEach(() => {
    getInstance.mockReset();
    getInstanceTimeline.mockReset();
    getInstanceLogs.mockReset();
    getInstance
      .mockResolvedValueOnce({ data: { instance_id: 'inst-1', agent_id: 'coder', label: 'coder #1', state: 'active', resident: false } })
      .mockResolvedValue({ data: { instance_id: 'inst-1', agent_id: 'coder', label: 'coder #1', state: 'stopped', resident: false } });
    getInstanceTimeline.mockResolvedValue({ data: { items: [] } });
    getInstanceLogs.mockResolvedValue({ data: { logs: '' } });
  });

  it('reads the instance again and shows its new state', async () => {
    show();
    const button = await screen.findByTestId('instance-refresh');
    expect(getInstance).toHaveBeenCalledTimes(1);
    fireEvent.click(button);
    await vi.waitFor(() => expect(getInstance).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => expect(getInstanceTimeline).toHaveBeenCalledTimes(2));
    await screen.findByText(/stopped/i);
  });
});
