import { useState } from 'react';
import { AlertCircle, AudioLines, Copy, FolderGit2, Loader, MessageSquare, PlusCircle, Search, Send as SendIcon, Trash2, UsersRound, Workflow, X } from 'lucide-react';
import { useChatPage } from './context';
import { useChatSearch } from './chatSearch';

/**
 * The conversation list: the ordinary chats on top, the Telegram threads below
 * when there are any, each labelled with the workspace, target and project it
 * belongs to, with a search over the chats above it (titles at once, the
 * messages from the server: chatSearch.js).
 */
export default function ChatSidebar() {
  const {
    currentConvId, deleteConversation, flows, listOpen, navigate, newConversation, projects,
    setListOpen,
    selectableAgents, selectedWorkspace, setConversations, setSelectedAgent,
    setSelectedFlow, setSelectedProject, setSelectedTeam, setTargetMode, syncError, t,
    runningChats, teams, turns, visibleConversations, visibleTelegramBindings,
  } = useChatPage();
  const [query, setQuery] = useState('');
  const { results, searching } = useChatSearch(query, visibleConversations, selectedWorkspace);
  const shown = results || visibleConversations;
  return (
    <>
      {/* ── Sidebar: a column on a wide screen, a drawer on a phone ── */}
      {listOpen && (
        <div className="sm:hidden fixed inset-0 z-40 bg-black/40" onClick={() => setListOpen(false)} aria-hidden="true" />
      )}
      <div className={`w-60 flex-shrink-0 border-r border-gray-200 flex-col ${
        listOpen
          ? 'flex bg-gray-50 max-sm:fixed max-sm:inset-y-0 max-sm:left-0 max-sm:z-50 max-sm:w-72 max-sm:max-w-[85vw] max-sm:shadow-xl max-sm:pt-safe max-sm:pb-safe'
          : 'hidden sm:flex'
      }`}>
        {/* New chat in a band as tall as the top bar beside it (60px), so its
            line under it runs on with the bar's across the page. */}
        <div className="h-[60px] flex-shrink-0 flex items-center px-3 border-b border-gray-200">
          <button
            onClick={newConversation}
            className="w-full h-9 flex items-center justify-center gap-2 px-3 rounded-xl text-sm font-semibold text-white bg-indigo-600 hover:bg-indigo-700 transition-colors"
          >
            <PlusCircle className="w-4 h-4" />
            {t('chat.newChat')}
          </button>
        </div>
        <div className="p-3 pb-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Escape') setQuery(''); }}
              placeholder={t('chat.search.placeholder')}
              aria-label={t('chat.search.placeholder')}
              data-testid="chat-search"
              className="w-full h-8 rounded-lg border border-gray-200 bg-white pl-8 pr-7 text-xs text-gray-700 placeholder:text-gray-400 focus:border-indigo-300 focus:outline-none focus:ring-2 focus:ring-indigo-100 [&::-webkit-search-cancel-button]:hidden"
            />
            {query && (
              <button type="button" onClick={() => setQuery('')} aria-label={t('chat.search.clear')}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-0.5 text-gray-400 hover:text-gray-600">
                {searching ? <Loader className="h-3.5 w-3.5 animate-spin" /> : <X className="h-3.5 w-3.5" />}
              </button>
            )}
          </div>
        </div>

        {/* Top panel — normal chats */}
        <div className="flex-1 min-h-0 flex flex-col">
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
          {results && !results.length && (
            <p className="text-xs text-gray-400 text-center py-6 px-3" data-testid="chat-search-none">
              {searching ? t('chat.search.searching') : t('chat.search.none')}
            </p>
          )}
          {!results && visibleConversations.length === 0 && (
            <p className="text-xs text-gray-400 text-center py-10 px-3 leading-relaxed">
              No conversations yet.
              <br />
              Click <strong>{t('chat.newChat')}</strong> to start.
            </p>
          )}
          {shown.map((conv) => (
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
              onClick={() => {
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
              }}
              className={`w-full text-left px-3 py-2 rounded-lg text-xs group flex items-start gap-2 transition-colors
                ${currentConvId === conv.id
                  ? 'bg-indigo-50 text-indigo-700'
                  : 'text-gray-600 hover:bg-white hover:shadow-sm'
                }`}
            >
              {/* Being answered now: a turn this tab sent (it goes on while
                  another conversation is open), or anyone else's. A chat at
                  rest has no icon. */}
              {(turns?.[conv.id] || runningChats?.has(conv.id)) && (
                <Loader
                  className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 animate-spin text-indigo-500"
                  data-testid="chat-conv-running"
                  aria-label={t('chat.answering')}
                />
              )}
              <div className="flex-1 min-w-0">
                <div className="truncate leading-5">{conv.title}</div>
                {conv.match && (
                  <div className="mt-0.5 line-clamp-2 text-[11px] leading-4 text-gray-400" data-testid="chat-search-match">{conv.match}</div>
                )}
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
                  {conv.origin === 'telegram' && (
                    <span className="inline-flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded bg-sky-100 text-sky-700 font-medium">
                      <SendIcon className="w-2.5 h-2.5 flex-shrink-0" />
                      {t('chat.telegram2')}
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
              {conv.origin !== 'assistant' && (
                <button
                  onClick={(e) => deleteConversation(conv.id, e)}
                  className="opacity-0 group-hover:opacity-100 p-0.5 rounded text-gray-400 hover:text-red-500 flex-shrink-0 transition-opacity"
                  title={t('chat.delete')}
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              )}
            </div>
          ))}
          </div>
        </div>

        {/* Bottom panel — Telegram chats. Collapsed entirely when there are none,
            so the top panel claims the full column. When present, it takes
            exactly the bottom half of the column (flex-1 + a matching flex-1 on
            the top "Chats" panel makes them split 50/50). Overflow uses the
            macOS-style overlay scrollbar via overflow-y-auto. */}
        {visibleTelegramBindings.length > 0 && (
          <div className="flex-1 min-h-0 flex flex-col border-t border-gray-200">
            <div className="px-3 pt-2 pb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-gray-400 font-semibold flex-shrink-0">
              <SendIcon className="w-3 h-3" /> {t('chat.telegramChats')}
            </div>
            <div className="flex-1 overflow-y-auto px-2 pb-3 space-y-0.5">
              {visibleTelegramBindings.map((b) => {
                const convId = b.conversation_id;
                const isActive = currentConvId === convId;
                const agent = selectableAgents.find((a) => a.id === b.agent_id);
                return (
                  <button
                    key={`tg-${b.chat_id}`}
                    onClick={() => {
                      if (!convId) return;
                      setListOpen(false);
                      // Ensure a local conversation entry exists so the main pane renders.
                      setConversations((prev) => {
                        if (prev.some((c) => c.id === convId)) return prev;
                        return [{
                          id: convId,
                          title: b.title || `Telegram ${b.chat_id}`,
                          agent_id: b.agent_id,
                          workspace: b.workspace || null,
                          messages: [],
                          origin: 'telegram',
                          chat_id: b.chat_id,
                          createdAt: b.created_at || new Date().toISOString(),
                        }, ...prev];
                      });
                      if (b.agent_id && selectableAgents.some((a) => a.id === b.agent_id)) {
                        setSelectedAgent(b.agent_id);
                      }
                      navigate(`/chat/${convId}`);
                    }}
                    className={`w-full text-left px-3 py-2 rounded-lg text-xs flex items-start gap-2 transition-colors
                      ${isActive ? 'bg-indigo-50 text-indigo-700' : 'text-gray-600 hover:bg-white hover:shadow-sm'}`}
                  >
                    <SendIcon className="w-3.5 h-3.5 mt-0.5 flex-shrink-0 opacity-70" />
                    <div className="flex-1 min-w-0">
                      <div className="truncate leading-5">{b.title || t('chat.telegramChat', { id: b.chat_id })}</div>
                      <div className="mt-0.5 flex flex-wrap gap-1">
                        <span className="inline-block text-[9px] px-1.5 py-0.5 rounded bg-violet-100 text-violet-600 font-medium truncate max-w-full">
                          {agent ? agent.name : b.agent_id}
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
          </div>
        )}
      </div>
    </>
  );
}
