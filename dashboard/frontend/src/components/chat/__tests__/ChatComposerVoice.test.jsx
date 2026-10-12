import { act, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider, useI18n } from '../../../i18n';

vi.mock('../../../api/steering', () => ({ steerRun: vi.fn() }));
vi.mock('../../../api/palette', () => ({
  getMyPreferences: () => Promise.reject({ response: { status: 404 } }),
  putMyPreferences: () => Promise.resolve({}),
}));
vi.mock('../../ContextMeter', () => ({ default: () => null }));
vi.mock('../../ContextEntityPicker', () => ({ default: () => null }));

// The microphone itself is ../useChatVoice; here it is a stand-in that says
// whether it is listening and hands back what was "said".
const voice = { available: true, talking: false, transcribing: false, level: 0, listening: false };
let lastOptions = null;
vi.mock('../useChatVoice', () => ({
  default: (options) => {
    lastOptions = options;
    return {
      ...voice,
      start: () => { voice.listening = true; voice.started += 1; },
      end: () => { voice.listening = false; voice.ended += 1; },
      active: () => voice.listening,
    };
  },
}));

import ChatComposer from '../ChatComposer';
import { ChatPageContext } from '../context';
import { useChatSteering } from '../useChatSteering';

// A voice message from the composer: the microphone stands in the Send
// button's place while the box is empty, Space held in the empty box records,
// and what was said is sent as a spoken message.

const sendMessage = vi.fn();

function Page({ input = '', extra = {} }) {
  const { t } = useI18n();
  const base = {
    addReferences: () => {}, attachMenuOpen: false, attachmentError: '', commandMenuIndex: 0,
    commandMenuOpen: false, commandSuggestions: [], composerPlaceholder: 'Message', contextKinds: [],
    contextUsage: null, currentTelegramBinding: null, fileInputRef: { current: null },
    handleKeyDown: vi.fn(), hasTarget: true, input, loading: false, onPickFiles: () => {},
    pendingAttachments: [], pendingReferences: [], pickerKind: null, removeAttachment: () => {},
    removeReference: () => {}, resizeTextarea: () => {}, selectCommand: () => {},
    selectedProject: '', selectedWorkspace: 'ws', sendAsBot: () => {}, sendMessage,
    setAttachMenuOpen: () => {}, setCommandMenuIndex: () => {}, setCommandMenuOpen: () => {},
    setInput: () => {}, setPickerKind: () => {}, stopGeneration: vi.fn(), t, telegramError: '',
    telegramReplyAllowed: false, telegramSending: false, textareaRef: { current: null },
    toggleAttachmentStore: () => {}, targetMode: 'agent', currentConvId: 'c1',
    currentConv: { id: 'c1', workspace: 'sales', messages: [] },
    conversations: [{ id: 'c1', messages: [] }], setConversations: vi.fn(),
    ...extra,
  };
  const steering = useChatSteering(base);
  return (
    <ChatPageContext.Provider value={{ ...base, steering }}>
      <ChatComposer />
    </ChatPageContext.Provider>
  );
}

const show = (props = {}) => render(<MemoryRouter><I18nProvider><Page {...props} /></I18nProvider></MemoryRouter>);

describe('ChatComposer voice messages', () => {
  beforeEach(() => {
    sendMessage.mockClear();
    Object.assign(voice, { available: true, talking: false, transcribing: false, listening: false, started: 0, ended: 0 });
  });

  it('shows the microphone while the box is empty and Send once something is typed', () => {
    const { unmount } = show();
    expect(screen.getByTestId('composer-mic')).toBeTruthy();
    expect(screen.queryByTitle('Send (Enter)')).toBeNull();
    unmount();
    show({ input: 'hello' });
    expect(screen.queryByTestId('composer-mic')).toBeNull();
    expect(screen.getByTitle('Send (Enter)')).toBeTruthy();
  });

  it('listens for the conversation\'s workspace', () => {
    show();
    expect(lastOptions.workspace).toBe('sales');
  });

  it('records while Space is held in the empty box', () => {
    show();
    const box = screen.getByPlaceholderText('Message');
    const down = fireEvent.keyDown(box, { key: ' ', code: 'Space' });
    expect(down).toBe(false);  // no space typed
    expect(voice.started).toBe(1);
    fireEvent.keyDown(box, { key: ' ', code: 'Space', repeat: true });
    expect(voice.started).toBe(1);
    fireEvent.keyUp(box, { key: ' ', code: 'Space' });
    expect(voice.ended).toBe(1);
  });

  it('leaves Space alone once something is typed', () => {
    show({ input: 'hi' });
    const down = fireEvent.keyDown(screen.getByPlaceholderText('Message'), { key: ' ', code: 'Space' });
    expect(down).toBe(true);
    expect(voice.started).toBe(0);
  });

  it('records while the button is held', () => {
    show();
    const mic = screen.getByTestId('composer-mic');
    fireEvent.pointerDown(mic, { button: 0, pointerId: 1 });
    expect(voice.started).toBe(1);
    fireEvent.pointerUp(mic, { pointerId: 1 });
    expect(voice.ended).toBe(1);
  });

  it('sends what was said as a spoken message', () => {
    show();
    act(() => { lastOptions.onText('turn the lights off'); });
    expect(sendMessage).toHaveBeenCalledWith('turn the lights off', { voice: true });
  });

  it('keeps the Send button where there is no way to listen', () => {
    voice.available = false;
    show();
    expect(screen.queryByTestId('composer-mic')).toBeNull();
  });
});
