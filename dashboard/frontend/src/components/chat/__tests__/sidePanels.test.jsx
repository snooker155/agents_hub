import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

// The right side of the chat's top bar and the columns it opens: Artifacts
// and Code with their counts, the view switch, and the Process button that
// exists in the chat view only. The columns open independently.

vi.mock('../../../api', () => ({ getChatRoute: vi.fn(() => Promise.resolve({ data: null })), listViews: vi.fn() }));
vi.mock('../../stream', () => ({ useLiveRefetch: vi.fn() }));
vi.mock('../targetPickers', () => ({
  AgentDropdown: () => null, FlowDropdown: () => null, ProjectDropdown: () => null, TeamDropdown: () => null,
}));
vi.mock('../CodePanel', () => ({ default: () => <div data-testid="code-panel-body" /> }));
vi.mock('../panels', () => ({
  ArtifactsPanel: ({ artifacts, views }) => (
    <div data-testid="artifacts-body">{Object.keys(artifacts).length} files, {views.length} views</div>
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
  artifacts: { 'a.py': {}, 'b.py': {} }, artifactsOpen: false, codeCount: 2, codeOpen: false,
  currentConv: null, currentConvId: 'c1', flows: [], graphRun: { active: null }, messages: [],
  processOpen: false, projects: [], selectableAgents: ['x'], selectedAgent: 'x', selectedFlow: '',
  selectedProject: '', selectedTeam: '', selectedWorkspace: 'ws', setArtifactsOpen: vi.fn(), setCodeOpen: vi.fn(),
  setConversations: vi.fn(), setProcessOpen: vi.fn(), setSelectedAgent: vi.fn(), setSelectedFlow: vi.fn(),
  setSelectedProject: vi.fn(), setSelectedTeam: vi.fn(), setTargetMode: vi.fn(), setViewMode: vi.fn(),
  targetMode: 'agent', teams: [], viewMode: 'chat', activeRunId: null, loadProcessData: vi.fn(),
  processError: '', processInsights: { message_runs: [] }, processLoading: false,
};

describe('chat top bar panels', () => {
  it('counts artifacts and code on their buttons and offers Process in the chat view only', () => {
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
    expect(counts).toEqual(['3', '2']);
    // Left to right: Process, Chat and Build, Artifacts and Code.
    const labels = screen.getAllByRole('button').map((b) => b.textContent.trim())
      .filter((x) => /^(Process|Chat|Build|Artifacts|Code)/.test(x));
    expect(labels.map((x) => x.split(/\d/)[0].trim())).toEqual(['Process', 'Chat', 'Build', 'Artifacts', 'Code']);

    // One panel at a time: opening either closes the other.
    fireEvent.click(screen.getByRole('button', { name: /Artifacts/ }));
    expect(base.setArtifactsOpen).toHaveBeenCalled();
    expect(base.setCodeOpen).toHaveBeenCalledWith(false);
    base.setArtifactsOpen.mockClear();
    fireEvent.click(screen.getByRole('button', { name: /^Code/ }));
    expect(base.setCodeOpen).toHaveBeenCalledWith(expect.any(Function));
    expect(base.setArtifactsOpen).toHaveBeenCalledWith(false);

    rerender(<I18nProvider><MemoryRouter><Bar viewMode="build" /></MemoryRouter></I18nProvider>);
    expect(screen.queryByRole('button', { name: /Process/ })).not.toBeInTheDocument();
    expect(screen.getAllByTestId('panel-count')).toHaveLength(2);
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
    const { unmount } = draw({ codeOpen: true, processOpen: true });
    expect(screen.getByTestId('chat-code-column')).toBeInTheDocument();
    expect(screen.getByTestId('chat-process-column')).toBeInTheDocument();
    expect(screen.getByTestId('chat-code-column').compareDocumentPosition(screen.getByTestId('chat-process-column'))
      & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    unmount();
    draw({ artifactsOpen: true, processOpen: true });
    expect(screen.getByTestId('chat-artifacts-column')).toBeInTheDocument();
    expect(screen.getByTestId('chat-process-column')).toBeInTheDocument();
    expect(screen.getByTestId('artifacts-body')).toHaveTextContent('2 files, 1 views');
  });

  it('switches the artifacts column between files and diff from its header', () => {
    draw({ artifactsOpen: true });
    const diffTab = screen.getByRole('tab', { name: 'Diff' });
    expect(screen.getByRole('tab', { name: 'Files' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(diffTab);
    expect(diffTab).toHaveAttribute('aria-selected', 'true');
    expect(localStorage.getItem('agent_hub_chat_artifacts_mode')).toBe('diff');
    localStorage.removeItem('agent_hub_chat_artifacts_mode');
  });

  it('resolves a stale pair from an older saved state to the code panel', () => {
    draw({ artifactsOpen: true, codeOpen: true });
    expect(screen.getByTestId('chat-code-column')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-artifacts-column')).not.toBeInTheDocument();
  });

  it('leaves the process column out of the build view', () => {
    draw({ artifactsOpen: true, processOpen: true, viewMode: 'build' });
    expect(screen.getByTestId('chat-artifacts-column')).toBeInTheDocument();
    expect(screen.queryByTestId('chat-process-column')).not.toBeInTheDocument();
  });
});
