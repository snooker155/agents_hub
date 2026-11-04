import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Loader, MessageSquare, Search, X } from 'lucide-react';
import { useChatSearch } from './chatSearch';

const RECENT = 12;

/**
 * Search over the chats, opened from the search icon beside New chat: the
 * titles of the loaded chats match at once, the messages and older chats
 * come from the server (chatSearch.js). With nothing typed it lists the most
 * recent chats. A click, or Enter on the highlighted row, opens the chat
 * (`onOpen(conv)`, the same path as a row of the list) and closes the window;
 * arrow keys move, Escape closes.
 */
export default function ChatSearchModal({ conversations, workspace, onOpen, onClose, t }) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef(null);
  const listRef = useRef(null);
  const { results, searching } = useChatSearch(query, conversations, workspace);
  const rows = results || conversations.slice(0, RECENT);

  useEffect(() => { inputRef.current?.focus(); }, []);

  // Keep the row the arrow keys move to in view; never the one the mouse
  // rests on, so the list does not scroll away under the pointer.
  const moveTo = (index) => {
    setActive(index);
    const row = listRef.current?.querySelector(`[data-index="${index}"]`);
    if (row && typeof row.scrollIntoView === 'function') row.scrollIntoView({ block: 'nearest' });
  };

  const pick = (conv) => {
    if (!conv) return;
    onClose();
    onOpen(conv);
  };

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); moveTo(Math.min(rows.length - 1, active + 1)); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); moveTo(Math.max(0, active - 1)); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(rows[active]); }
    else if (e.key === 'Escape') { e.preventDefault(); onClose(); }
  };

  const showWorkspace = !workspace || workspace === 'default';

  return createPortal(
    // In the middle of the screen, at a fixed height so it does not jump as the results change.
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/40 p-3" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={t('chat.search.placeholder')}
        data-testid="chat-search-modal"
        onMouseDown={(e) => e.stopPropagation()}
        className="w-full max-w-2xl h-[min(70vh,40rem)] bg-white rounded-2xl shadow-2xl border border-gray-200 overflow-hidden flex flex-col"
      >
        <div className="flex items-center gap-3 px-5 py-4 border-b border-gray-100">
          <Search className="w-5 h-5 text-gray-400 shrink-0" />
          <input
            ref={inputRef}
            type="search"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setActive(0); }}
            onKeyDown={onKeyDown}
            placeholder={t('chat.search.placeholder')}
            aria-label={t('chat.search.placeholder')}
            data-testid="chat-search"
            className="flex-1 min-w-0 bg-transparent border-0 p-0 text-base text-gray-800 placeholder:text-gray-400 outline-none focus:ring-0 [&::-webkit-search-cancel-button]:hidden"
          />
          {searching && <Loader className="w-4 h-4 text-gray-400 animate-spin shrink-0" />}
          <button type="button" onClick={onClose} aria-label={t('chat.search.close')}
            className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div ref={listRef} role="listbox" className="flex-1 overflow-y-auto py-1">
          {!results && rows.length > 0 && (
            <p className="px-4 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">{t('chat.search.recent')}</p>
          )}
          {rows.length === 0 && (
            <p className="px-4 py-8 text-center text-xs text-gray-400" data-testid="chat-search-none">
              {searching ? t('chat.search.searching') : results ? t('chat.search.none') : t('chat.search.empty')}
            </p>
          )}
          {rows.map((conv, i) => (
            <button
              key={conv.id}
              type="button"
              role="option"
              aria-selected={i === active}
              data-index={i}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(conv)}
              className={`w-full flex items-start gap-3 px-4 py-2.5 text-left transition-colors ${i === active ? 'bg-indigo-50' : ''}`}
            >
              <MessageSquare className="w-4 h-4 mt-0.5 text-gray-400 shrink-0" />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2 min-w-0">
                  <span className="truncate text-sm text-gray-800">{conv.title}</span>
                  {showWorkspace && conv.workspace && conv.workspace !== 'default' && (
                    <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-600 font-medium">{conv.workspace}</span>
                  )}
                </span>
                {conv.match && (
                  <span className="block mt-0.5 line-clamp-2 text-xs leading-4 text-gray-500" data-testid="chat-search-match">{conv.match}</span>
                )}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}
