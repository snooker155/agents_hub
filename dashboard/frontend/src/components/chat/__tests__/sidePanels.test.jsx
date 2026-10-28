import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

// The right side of the chat's top bar and the columns it opens: Artifacts
// (views, code and files) with its count, the view switch, and the Process
// button that exists in the chat view only. The columns open independently.

vi.mock('../../../api', () => ({ getChatRoute: vi.fn(() => Promise.resolve({ data: null })), listViews: vi.fn() }));
vi.mock('../../stream', () => ({ useLiveRefetch: vi.fn() }));
vi.mock('../targetPickers', () => ({
  AgentDropdown: () => null, FlowDropdown: () => null, ProjectDropdown: () => null, TeamDropdown: () => null,
  ModeDropdown: ({ value }) => <span data-testid="mode-dropdown-picker">{value}</span>,
}));
vi.mock('../useChatCode', () => ({ useChatCode: () => ({ count: 2 }) }));
vi.mock('../panels', async (importOriginal) => ({
  // The real Files / Diff switch: the header's tabs are under test.
  ArtifactsModeSwitch: (await importOriginal()).ArtifactsModeSwitch,
  ArtifactsPanel: ({ artifacts, views, code }) => (
    <div data-testid="artifacts-body">{Object.keys(artifacts).length} files, {views.length} views, {code.count} code</div>
  ),
  ProcessPanelContent: () => <div data-testid="process-body" />,
}));

import { MemoryRouter } from 'react-router-dom';
import { I18nProvider, useI18n } from '../../../i18n';
import { ChatPageContext } from '../context';
import ChatTopBar from '../ChatTopBar';
import ChatSidePanel from '../ChatSidePanel';

const base = {
  agentModel: '', agentProvider: '', agentTopology: null, artifactCount: 3, artifactViews: [{ view_id: 'v1' }],
  artifacts: { 'a.py': {}, 'b.py': {} }, artifactsOpen: false,
  currentConv: null, currentConvId: 'c1', flows: [], graphRun: { active: null }, messages: [],
  processOpen: false, projects: [], selectableAgents: ['x'], selectedAgent: 'x', selectedFlow: '',
  selectedProject: '', selectedTeam: '', selectedWorkspace: 'ws', setArtifactsOpen: vi.fn(),
  setConversations: vi.fn(), setProcessOpen: vi.fn(), setSelectedAgent: vi.fn(), setSelectedFlow: vi.fn(),
  setSelectedProject: vi.fn(), setSelectedTeam: vi.fn(), setTargetMode: vi.fn(), setViewMode: vi.fn(),
  targetMode: 'agent', teams: [], viewMode: 'chat', activeRunId: null, loadProcessData: vi.fn(),
  processError: '', processInsights: { message_runs: [] }, processLoading: false,
};

describe('chat top bar panels', () => {
  it('counts artifacts, code among them, on one button and offers Process in the chat view only', () => {
    function Bar(props) {
      const { t } = useI18n();
      return (
        <ChatPageContext.Provider value={{ ...base, ...props, t }}>
          <ChatTopBar />
        </ChatPageContext.Provider>
      );
    }
    const { rerender } = render(<I18nProvider><MemoryRouter><Bar /></MemoryRouter></I18nProvider>);
    const counts = screen.getAllByTestId('panel-count').map((el) => el.textContent);
    expect(counts).toEqual(['3']);
    // Left to right: Process, Chat and Build, Artifacts; no Code button.
    const labels = screen.getAllByRole('button').map((b) => b.textContent.trim())
      .filter((x) => /^(Process|Chat|Build|Artifacts|Code)/.test(x));
    expect(labels.map((x) => x.split(/\d/)[0].trim())).toEqual(['Process', 'Chat', 'Build', 'Artifacts']);

    fireEvent.click(screen.getByRole('button', { name: /Artifacts/ }));
    expect(base.setArtifactsOpen).toHaveBeenCalledWith(expect.any(Function));

    // The mode toggle stays buttons while Artifacts is closed.
    expect(screen.queryByTestId('mode-dropdown')).not.toBeInTheDocument();
    expect(screen.getByTestId('mode-toggle')).not.toHaveClass('hidden');

    rerender(<I18nProvider><MemoryRouter><Bar viewMode="build" /></MemoryRouter></I18nProvider>);
    expect(screen.queryByRole('button', { name: /Process/ })).not.toBeInTheDocument();
    expect(screen.getAllByTestId('panel-count')).toHaveLength(1);
  });
});

