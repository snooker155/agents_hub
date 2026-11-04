import { AgentDropdown, FlowDropdown, ModeDropdown, ProjectDropdown, TeamDropdown } from './targetPickers';
import { Activity, AlertCircle, AlertTriangle, Bot, FileText, Folder, MessageCircle, MessageSquare, PanelLeftOpen, Terminal, UsersRound, Workflow } from 'lucide-react';
import { useChatPage } from './context';
import { Link } from 'react-router-dom';
import { useCallback, useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { PhoneTitleSlot } from '../phoneTitleSlot';
import { getChatRoute } from '../../api';
import { useLiveRefetch } from '../stream';

/**
 * What this conversation is pointed at, and how it is shown.
 *
 * Left to right: the mode (agent, flow or team) and the picker for it, the
 * project, and the agent's model when it has one of its own. On the right,
 * drawn the same way as the mode: in the chat view the Process panel, then
 * the view (Chat, the messages only; Build, the whole transcript), then the
 * panel beside the conversation (Artifacts: its views, code and files, with
 * how many the conversation has produced; pressed is open).
 *
 * Every control is one height, so the bar reads as one row. On a phone the
 * picker and the project go up into the app's top bar (PhoneTitleSlot), the
 * buttons drop their labels, and a first button opens the conversation list,
 * which is a drawer there.
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

// `iconOnly`: the label only from 1800 CSS px; `compact`: no label at all
// (always hidden on a phone).
function Segment({ active, onClick, icon: Icon, label, title, tone = 'indigo', first = false, iconOnly = false, compact = false, children }) {
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
      <span className={compact ? 'hidden' : iconOnly ? 'hidden min-[1800px]:inline' : 'hidden sm:inline'}>{label}</span>
      {children}
    </button>
  );
}

// Narrower than this, the bar (the chat column, squeezed by the panels beside
// it) cannot hold every label: the mode becomes a dropdown, the buttons keep
// their icons and the workspace and model leave the bar.
const COMPACT_BELOW_PX = 900;
// A phone has a layout of its own: the bar wraps to two lines.
const PHONE_BELOW_PX = 640;

/** Whether the bar behind the returned ref is too narrow for its labels. */
function useCompactBar() {
  const ref = useRef(null);
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const measure = () => setCompact(window.innerWidth >= PHONE_BELOW_PX
      && el.getBoundingClientRect().width < COMPACT_BELOW_PX);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, compact];
}

