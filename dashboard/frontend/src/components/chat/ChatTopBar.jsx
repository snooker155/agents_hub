import { AgentDropdown, FlowDropdown, ProjectDropdown, TeamDropdown } from './targetPickers';
import { Activity, AlertCircle, AlertTriangle, Bot, Code2, FileText, MessageSquare, PanelLeftOpen, Terminal, UsersRound, Workflow } from 'lucide-react';
import { useChatPage } from './context';
import { Link } from 'react-router-dom';
import { useCallback, useEffect, useState } from 'react';
import { getChatRoute } from '../../api';
import { useLiveRefetch } from '../stream';

/**
 * What this conversation is pointed at, and how it is shown.
 *
 * Left to right: the mode (agent, flow or team) and the picker for it, the
 * project, and the agent's model when it has one of its own. On the right,
 * drawn the same way as the mode: in the chat view the Process panel, then
 * the view (Chat, the messages only; Build, the whole transcript), then the
 * panel beside the conversation (Artifacts or Code, one at a time, each with
 * how many the conversation has produced; pressed is open).
 *
 * Every control is one height, so the bar reads as one row. On a phone it
 * wraps to two, the buttons drop their labels, and a first button opens the
 * conversation list, which is a drawer there.
 */

/** One segmented switch: a row of buttons in a shared border. */
function Segmented({ children }) {
  return (
    <div className="inline-flex rounded-lg border border-gray-200 bg-white overflow-hidden">
      {children}
    </div>
  );
}

const TONES = {
  indigo: 'bg-indigo-50 text-indigo-700',
  emerald: 'bg-emerald-50 text-emerald-700',
  amber: 'bg-amber-50 text-amber-700',
};

/** How many of a thing the conversation holds, on its panel button. */
function Count({ n, active }) {
  return (
    <span
      data-testid="panel-count"
      className={`min-w-[1.25rem] px-1 rounded-full text-[10px] leading-4 tabular-nums text-center ${
        active ? 'bg-white/70 text-indigo-700' : 'bg-gray-100 text-gray-500'
      }`}
    >
      {n}
    </span>
  );
}

function Segment({ active, onClick, icon: Icon, label, title, tone = 'indigo', first = false, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      title={title}
      className={`h-[30px] flex items-center gap-1.5 px-2.5 text-xs font-medium transition-colors ${
        first ? '' : 'border-l border-gray-200'
      } ${active ? TONES[tone] : 'text-gray-500 hover:bg-gray-50'}`}
    >
      <Icon className="w-3.5 h-3.5" />
      <span className="hidden sm:inline">{label}</span>
      {children}
    </button>
  );
}