describe('the mode toggle beside an open Artifacts column', () => {
  it('turns into a dropdown under 1800 CSS px, the buttons kept for a wide screen', () => {
    function Bar(props) {
      const { t } = useI18n();
      return (
        <ChatPageContext.Provider value={{ ...base, ...props, t }}>
          <ChatTopBar />
        </ChatPageContext.Provider>
      );
    }
    render(<I18nProvider><MemoryRouter><Bar artifactsOpen targetMode="flow" /></MemoryRouter></I18nProvider>);
    expect(screen.getByTestId('mode-dropdown')).toHaveClass('min-[1800px]:hidden');
    expect(screen.getByTestId('mode-dropdown-picker')).toHaveTextContent('flow');
    expect(screen.getByTestId('mode-toggle')).toHaveClass('hidden', 'min-[1800px]:block');
  });
});

describe('chat side columns', () => {
  function Panel(props) {
    const { t } = useI18n();
    return (
      <ChatPageContext.Provider value={{ ...base, ...props, t }}>
        <ChatSidePanel />
      </ChatPageContext.Provider>
    );
  }
  const draw = (props) => render(<I18nProvider><MemoryRouter><Panel {...props} /></MemoryRouter></I18nProvider>);

  it('renders nothing when every panel is closed', () => {
    draw({});
    expect(screen.queryByTestId('chat-artifacts-column')).not.toBeInTheDocument();
  });

  it('keeps the process a column of its own next to the open panel', () => {
    draw({ artifactsOpen: true, processOpen: true });
    expect(screen.getByTestId('chat-artifacts-column')).toBeInTheDocument();
    expect(screen.getByTestId('chat-process-column')).toBeInTheDocument();
    expect(screen.getByTestId('chat-artifacts-column').compareDocumentPosition(screen.getByTestId('chat-process-column'))
      & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId('artifacts-body')).toHaveTextContent('2 files, 1 views, 2 code');
  });

  it('switches the artifacts column between files and diff from its header', () => {
    draw({ artifactsOpen: true });
    const diffTab = screen.getByRole('tab', { name: 'Diff' });
    expect(screen.getByRole('tab', { name: 'File' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(diffTab);
    expect(diffTab).toHaveAttribute('aria-selected', 'true');
    expect(localStorage.getItem('agent_hub_chat_artifacts_mode')).toBe('diff');
    localStorage.removeItem('agent_hub_chat_artifacts_mode');
  });

  it('takes the whole width in the chat\'s place on request, and keeps that choice', () => {
    const setArtifactsFull = vi.fn();
    const { unmount } = draw({ artifactsOpen: true, processOpen: true, setArtifactsFull });
    expect(screen.getByTestId('chat-artifacts-column').className).toContain('min-w-[391px]');
    fireEvent.click(screen.getByTestId('artifacts-full-toggle'));
    expect(setArtifactsFull).toHaveBeenCalledWith(expect.any(Function));
    unmount();
    draw({ artifactsOpen: true, artifactsFull: true, processOpen: true, setArtifactsFull });
    const column = screen.getByTestId('chat-artifacts-column');
    expect(column.className).toContain('flex-1 min-w-0');
    expect(column.className).not.toContain('min-w-[391px]');
    // Nothing beside it, the Process column included.
    expect(screen.queryByTestId('chat-process-column')).not.toBeInTheDocument();
    expect(screen.getByTestId('artifacts-full-toggle')).toHaveAttribute('aria-pressed', 'true');
    // Closing it keeps the choice: it opens the whole width next time.
    setArtifactsFull.mockClear();
    fireEvent.click(screen.getByTitle('Close panel'));
    expect(base.setArtifactsOpen).toHaveBeenCalledWith(false);
    expect(setArtifactsFull).not.toHaveBeenCalled();
  });

  it('leaves the process column out of the build view', () => {
    draw({ artifactsOpen: true, processOpen: true, viewMode: 'build' });
    expect(screen.getByTestId('chat-artifacts-column')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-process-column')).not.toBeInTheDocument();
  });
});