export default function ChatTopBar() {
  const {
    agentModel, agentProvider, agentTopology, artifactCount, artifactsOpen, currentConv,
    currentConvId, flows, graphRun, processOpen, projects, selectableAgents, selectedAgent,
    selectedFlow, selectedProject, selectedTeam, selectedWorkspace, setArtifactsOpen,
    setConversations, setProcessOpen, setSelectedAgent, setSelectedFlow, setSelectedProject,
    setSelectedTeam, setTargetMode, setViewMode, t, targetMode, teams, viewMode, setListOpen, visibleConversations,
  } = useChatPage();
  const isBuild = viewMode === 'build';
  const [barRef, compact] = useCompactBar();
  const toggleArtifacts = () => setArtifactsOpen((v) => !v);
  // The Process column exists in the chat view only (ChatSidePanel).
  const bothColumns = artifactsOpen && processOpen && viewMode !== 'build';
  // A column open beside the chat: under 1800 CSS px the pickers show names alone.
  const anyColumn = artifactsOpen || (processOpen && viewMode !== 'build');

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

  // The top bar's slot on a phone (Layout.jsx), null elsewhere.
  const phoneSlot = useContext(PhoneTitleSlot);
  const targetPicker = (
    <div className="min-w-0">
      {targetMode === 'agent' ? (
        <AgentDropdown agents={selectableAgents} value={selectedAgent} conversations={visibleConversations} iconOnWide={anyColumn} details={!compact} onChange={(id) => {
          setSelectedAgent(id);
          pickTarget('agent_id', id);
        }} />
      ) : targetMode === 'flow' ? (
        <FlowDropdown flows={flows} value={selectedFlow} iconOnWide={anyColumn} onChange={(id) => {
          setSelectedFlow(id);
          pickTarget('flow_id', id, { target_mode: 'flow' });
        }} />
      ) : (
        <TeamDropdown teams={teams} value={selectedTeam} iconOnWide={anyColumn} onChange={(id) => {
          setSelectedTeam(id);
          pickTarget('team_id', id, { target_mode: 'team' });
        }} />
      )}
    </div>
  );
  const projectPicker = projects.length > 0 ? (
    <div className="min-w-0">
      <ProjectDropdown projects={projects} value={selectedProject} onChange={setSelectedProject} iconOnWide={anyColumn} />
    </div>
  ) : null;

  return (
    <div ref={barRef} data-compact={compact ? 'true' : 'false'} className="flex-shrink-0 px-3 sm:px-5 py-1.5 sm:py-0 sm:h-[60px] flex flex-wrap sm:flex-nowrap items-center gap-x-2 gap-y-0 sm:gap-3">
      <button
        type="button"
        onClick={() => setListOpen(true)}
        aria-label={t('chat.chats')}
        title={t('chat.chats')}
        className="sm:hidden h-8 w-8 flex items-center justify-center rounded-lg border border-gray-200 text-gray-500 hover:bg-gray-50"
      >
        <PanelLeftOpen className="w-4 h-4" />
      </button>
      {/* Mode: who answers. Three buttons; a dropdown instead in a narrow bar,
          or while the Artifacts column takes the room on a screen under 1800
          CSS px (a laptop's, see ChatSidePanel). */}
      <div className={compact ? 'hidden' : artifactsOpen ? 'hidden min-[1800px]:block' : ''} data-testid="mode-toggle">
        <Segmented>
          <Segment first active={targetMode === 'agent'} onClick={() => setTargetMode('agent')}
                   icon={Bot} label={t('chat.agent')} title={t('chat.chatWithASingleAgent')} />
          <Segment active={targetMode === 'flow'} onClick={() => setTargetMode('flow')} tone="emerald"
                   icon={Workflow} label={t('chat.flow')} title={t('chat.chatWithAFlowEach')} />
          <Segment active={targetMode === 'team'} onClick={() => setTargetMode('team')} tone="amber"
                   icon={UsersRound} label={t('chat.team')} title={t('chat.handTheMessageToA')} />
        </Segmented>
      </div>
      {(compact || artifactsOpen) && (
        <div className={compact ? '' : 'min-[1800px]:hidden'} data-testid="mode-dropdown">
          <ModeDropdown value={targetMode} onChange={setTargetMode} />
        </div>
      )}

      {/* On a phone the app's top bar (PhoneTitleSlot) holds the page's title,
          with the menu's chat icon, and on its right the agent (or flow, or
          team) and the project; the bar here keeps who answers and the views. */}
      {phoneSlot ? createPortal(
        <>
          <MessageCircle className="h-4 w-4 shrink-0 text-indigo-600" />
          <h1 className="min-w-0 shrink-0 truncate text-base font-bold text-gray-900">{t('chat.chat')}</h1>
          <div className="ml-auto flex min-w-0 items-center gap-1.5" data-testid="chat-phone-pickers">
            {targetPicker}
            {projectPicker}
          </div>
        </>,
        phoneSlot,
      ) : targetPicker}

      {routeDown && (
        <Link
          to={`/services/${routeDown.service_id}`}
          data-testid="chat-service-down"
          className="max-sm:order-3 h-8 flex items-center gap-1.5 px-3 rounded-lg border border-amber-200 bg-amber-50 text-amber-800 text-xs font-medium hover:bg-amber-100"
          title={t('chat.serviceDownHint')}
        >
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          {t('chat.serviceDown', { name: routeDown.name })}
        </Link>
      )}

      {!phoneSlot && projectPicker}

      {/* The active chat's workspace is redundant when a specific workspace is
          selected in the header — only surface it in the default (all) view. */}
      {!compact && (!selectedWorkspace || selectedWorkspace === 'default') && (currentConv?.workspace || selectedWorkspace) && (
        <div
          className="hidden md:flex items-center gap-1.5 text-xs font-medium text-gray-700 bg-gray-100 px-2 py-0.5 rounded"
          title={t('chat.ws')}
          data-testid="chat-workspace"
        >
          {/* The Workspaces menu item's icon (navMenu.js). */}
          <Folder className="w-3.5 h-3.5 text-gray-400 shrink-0" aria-label={t('chat.ws')} />
          {currentConv?.workspace || selectedWorkspace}
        </div>
      )}

      {/* The model, only when the agent names one of its own: an inherited
          setting says nothing about this agent in particular. */}
      {!compact && targetMode === 'agent' && selectedAgent && agentModel && (
        <div className="hidden md:flex items-center gap-1.5 text-xs text-gray-500">
          <span className="font-semibold text-gray-400 uppercase tracking-wider text-[10px]">{t('chat.model')}</span>
          <span className="font-medium text-gray-700 bg-gray-100 px-2 py-0.5 rounded">
            {agentProvider && agentProvider !== 'inherit' ? `${agentProvider} · ${agentModel}` : agentModel}
          </span>
        </div>
      )}

      {selectedWorkspace && selectableAgents.length === 0 && (
        <div className="max-sm:order-3 flex items-center gap-2 bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 h-8 text-xs font-medium">
          <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
          {t('chat.noAuthorizedAgentsInThis')}
        </div>
      )}

      {/* View: the messages, or the whole transcript */}
      <div className="ml-auto flex items-center gap-2 sm:gap-3">
        {/* The process, in the chat view only: the build view already shows
            the run inline. */}
        {/* Under 1800 CSS px an open column's button leaves the bar: the
            column has room only at the bar's cost, and its own X closes it. */}
        {!isBuild && (
          <div className={processOpen ? 'hidden min-[1800px]:block' : ''} data-testid="process-toggle">
            <Segmented>
              <Segment first active={processOpen} onClick={() => setProcessOpen((v) => !v)} icon={Activity} compact={compact}
                       label={t('chat.panels.process')}
                       title={processOpen ? t('chat.hideProcess') : t('chat.showProcess')}>
                {/* The panel is closed by default, so an agent whose graph is
                    being walked right now would otherwise be drawing itself where
                    nobody is looking. The node's name on the button is both the
                    notice and the invitation. */}
                {!processOpen && agentTopology?.nodes?.length > 0 && (
                  <span className="inline-flex items-center gap-1 text-indigo-600">
                    <Workflow className="w-3.5 h-3.5" />
                    {graphRun.active && !compact && <span className="max-w-[90px] truncate">{graphRun.active}</span>}
                  </span>
                )}
              </Segment>
            </Segmented>
          </div>
        )}
        {/* With both columns open on a screen under 1800 CSS px, the view
            switch keeps its icons only; the titles still name them. */}
        <Segmented>
          <Segment first active={viewMode === 'chat'} onClick={() => setViewMode('chat')} iconOnly={bothColumns} compact={compact}
                   icon={MessageSquare} label={t('chat.chat')} title={t('chat.cleanChatMessagesOnly')} />
          <Segment active={isBuild} onClick={() => setViewMode('build')} iconOnly={bothColumns} compact={compact}
                   icon={Terminal} label={t('chat.build')} title={t('chat.buildFullTranscriptToolsThinking')} />
        </Segmented>

        {/* The panel beside the conversation (views, code, files), with how many it holds; pressed is open. */}
        <div className={artifactsOpen ? 'hidden min-[1800px]:block' : ''} data-testid="artifacts-toggle">
          <Segmented>
            <Segment first active={artifactsOpen} onClick={toggleArtifacts} icon={FileText} compact={compact}
                     label={t('chat.panels.artifacts')}
                     title={artifactsOpen ? t('chat.hideArtifacts') : t('chat.showArtifacts')}>
              <Count n={artifactCount || 0} active={artifactsOpen} />
            </Segment>
          </Segmented>
        </div>

      </div>
    </div>
  );
}