export default function ChatTopBar() {
  const {
    agentModel, agentProvider, agentTopology, artifactCount, artifactsOpen, codeCount, codeOpen, currentConv,
    currentConvId, flows, graphRun, messages, processOpen, projects, selectableAgents, selectedAgent,
    selectedFlow, selectedProject, selectedTeam, selectedWorkspace, setArtifactsOpen, setCodeOpen,
    setConversations, setProcessOpen, setSelectedAgent, setSelectedFlow, setSelectedProject,
    setSelectedTeam, setTargetMode, setViewMode, t, targetMode, teams, viewMode, setListOpen,
  } = useChatPage();
  const isBuild = viewMode === 'build';
  // Artifacts and Code share the one slot: opening one closes the other.
  const toggleArtifacts = () => { setArtifactsOpen((v) => !v); setCodeOpen(false); };
  const toggleCode = () => { setCodeOpen((v) => !v); setArtifactsOpen(false); };

  // The service a turn would go to, when chat runs on replicas: a paused one
  // is a chat that will not answer, said here before the message is typed.
  const routeAgent = targetMode === 'agent' ? selectedAgent : '';
  const [route, setRoute] = useState(null);
  const probeRoute = useCallback(() => {
    getChatRoute({ workspace: selectedWorkspace || undefined, agent_id: routeAgent || undefined })
      .then((r) => setRoute(r.data || null))
      .catch(() => setRoute(null));
  }, [selectedWorkspace, routeAgent]);
  useEffect(() => { probeRoute(); }, [probeRoute]);
  useLiveRefetch(probeRoute, { type: 'services.changed' });
  const routeDown = route && route.available === false && route.service;

  const pickTarget = (field, id, extra = {}) => {
    if (!currentConvId) return;
    setConversations((prev) =>
      prev.map((c) => (c.id === currentConvId ? { ...c, [field]: id, ...extra } : c)),
    );
  };

  return (
    <div className="flex-shrink-0 bg-white border-b border-gray-200 px-3 sm:px-5 py-2 sm:py-0 sm:h-[60px] flex flex-wrap sm:flex-nowrap items-center gap-2 sm:gap-3">
      <button
        type="button"
        onClick={() => setListOpen(true)}
        aria-label={t('chat.chats')}
        title={t('chat.chats')}
        className="sm:hidden h-8 w-8 flex items-center justify-center rounded-lg border border-gray-200 text-gray-500 hover:bg-gray-50"
      >
        <PanelLeftOpen className="w-4 h-4" />
      </button>
      {/* Mode: who answers */}
      <Segmented>
        <Segment first active={targetMode === 'agent'} onClick={() => setTargetMode('agent')}
                 icon={Bot} label={t('chat.agent')} title={t('chat.chatWithASingleAgent')} />
        <Segment active={targetMode === 'flow'} onClick={() => setTargetMode('flow')} tone="emerald"
                 icon={Workflow} label={t('chat.flow')} title={t('chat.chatWithAFlowEach')} />
        <Segment active={targetMode === 'team'} onClick={() => setTargetMode('team')} tone="amber"
                 icon={UsersRound} label={t('chat.team')} title={t('chat.handTheMessageToA')} />
      </Segmented>

      {targetMode === 'agent' ? (
        <AgentDropdown agents={selectableAgents} value={selectedAgent} onChange={(id) => {
          setSelectedAgent(id);
          pickTarget('agent_id', id);
        }} />
      ) : targetMode === 'flow' ? (
        <FlowDropdown flows={flows} value={selectedFlow} onChange={(id) => {
          setSelectedFlow(id);
          pickTarget('flow_id', id, { target_mode: 'flow' });
        }} />
      ) : (
        <TeamDropdown teams={teams} value={selectedTeam} onChange={(id) => {
          setSelectedTeam(id);
          pickTarget('team_id', id, { target_mode: 'team' });
        }} />
      )}

      {routeDown && (
        <Link
          to={`/services/${routeDown.service_id}`}
          data-testid="chat-service-down"
          className="h-8 flex items-center gap-1.5 px-3 rounded-lg border border-amber-200 bg-amber-50 text-amber-800 text-xs font-medium hover:bg-amber-100"
          title={t('chat.serviceDownHint')}
        >
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          {t('chat.serviceDown', { name: routeDown.name })}
        </Link>
      )}

      {projects.length > 0 && (
        <ProjectDropdown projects={projects} value={selectedProject} onChange={setSelectedProject} />
      )}

      {/* The active chat's workspace is redundant when a specific workspace is
          selected in the header — only surface it in the default (all) view. */}
      {(!selectedWorkspace || selectedWorkspace === 'default') && (currentConv?.workspace || selectedWorkspace) && (
        <div className="hidden md:flex items-center gap-1.5 text-xs text-gray-500">
          <span className="font-semibold text-gray-400 uppercase tracking-wider text-[10px]">{t('chat.ws')}</span>
          <span className="font-medium text-gray-700 bg-gray-100 px-2 py-0.5 rounded">
            {currentConv?.workspace || selectedWorkspace}
          </span>
        </div>
      )}

      {/* The model, only when the agent names one of its own: an inherited
          setting says nothing about this agent in particular. */}
      {targetMode === 'agent' && selectedAgent && agentModel && (
        <div className="hidden md:flex items-center gap-1.5 text-xs text-gray-500">
          <span className="font-semibold text-gray-400 uppercase tracking-wider text-[10px]">{t('chat.model')}</span>
          <span className="font-medium text-gray-700 bg-gray-100 px-2 py-0.5 rounded">
            {agentProvider && agentProvider !== 'inherit' ? `${agentProvider} · ${agentModel}` : agentModel}
          </span>
        </div>
      )}

      {selectedWorkspace && selectableAgents.length === 0 && (
        <div className="flex items-center gap-2 bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 h-8 text-xs font-medium">
          <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
          {t('chat.noAuthorizedAgentsInThis')}
        </div>
      )}

      {/* View: the messages, or the whole transcript */}
      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        {/* What the person wrote, not every bubble: a turn's tool calls and
            steering notes are part of one message's answer. */}
        {(() => {
          const sent = messages.filter((m) => m.role === 'user' && !m.steer).length;
          return sent > 0 ? (
            <div className="hidden sm:block text-xs text-gray-400 tabular-nums">
              {t('chat.messageCount', { count: sent })}
            </div>
          ) : null;
        })()}
        {/* The process, in the chat view only: the build view already shows
            the run inline. */}
        {!isBuild && (
          <Segmented>
            <Segment first active={processOpen} onClick={() => setProcessOpen((v) => !v)} icon={Activity}
                     label={t('chat.panels.process')}
                     title={processOpen ? t('chat.hideProcess') : t('chat.showProcess')}>
              {/* The panel is closed by default, so an agent whose graph is
                  being walked right now would otherwise be drawing itself where
                  nobody is looking. The node's name on the button is both the
                  notice and the invitation. */}
              {!processOpen && agentTopology?.nodes?.length > 0 && (
                <span className="inline-flex items-center gap-1 text-indigo-600">
                  <Workflow className="w-3.5 h-3.5" />
                  {graphRun.active && <span className="max-w-[90px] truncate">{graphRun.active}</span>}
                </span>
              )}
            </Segment>
          </Segmented>
        )}
        <Segmented>
          <Segment first active={viewMode === 'chat'} onClick={() => setViewMode('chat')}
                   icon={MessageSquare} label={t('chat.chat')} title={t('chat.cleanChatMessagesOnly')} />
          <Segment active={isBuild} onClick={() => setViewMode('build')}
                   icon={Terminal} label={t('chat.build')} title={t('chat.buildFullTranscriptToolsThinking')} />
        </Segmented>

        {/* The panel beside the conversation, one of two, with how many each holds; pressed is open. */}
        <Segmented>
          <Segment first active={artifactsOpen} onClick={toggleArtifacts} icon={FileText}
                   label={t('chat.panels.artifacts')}
                   title={artifactsOpen ? t('chat.hideArtifacts') : t('chat.showArtifacts')}>
            <Count n={artifactCount || 0} active={artifactsOpen} />
          </Segment>
          <Segment active={codeOpen} onClick={toggleCode} icon={Code2}
                   label={t('chat.panels.code')} title={t('chat.code.toggle')}>
            <Count n={codeCount || 0} active={codeOpen} />
          </Segment>
        </Segmented>

      </div>
    </div>
  );
}
