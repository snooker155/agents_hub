import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { I18nProvider } from '../../i18n';

// The instance's box is the Chat page's composer for one copy: Enter sends,
// `/` opens the commands (without /clear), and a message typed while the copy
// answers goes into the running turn or waits for it.

const ok = (data) => Promise.resolve({ data });
const steerRun = vi.fn();
vi.mock('../../api/steering', () => ({
  steerRun: (...a) => steerRun(...a),
}));
vi.mock('../../api', () => ({
  getAgentDefinition: () => ok({ system_prompt: 'be brief' }),
  getContextKinds: () => ok([]),
  getMyPreferences: () => Promise.reject(new Error('no prefs')),
  putMyPreferences: () => ok({}),
}));
vi.mock('../../api/palette', () => ({
  getMyPreferences: () => Promise.reject(new Error('no prefs')),
  putMyPreferences: () => ok({}),
}));
vi.mock('../../api/files', () => ({ uploadWorkspaceFileObject: () => ok({}) }));
vi.mock('../ContextEntityPicker', () => ({ default: () => null }));
vi.mock('../files/WorkspaceFilePicker', () => ({ default: () => null }));

import InstanceComposer from '../instances/InstanceComposer';

const show = (props = {}) => {
  const onSend = vi.fn(() => Promise.resolve(true));
  render(
    <I18nProvider>
      <MemoryRouter>
        <InstanceComposer
          agents={[{ id: 'writer', name: 'Writer', commands: [{ name: '/draft', description: 'Draft it', template: '/draft ' }] }]}
          agentId="writer"
          workspace="ws"
          onSend={onSend}
          hint="direct"
          {...props}
        />
      </MemoryRouter>
    </I18nProvider>,
  );
  return onSend;
};

describe('InstanceComposer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    try { window.localStorage.setItem('agents_hub_steer_mode', 'inject'); } catch { /* no storage */ }
  });

  it('sends the box on Enter and empties it', async () => {
    const onSend = show();
    const box = screen.getByPlaceholderText('Write to this instance…');
    fireEvent.change(box, { target: { value: 'hello there' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(onSend).toHaveBeenCalledWith({
      message: 'hello there', attachments: [], references: [],
    }));
    await waitFor(() => expect(box.value).toBe(''));
  });

  it('offers the commands without /clear, the agent\'s own included', () => {
    show();
    const box = screen.getByPlaceholderText('Write to this instance…');
    fireEvent.change(box, { target: { value: '/' } });
    expect(screen.getByText('/help')).toBeTruthy();
    expect(screen.getByText('/config')).toBeTruthy();
    expect(screen.getByText('/draft')).toBeTruthy();
    expect(screen.queryByText('/clear')).toBeNull();
    expect(screen.queryByText('/new')).toBeNull();
  });

  it('offers /new only where the copy holds several conversations', () => {
    show({ canNewConversation: true });
    fireEvent.change(screen.getByPlaceholderText('Write to this instance…'), { target: { value: '/n' } });
    expect(screen.getByText('/new')).toBeTruthy();
  });

  it('steers the running turn instead of sending a new message', async () => {
    steerRun.mockImplementation(() => ok({ next: 'wait', message: { msg_id: 'm1' } }));
    const onSend = show({ streaming: true, liveRunId: 'run-9' });
    const box = screen.getByPlaceholderText('Message the agent while it works…');
    fireEvent.change(box, { target: { value: 'use the short form' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(steerRun).toHaveBeenCalledWith('run-9', 'use the short form', 'inject'));
    expect(onSend).not.toHaveBeenCalled();
  });

  it('queues through the copy when the turn cannot be steered', async () => {
    const onSend = show({ streaming: true, liveRunId: null });
    expect(screen.getByText('This turn cannot be steered, so your message is sent when it ends.')).toBeTruthy();
    const box = screen.getByPlaceholderText('Message the agent while it works…');
    fireEvent.change(box, { target: { value: 'later' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(onSend).toHaveBeenCalled());
    expect(steerRun).not.toHaveBeenCalled();
  });
});
