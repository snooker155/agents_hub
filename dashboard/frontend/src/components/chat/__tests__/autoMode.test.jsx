import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { I18nProvider, useI18n } from '../../../i18n';

const summary = { auto_approve_allowed: true };
vi.mock('../../../api/workspaceSummary', () => ({
  loadWorkspaceSummary: () => Promise.resolve(summary),
}));
vi.mock('../../../api/palette', () => ({
  getMyPreferences: () => Promise.reject({ response: { status: 404 } }),
  putMyPreferences: () => Promise.resolve({}),
}));
vi.mock('../../../api/toolApprovals', () => ({
  decideToolApproval: vi.fn(), listRunApprovals: () => Promise.resolve({ data: { approvals: [] } }),
}));
vi.mock('../../ContextMeter', () => ({ default: () => null }));
vi.mock('../../ContextEntityPicker', () => ({ default: () => null }));

import useAutoMode from '../useAutoMode';
import ChatComposer from '../ChatComposer';
import ChatMessageList from '../ChatMessageList';
import { ChatPageContext } from '../context';
import { useChatSteering } from '../useChatSteering';
import { ToolApprovalCard } from '../ToolApprovalCard';
import { upsertApproval } from '../toolApprovals';

// Auto mode (common/auto_mode.py): a conversation whose person said yes in
// advance to every confirmation. A toggle under the box (and Shift+Tab), a
// banner while it is on, and cards that say they were approved by it.

describe('useAutoMode', () => {
  beforeEach(() => { summary.auto_approve_allowed = true; });

  it('keeps a draft before the first message and hands it over once', () => {
    const { result } = renderHook(() => useAutoMode({ currentConv: null, setConversations: vi.fn(), workspace: 'ws' }));
    expect(result.current.on).toBe(false);
    act(() => result.current.toggle());
    expect(result.current.on).toBe(true);
    let taken;
    act(() => { taken = result.current.takeDraft(); });
    expect(taken).toBe(true);
    expect(result.current.on).toBe(false);
  });

  it('stores the flag on the open conversation', () => {
    let convs = [{ id: 'c1', auto_approve: false }, { id: 'c2' }];
    const setConversations = vi.fn((fn) => { convs = fn(convs); });
    const { result } = renderHook(() => useAutoMode({ currentConv: convs[0], setConversations, workspace: 'ws' }));
    act(() => result.current.toggle());
    expect(convs).toEqual([{ id: 'c1', auto_approve: true }, { id: 'c2' }]);
  });

  it('is off and cannot be switched on where the workspace forbids it', async () => {
    summary.auto_approve_allowed = false;
    const setConversations = vi.fn();
    const { result } = renderHook(() => useAutoMode({
      currentConv: { id: 'c1', auto_approve: true }, setConversations, workspace: 'locked',
    }));
    await waitFor(() => expect(result.current.allowed).toBe(false));
    expect(result.current.on).toBe(false);
    act(() => result.current.toggle());
    expect(setConversations).not.toHaveBeenCalled();
  });
});

function Page({ autoMode, conv = { id: 'c1', messages: [] } }) {
  const { t } = useI18n();
  const base = {
    addReferences: () => {}, attachMenuOpen: false, attachmentError: '', commandMenuIndex: 0,
    commandMenuOpen: false, commandSuggestions: [], composerPlaceholder: 'Message', contextKinds: [],
    contextUsage: null, currentTelegramBinding: null, fileInputRef: { current: null },
    handleKeyDown: vi.fn(), hasTarget: true, input: '', loading: false, onPickFiles: () => {},
    pendingAttachments: [], pendingReferences: [], pickerKind: null, removeAttachment: () => {},
    removeReference: () => {}, resizeTextarea: () => {}, selectCommand: () => {},
    selectedProject: '', selectedWorkspace: 'ws', sendAsBot: () => {}, sendMessage: vi.fn(),
    setAttachMenuOpen: () => {}, setCommandMenuIndex: () => {}, setCommandMenuOpen: () => {},
    setInput: () => {}, setPickerKind: () => {}, stopGeneration: vi.fn(), t, telegramError: '',
    telegramReplyAllowed: false, telegramSending: false, textareaRef: { current: null },
    toggleAttachmentStore: () => {}, targetMode: 'agent', currentConvId: conv.id,
    conversations: [conv], currentConv: conv, setConversations: vi.fn(), autoMode,
    renderedMessages: [], messages: [], liveMessages: [], agents: [], flows: [], teams: [],
    artifacts: {}, runTimelineByRunId: {}, viewMode: 'chat',
  };
  const steering = useChatSteering(base);
  const page = { ...base, steering };
  return (
    <ChatPageContext.Provider value={page}>
      <ChatMessageList />
      <ChatComposer />
    </ChatPageContext.Provider>
  );
}

const show = (autoMode) => render(<MemoryRouter><I18nProvider><Page autoMode={autoMode} /></I18nProvider></MemoryRouter>);

describe('auto mode in the chat page', () => {
  beforeEach(() => { window.localStorage.clear(); });

  it('offers the toggle, and Shift+Tab in the box switches it', () => {
    const autoMode = { on: false, allowed: true, toggle: vi.fn(), setOn: vi.fn() };
    show(autoMode);
    const toggle = screen.getByTestId('auto-mode-toggle');
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    expect(toggle.textContent).toContain('Auto: off');
    fireEvent.click(toggle);
    fireEvent.keyDown(screen.getByPlaceholderText('Message'), { key: 'Tab', shiftKey: true });
    expect(autoMode.toggle).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('composer-box').className).not.toContain('border-amber-400');
  });

  it('shows no banner while it is on, only the toggle and an amber border on the box', () => {
    const autoMode = { on: true, allowed: true, toggle: vi.fn(), setOn: vi.fn() };
    show(autoMode);
    expect(screen.queryByText('Auto mode is on')).toBeNull();
    expect(screen.getByTestId('auto-mode-toggle').textContent).toContain('Auto: on');
    expect(screen.getByTestId('composer-box').className).toContain('border-amber-400');
  });

  it('disables the toggle where an administrator turned auto mode off', () => {
    show({ on: false, allowed: false, toggle: vi.fn(), setOn: vi.fn() });
    expect(screen.getByTestId('auto-mode-toggle').disabled).toBe(true);
  });
});

describe('a card auto mode approved', () => {
  it('keeps the auto flag from the stream and says who approved it', () => {
    const approvals = upsertApproval([], {
      approval_id: 'appr_9', tool: 'run_shell', status: 'approved', by: 'auto_mode', auto: true,
      decided_by_name: 'auto mode',
    });
    expect(approvals[0].auto).toBe(true);
    render(<I18nProvider><ToolApprovalCard approval={approvals[0]} /></I18nProvider>);
    expect(screen.getByTestId('tool-approval-auto').textContent).toContain('auto mode is on');
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull();
  });
});
