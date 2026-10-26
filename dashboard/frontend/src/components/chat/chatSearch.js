/**
 * Search over the conversations, for the list beside the chat (ChatSidebar):
 * the loaded titles match at once, the server adds the chats whose messages
 * hold the words (with the text around the hit) and those older than the
 * loaded page (common/chat_store.py search_chats).
 */
import { useEffect, useMemo, useState } from 'react';
import { searchChats } from '../../api';

const fold = (s) => String(s || '').split(/\s+/).filter(Boolean).join(' ').toLocaleLowerCase();

/** The chats matching `query`, `{ results, searching }`; null results with no query. */
export function useChatSearch(query, conversations, workspace) {
  const needle = fold(query);
  const [server, setServer] = useState({ needle: '', items: [] });
  useEffect(() => {
    if (!needle) return undefined;
    let alive = true;
    const timer = setTimeout(() => {
      searchChats({ q: needle, ...(workspace && workspace !== 'default' ? { workspace } : {}) })
        .then((r) => { if (alive) setServer({ needle, items: r.data?.items || [] }); })
        .catch(() => { if (alive) setServer({ needle, items: [] }); });
    }, 250);
    return () => { alive = false; clearTimeout(timer); };
  }, [needle, workspace]);
  const results = useMemo(() => {
    if (!needle) return null;
    const local = (conversations || []).filter((c) => fold(c.title).includes(needle));
    const ids = new Set(local.map((c) => c.id));
    const fromServer = server.needle === needle ? server.items : [];
    const known = new Map((conversations || []).map((c) => [c.id, c]));
    const more = fromServer.filter((c) => !ids.has(c.id))
      .map((c) => (known.has(c.id) ? { ...known.get(c.id), match: c.match } : { ...c, _searchOnly: true }));
    return [...local, ...more];
  }, [needle, conversations, server]);
  return { results, searching: Boolean(needle) && server.needle !== needle };
}
