import { AgentDropdown, FlowDropdown, TeamDropdown } from './targetPickers';
import { AlertCircle, Bot, FileText, FolderGit2, MessageSquare, Terminal, UsersRound, Workflow, X } from 'lucide-react';
import { useChatPage } from './context';

/**
 * What this conversation is pointed at, and how it is shown: the agent, flow or
 * team picker, the project, and the Chat/Build toggle.
 */
export default function ChatTopBar() {
  const {
    agentModel, agentProvider, agentTopology, currentConv, currentConvId, flows, graphRun,
    messages, processOpen, projects, selectableAgents, selectedAgent, selectedFlow,
    selectedProject, selectedTeam, selectedWorkspace, setConversations, setProcessOpen,
    setSelectedAgent, setSelectedFlow, setSelectedProject, setSelectedTeam, setTargetMode,
    setViewMode, t, targetMode, teams, viewMode,
  } = useChatPage();
  return (
    <>

        {/* Top bar */}
        <div className="flex-shrink-0 bg-white border-b border-gray-200 px-5 h-[60px] flex items-center gap-4">
          {/* Target-mode toggle: agent vs flow */}
          <div className="inline-flex rounded-lg border border-gray-200 bg-white overflow-hidden">
            <button
              onClick={() => setTargetMode('agent')}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium transition-colors ${
                targetMode === 'agent' ? 'bg-indigo-50 text-indigo-700' : 'text-gray-500 hover:bg-gray-50'
              }`}
              title={t('chat.chatWithASingleAgent')}
            >
              <Bot className="w-3.5 h-3.5" />
              {t('chat.agent')}
            </button>
            <button
              onClick={() => setTargetMode('flow')}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium transition-colors border-l border-gray-200 ${
                targetMode === 'flow' ? 'bg-emerald-50 text-emerald-700' : 'text-gray-500 hover:bg-gray-50'
              }`}
              title={t('chat.chatWithAFlowEach')}
            >
              <Workflow className="w-3.5 h-3.5" />
              {t('chat.flow')}
            </button>
            <button
              onClick={() => setTargetMode('team')}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium transition-colors border-l border-gray-200 ${
                targetMode === 'team' ? 'bg-amber-50 text-amber-700' : 'text-gray-500 hover:bg-gray-50'
              }`}
              title={t('chat.handTheMessageToA')}
            >
              <UsersRound className="w-3.5 h-3.5" />
              {t('chat.team')}
            </button>
          </div>

          {targetMode === 'agent' ? (
            <AgentDropdown agents={selectableAgents} value={selectedAgent} onChange={(id) => {
              setSelectedAgent(id);
              // update current conv's agent
              if (currentConvId) {
                setConversations((prev) =>
                  prev.map((c) => c.id === currentConvId ? { ...c, agent_id: id } : c),
                );
              }
            }} />
          ) : targetMode === 'flow' ? (
            <FlowDropdown flows={flows} value={selectedFlow} onChange={(id) => {
              setSelectedFlow(id);
              if (currentConvId) {
                setConversations((prev) =>
                  prev.map((c) => c.id === currentConvId ? { ...c, flow_id: id, target_mode: 'flow' } : c),
                );
              }
            }} />
          ) : (
            <TeamDropdown teams={teams} value={selectedTeam} onChange={(id) => {
              setSelectedTeam(id);
              if (currentConvId) {
                setConversations((prev) =>
                  prev.map((c) => c.id === currentConvId ? { ...c, team_id: id, target_mode: 'team' } : c),
                );
              }
            }} />
          )}

          {/* The active chat's workspace is redundant when a specific workspace is
              selected in the header — only surface it in the default (all) view. */}
          {(!selectedWorkspace || selectedWorkspace === 'default') && (currentConv?.workspace || selectedWorkspace) && (
            <div className="flex items-center gap-1.5 text-xs text-gray-500">
              <span className="font-semibold text-gray-400 uppercase tracking-wider text-[10px]">{t('chat.ws')}</span>
              <span className="font-medium text-gray-700 bg-gray-100 px-2 py-0.5 rounded">
                {currentConv?.workspace || selectedWorkspace}
              </span>
            </div>
          )}

          {projects.length > 0 && (
            <div className="flex items-center gap-1.5 text-xs text-gray-500">
              <FolderGit2 className="w-3.5 h-3.5 text-emerald-500 flex-shrink-0" />
              <select
                value={selectedProject}
                onChange={(e) => setSelectedProject(e.target.value)}
                className="text-xs border border-gray-200 rounded px-2 py-0.5 bg-white text-gray-700 focus:outline-none focus:ring-1 focus:ring-emerald-400 max-w-[140px]"
              >
                <option value="">{t('chat.noProject')}</option>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
          )}

          {selectedAgent && (
            <div className="flex items-center gap-1.5 text-xs text-gray-500">
              <span className="font-semibold text-gray-400 uppercase tracking-wider text-[10px]">{t('chat.model')}</span>
              {agentProvider === 'inherit' ? (
                <span className="font-medium text-gray-400 bg-gray-100 px-2 py-0.5 rounded italic">{t('chat.global')}</span>
              ) : (
                <span className="font-medium text-gray-700 bg-gray-100 px-2 py-0.5 rounded">
                  {agentProvider}{agentModel ? ` · ${agentModel}` : ''}
                </span>
              )}
            </div>
          )}

          {selectedWorkspace && selectableAgents.length === 0 && (
            <div className="flex items-center gap-2 bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-1.5 text-xs font-medium">
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
              {t('chat.noAuthorizedAgentsInThis')}
            </div>
          )}


          {/* View-mode toggle: clean Chat vs full Build transcript + artifacts */}
          <div className="ml-auto inline-flex rounded-lg border border-gray-200 bg-white overflow-hidden">
            <button
              onClick={() => setViewMode('chat')}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium transition-colors ${
                viewMode === 'chat' ? 'bg-indigo-50 text-indigo-700' : 'text-gray-500 hover:bg-gray-50'
              }`}
              title={t('chat.cleanChatMessagesOnly')}
            >
              <MessageSquare className="w-3.5 h-3.5" />
              {t('chat.chat')}
            </button>
            <button
              onClick={() => setViewMode('build')}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium transition-colors border-l border-gray-200 ${
                viewMode === 'build' ? 'bg-indigo-50 text-indigo-700' : 'text-gray-500 hover:bg-gray-50'
              }`}
              title={t('chat.buildFullTranscriptToolsThinking')}
            >
              <Terminal className="w-3.5 h-3.5" />
              {t('chat.build')}
            </button>
          </div>

          {viewMode !== 'build' && (
            <button
              onClick={() => setProcessOpen((v) => !v)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium border border-gray-200 rounded-lg text-gray-600 hover:bg-gray-50"
            >
              {processOpen ? (
                <>
                  <X className="w-3.5 h-3.5" />
                  {t('chat.hideProcess')}
                </>
              ) : (
                <>
                  <FileText className="w-3.5 h-3.5" />
                  {t('chat.showProcess')}
                  {/* The panel is closed by default, so an agent whose graph is
                      being walked right now would otherwise be drawing itself
                      where nobody is looking. The node's name on the button is
                      both the notice and the invitation. */}
                  {agentTopology?.nodes?.length > 0 && (
                    <span className="inline-flex items-center gap-1 text-indigo-600">
                      <Workflow className="w-3.5 h-3.5" />
                      {graphRun.active && <span className="max-w-[90px] truncate">{graphRun.active}</span>}
                    </span>
                  )}
                </>
              )}
            </button>
          )}

          <div className="text-xs text-gray-400">
            {messages.length > 0 && `${messages.length} message${messages.length !== 1 ? 's' : ''}`}
          </div>
        </div>
    </>
  );
}
