import { useState } from 'react';
import { AlertCircle, AudioLines, ChevronDown, Copy, FolderGit2, Loader, MessageSquare, PlusCircle, Search, Square, Trash2, UsersRound, Workflow } from 'lucide-react';
import { useChatPage } from './context';
import ChatSearchModal from './ChatSearchModal';
import { groupByConnector } from './connectorChats';

/** One section's row in the chat column's accordion: what it holds, how many,
 * and (`chevron`) whether it is open. */
function SectionRow({ icon: Icon, label, count, open, onClick, testId, chevron = true }) {
  return (
    <div className="px-2 py-1 shrink-0">
      <button
        type="button"
        onClick={onClick}
        aria-expanded={open}
        data-testid={testId}
        className={`w-full flex items-center gap-2 px-3 py-2 rounded-lg text-xs font-medium transition-colors ${
          open ? 'text-gray-800' : 'text-gray-600 hover:bg-white hover:shadow-sm'}`}
      >
        <Icon className="w-3.5 h-3.5 shrink-0 text-gray-500" />
        <span className="flex-1 min-w-0 truncate text-left">{label}</span>
        <span className="min-w-[1.25rem] px-1 rounded-full bg-gray-200 text-[10px] leading-4 tabular-nums text-center text-gray-500">
          {count}
        </span>
        {/* Closed, it points up (it opens to fill the column); open, down. */}
        {chevron && (
          <ChevronDown className={`w-3.5 h-3.5 shrink-0 text-gray-400 transition-transform ${open ? '' : 'rotate-180'}`} />
        )}
      </button>
    </div>
  );
}

/**
 * The conversation list: the ordinary chats on top, each labelled with the
 * workspace, target and project it belongs to, and the connectors' chats
 * (Telegram, Slack...) below them, a section per connector: an accordion with
 * one section open at a time. The search over the chats opens in a window from the icon
 * beside New chat (ChatSearchModal.jsx).
 */
// The end of a row's title fading out under the icons laid over it; written
// out whole for each state so Tailwind finds the classes.
const TITLE_FADE = '[mask-image:linear-gradient(to_right,#000_calc(100%-2.5rem),transparent_calc(100%-1rem))]';
const TITLE_FADE_ON_HOVER = 'group-hover:[mask-image:linear-gradient(to_right,#000_calc(100%-2.5rem),transparent_calc(100%-1rem))] group-focus-within:[mask-image:linear-gradient(to_right,#000_calc(100%-2.5rem),transparent_calc(100%-1rem))]';

