import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { I18nProvider, useI18n } from '../../../i18n';

const steerRun = vi.fn(() => Promise.resolve({ data: { message: { msg_id: 'm1' } } }));
vi.mock('../../../api/steering', () => ({ steerRun: (...a) => steerRun(...a) }));
vi.mock('../../../api/palette', () => ({
  getMyPreferences: () => Promise.reject({ response: { status: 404 } }),
  putMyPreferences: () => Promise.resolve({}),
}));
vi.mock('../../ContextMeter', () => ({ default: () => null }));
vi.mock('../../ContextEntityPicker', () => ({ default: () => null }));

import ChatComposer from '../ChatComposer';
import { ChatPageContext } from '../context';
import { useChatSteering } from '../useChatSteering';

// While a turn runs the box stays usable and a message goes to the turn in
// the chosen mode instead of waiting for the Send button to come back.

function Page({ loading, input = '', setInput = () => {}, extra = {} }) {
  const { t } = useI18n();
  const base = {
    addReferences: () => {}, attachMenuOpen: false, attachmentError: '', commandMenuIndex: 0,
    commandMenuOpen: false, commandSuggestions: [], composerPlaceholder: 'Message', contextKinds: [],
    contextUsage: null, currentTelegramBinding: null, fileInputRef: { current: null },
    handleKeyDown: vi.fn(), hasTarget: true, input, loading, onPickFiles: () => {},
    pendingAttachments: [], pendingReferences: [], pickerKind: null, removeAttachment: () => {},
    removeReference: () => {}, resizeTextarea: () => {}, selectCommand: () => {},
    selectedProject: '', selectedWorkspace: 'ws', sendAsBot: () => {}, sendMessage: vi.fn(),
    setAttachMenuOpen: () => {}, setCommandMenuIndex: () => {}, setCommandMenuOpen: () => {},
    setInput, setPickerKind: () => {}, stopGeneration: vi.fn(), t, telegramError: '',
    telegramReplyAllowed: false, telegramSending: false, textareaRef: { current: null },
    toggleAttachmentStore: () => {}, targetMode: 'agent', currentConvId: 'c1',
    conversations: [{ id: 'c1', messages: [
      { id: 'u1', role: 'user', content: 'go' },
      { id: 'a1', role: 'agent', content: '', run_id: 'run-7' },
    ] }],
    setConversations: vi.fn(),
    ...extra,
  };
  const steering = useChatSteering(base);
  const page = { ...base, steering };
  return (
    <ChatPageContext.Provider value={page}>
      <ChatComposer />
    </ChatPageContext.Provider>
  );
}

const show = (props) => render(
  <MemoryRouter><I18nProvider><Page {...props} /></I18nProvider></MemoryRouter>,
);

describe('ChatComposer while a turn runs', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
  });

  it('keeps the box open and offers the three modes', () => {
    show({ loading: true });
    const box = screen.getByPlaceholderText('Message the agent while it works…');
    expect(box.disabled).toBe(false);
    expect(screen.getByRole('radio', { name: 'Steer' }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('radio', { name: 'Interrupt' })).toBeTruthy();
    expect(screen.getByRole('radio', { name: 'Queue' })).toBeTruthy();
  });

  it('sends Enter to the running turn in the chosen mode', async () => {
    show({ loading: true, input: 'check the totals' });
    fireEvent.keyDown(screen.getByPlaceholderText('Message the agent while it works…'), { key: 'Enter' });
    await waitFor(() => expect(steerRun).toHaveBeenCalledWith('run-7', 'check the totals', 'inject'));
  });

  it('shows no mode bar when nothing is running', () => {
    show({ loading: false });
    expect(screen.queryByTestId('steer-bar')).toBeNull();
    expect(screen.getByPlaceholderText('Message').disabled).toBe(false);
  });
});