export default function ChatSidebar() {
  const {
    currentConvId, deleteConversation, flows, listOpen, navigate, newConversation, projects,
    setListOpen,
    selectableAgents, selectedWorkspace, setConversations, setSelectedAgent,
    setSelectedFlow, setSelectedProject, setSelectedTeam, setTargetMode, syncError, t,
    runningChats, stopConversation, teams, turns, visibleConversations, visibleConnectorBindings,
  } = useChatPage();
  // A page with no connectors (a test's page, a hub without them) lists none.
  const connectorGroups = groupByConnector(visibleConnectorBindings || []);
  const [searchOpen, setSearchOpen] = useState(false);
  // An accordion when connectors have chats: the service's chats, then each
  // connector, one open at a time and filling the column, the others down to
  // their header row. Until one is picked, the open chat's section is open.
  const [picked, setPicked] = useState(undefined);
  const activeConnector = (visibleConnectorBindings || []).find((b) => b.conversation_id === currentConvId)?.connector || null;
  const accordion = connectorGroups.length > 0;
  const openSection = !accordion ? 'chats'
    : picked !== undefined && (picked === 'chats' || connectorGroups.some((g) => g.id === picked)) ? picked
      : (activeConnector || 'chats');
  // A click on the open section's row goes back to the service's chats.
  const toggleSection = (id) => setPicked(openSection === id ? 'chats' : id);

  const openConnectorChat = (b) => {
    const convId = b.conversation_id;
    if (!convId) return;
    setListOpen(false);
    // A local entry so the main pane renders; the messages are read back from
    // the runs (useChatTelegram.js).
    setConversations((prev) => {
      if (prev.some((c) => c.id === convId)) return prev;
      return [{
        id: convId,
        title: b.title || `${t(`connectors.tabs.${b.connector}`)} ${b.chat_key}`,
        agent_id: b.agent_id,
        workspace: b.workspace || null,
        messages: [],
        origin: b.connector,
        chat_id: b.chat_key,
        createdAt: b.created_at || new Date().toISOString(),
      }, ...prev];
    });
    if (b.agent_id && selectableAgents.some((a) => a.id === b.agent_id)) setSelectedAgent(b.agent_id);
    navigate(`/chat/${convId}`);
  };

  const openConversation = (conv) => {
    setListOpen(false);
    // A chat older than the loaded page, found by the search:
    // listed first, so it opens (and loads) like any other. Marked
    // not loaded, so the store reads its transcript and never
    // writes this empty summary over it (chatStore.js).
    if (conv._searchOnly) {
      const { _searchOnly: _found, match: _match, ...summary } = conv;
      setConversations((prev) => (prev.some((c) => c.id === conv.id)
        ? prev : [...prev, { ...summary, messages: [], _hydrated: false }]));
    }
    navigate(`/chat/${conv.id}`);
    // A conversation with the Assistant opens with the Assistant
    // picked (its record's agent_id), like any other agent's.
    if (conv.target_mode === 'flow' && conv.flow_id) {
      setTargetMode('flow');
      setSelectedFlow(conv.flow_id);
    } else if (conv.target_mode === 'team' && conv.team_id) {
      setTargetMode('team');
      setSelectedTeam(conv.team_id);
    } else if (conv.agent_id && selectableAgents.some((a) => a.id === conv.agent_id)) {
      setTargetMode('agent');
      setSelectedAgent(conv.agent_id);
    }
    // Restore the conversation's project so the selector reflects the
    // scope it was created with (empty = no project).
    setSelectedProject(conv.project_id || '');
  };
  return (
    <>
      {/* ── Sidebar: a column on a wide screen, a drawer on a phone ── */}
      {listOpen && (
        <div className="sm:hidden fixed inset-0 z-40 bg-black/40" onClick={() => setListOpen(false)} aria-hidden="true" />
      )}
      <div className={`w-[17.25rem] flex-shrink-0 border-r border-gray-200 flex-col ${
        listOpen
          ? 'flex bg-gray-50 max-sm:fixed max-sm:inset-y-0 max-sm:left-0 max-sm:z-50 max-sm:w-72 max-sm:max-w-[85vw] max-sm:shadow-xl max-sm:pt-safe max-sm:pb-safe'
          : 'hidden sm:flex'
      }`}>
        {/* New chat in a band as tall as the top bar beside it (60px); no
            line under either. */}
        <div className="h-[60px] flex-shrink-0 flex items-center gap-2 px-3">
          <button
            onClick={newConversation}
            className="flex-1 min-w-0 h-9 flex items-center justify-center gap-2 px-3 rounded-xl text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
          >
            <PlusCircle className="w-4 h-4" />
            {t('chat.newChat')}
          </button>
          <button
            type="button"
            onClick={() => setSearchOpen(true)}
            title={t('chat.search.placeholder')}
            aria-label={t('chat.search.placeholder')}
            data-testid="chat-search-open"
            className="h-9 w-9 shrink-0 flex items-center justify-center rounded-xl border border-gray-200 bg-white text-gray-500 hover:text-indigo-600 hover:border-indigo-200 transition-colors"
          >
            <Search className="w-4 h-4" />
          </button>
        </div>
        {searchOpen && (
          <ChatSearchModal
            conversations={visibleConversations}
            workspace={selectedWorkspace}
            onOpen={openConversation}
            onClose={() => setSearchOpen(false)}
            t={t}
          />
        )}

        {/* The service's chats. Folded while a connector is open, they are this
            row alone, which opens them again; open, they need no row. */}
        <div className={openSection === 'chats' ? 'flex-1 min-h-0 flex flex-col' : 'shrink-0'}>
          {accordion && openSection !== 'chats' && (
            <SectionRow
              icon={MessageSquare} label={t('chat.chats')} count={visibleConversations.length}
              open={openSection === 'chats'} onClick={() => toggleSection('chats')} testId="chats-section" chevron={false}
            />
          )}
          {openSection === 'chats' && <>
          {/* Chats are stored on the server; when that write keeps failing the
              conversation only exists in this tab, and saying so is the
              difference between a delay and silent loss. The list has no
              heading of its own: this line shows only then. */}
          {syncError && (
            <div
              className="px-3 pt-2 pb-1 flex items-center gap-1 text-[10px] font-semibold text-amber-600 flex-shrink-0"
              title={t('chat.notSavedHint')}
            >
              <AlertCircle className="w-3 h-3" /> {t('chat.notSaved')}
            </div>
          )}
          <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5">
          {visibleConversations.length === 0 && (
            <p className="text-xs text-gray-400 text-center py-10 px-3 leading-relaxed">
              No conversations yet.
              <br />
              Click <strong>{t('chat.newChat')}</strong> to start.
            </p>
          )}
          {visibleConversations.map((conv) => {
            // The icons lie over the end of the title, which fades out under
            // them: always while the conversation is answered, on hover for delete.
            const running = Boolean(turns?.[conv.id] || runningChats?.has(conv.id));
            const titleFade = running ? TITLE_FADE
              : conv.origin !== 'assistant' ? TITLE_FADE_ON_HOVER : '';
            return (
            // Rendered as a div, not a button: it contains the delete button and
            // nesting a button inside a button is invalid HTML. role/tabIndex/
            // onKeyDown restore the keyboard and a11y behaviour of a button.
            <div
              key={conv.id}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  e.currentTarget.click();
                }
              }}
              onClick={() => openConversation(conv)}
              className={`relative w-full text-left px-3 py-2 rounded-lg text-xs group flex items-start transition-colors
                ${currentConvId === conv.id
                  ? 'bg-indigo-50 text-indigo-700'
                  : 'text-gray-600 hover:bg-white hover:shadow-sm'
                }`}
            >
              <div className="flex-1 min-w-0">
                <div className={`truncate leading-5 ${titleFade}`}>{conv.title}</div>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {/* A conversation with the Assistant is no workspace's and talks to the Assistant only: its own badge says both. */}
                  {conv.origin !== 'assistant' && (!selectedWorkspace || selectedWorkspace === 'default') && (
                    (!conv.workspace || conv.workspace === 'default') ? (
                      <span className="inline-block text-[9px] px-1.5 py-0.5 rounded bg-gray-200 text-gray-500 font-medium">
                        {t('chat.default')}
                      </span>
                    ) : (
                      <span className="inline-block text-[9px] px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-600 font-medium truncate max-w-full">
                        {conv.workspace}
                      </span>
                    )
                  )}
                  {conv.target_mode === 'team' && conv.team_id ? (() => {
                    const team = teams.find((t) => t.team_id === conv.team_id);
                    return (
                      <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-medium truncate max-w-full">
                        <UsersRound className="w-2.5 h-2.5 flex-shrink-0" />
                        {team ? team.name : 'team'}
                      </span>
                    );
                  })() : conv.target_mode === 'flow' && conv.flow_id ? (() => {
                    const flow = flows.find((f) => f.id === conv.flow_id);
                    return (
                      <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 font-medium truncate max-w-full">
                        <Workflow className="w-2.5 h-2.5 flex-shrink-0" />
                        {flow ? flow.name : 'flow'}
                      </span>
                    );
                  })() : conv.agent_id && conv.origin !== 'assistant' && (() => {
                    const agent = selectableAgents.find(a => a.id === conv.agent_id);
                    return (
                      <span className="inline-block text-[9px] px-1.5 py-0.5 rounded bg-violet-100 text-violet-600 font-medium truncate max-w-full">
                        {agent ? agent.name : conv.agent_id}
                      </span>
                    );
                  })()}
                  {conv.origin === 'assistant' && (
                    <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 font-medium"
                      data-testid="assistant-thread-badge">
                      <AudioLines className="w-2.5 h-2.5 flex-shrink-0" />
                      {t('chat.assistantThread.badge')}
                      {conv.assistant_thread?.mode === 'service' && <> · {t('chat.assistantThread.service')}</>}
                      {/* A conversation stays in its workspace: named where all are listed. */}
                      {(!selectedWorkspace || selectedWorkspace === 'default') && conv.workspace && <> · {conv.workspace}</>}
                    </span>
                  )}
                  {conv.origin === 'mirror' && (
                    <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded bg-slate-200 text-slate-700 font-medium" title={conv.mirror?.node} data-testid="mirror-badge">
                      <Copy className="w-2.5 h-2.5 flex-shrink-0" />
                      {t('chat.mirror.badge')}{conv.mirror?.node ? ` · ${conv.mirror.node}` : ''}
                    </span>
                  )}
                  {conv.project_id && (() => {
                    const proj = projects.find(p => p.id === conv.project_id);
                    return proj ? (
                      <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 font-medium truncate max-w-full">
                        <FolderGit2 className="w-2.5 h-2.5 flex-shrink-0" />
                        {proj.name}
                      </span>
                    ) : null;
                  })()}
                </div>
              </div>
              {/* Being answered now: a turn this tab sent (it goes on while
                  another conversation is open), or anyone else's. The spinner
                  takes the delete button's place until the turn ends, and a
                  stop button takes the spinner's on hover. Both lie over the
                  faded end of the title, so the title keeps the whole width. */}
              {running ? (
                <span className="absolute right-2 top-2 flex items-center h-5">
                  <Loader
                    className={`w-3.5 h-3.5 flex-shrink-0 animate-spin text-indigo-500 ${stopConversation ? 'group-hover:hidden' : ''}`}
                    data-testid="chat-conv-running"
                    aria-label={t('chat.answering')}
                  />
                  {stopConversation && (
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); stopConversation(conv.id); }}
                      className="hidden group-hover:flex p-0.5 rounded text-gray-500 hover:text-red-600 hover:bg-red-50 flex-shrink-0"
                      title={t('chat.stop')}
                      aria-label={t('chat.stop')}
                      data-testid="chat-conv-stop"
                    >
                      <Square className="w-3 h-3 fill-current" />
                    </button>
                  )}
                </span>
              ) : conv.origin !== 'assistant' && (
                <span className="absolute right-2 top-2 hidden group-hover:flex group-focus-within:flex items-center h-5">
                  <button
                    onClick={(e) => deleteConversation(conv.id, e)}
                    className="p-0.5 rounded text-gray-400 hover:text-red-500"
                    title={t('chat.delete')}
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </span>
              )}
            </div>
            );
          })}
          </div>
          </>}
        </div>

        {/* The connectors' chats (Telegram, Slack, Discord, Teams, mail), one
            section per connector that has any, below the service's chats. */}
        {accordion && (
          <div className={`border-t border-gray-200 flex flex-col ${openSection === 'chats' ? 'shrink-0' : 'flex-1 min-h-0'}`} data-testid="connector-chats">
            {connectorGroups.map((group) => {
              const open = openSection === group.id;
              return (
                <div key={group.id} className={open ? 'flex-1 min-h-0 flex flex-col' : 'shrink-0'}>
                  <SectionRow
                    icon={group.icon} label={t(`connectors.tabs.${group.id}`)} count={group.chats.length}
                    open={open} onClick={() => toggleSection(group.id)} testId={`connector-${group.id}`}
                  />
                  {open && (
                    <div className="flex-1 min-h-0 overflow-y-auto px-2 pb-3 space-y-0.5" data-testid={`connector-${group.id}-chats`}>
                      {group.chats.map((b) => {
                        const isActive = currentConvId === b.conversation_id;
                        const agent = selectableAgents.find((a) => a.id === b.agent_id);
                        return (
                          <button
                            key={`${b.connector}-${b.chat_key}`}
                            type="button"
                            onClick={() => openConnectorChat(b)}
                            className={`w-full text-left px-3 py-2 rounded-lg text-xs flex items-start gap-2 transition-colors
                              ${isActive ? 'bg-indigo-50 text-indigo-700' : 'text-gray-600 hover:bg-white hover:shadow-sm'}`}
                          >
                            <div className="flex-1 min-w-0">
                              <div className="truncate leading-5">{b.title || t('chat.telegramChat', { id: b.chat_key })}</div>
                              <div className="mt-0.5 flex flex-wrap gap-1">
                                <span className="inline-block text-[9px] px-1.5 py-0.5 rounded bg-violet-100 text-violet-600 font-medium truncate max-w-full">
                                  {agent ? agent.name : (b.agent_name || b.agent_id)}
                                </span>
                                {b.workspace && (
                                  <span className="inline-block text-[9px] px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-600 font-medium truncate max-w-full">
                                    {b.workspace}
                                  </span>
                                )}
                              </div>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
