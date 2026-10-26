/**
 * The code of a conversation as its Artifacts panel lists it (CodePanel.jsx
 * draws it): the saved code views by filename and the replies' blocks not
 * yet saved as views.
 */
import { useEffect, useMemo, useState } from 'react';
import { useChatPage } from './context';
import { getView } from '../../api';

/**
 * The conversation's code for the Artifacts panel: `groups` (saved views by
 * filename), `replies` (unsaved blocks, newest first), `has(id)`, the newest
 * one's id, and `focus` ({ id, nonce }) when "Open in Code panel" asks for
 * one. Inside the Chat page only (its context).
 */
export function useChatCode() {
  const {
    codeFocus, codeListError: listError, codeListLoading: listLoading, codeRows, currentConvId,
    replyBlocks = [], savedReplyIds = null,
  } = useChatPage();
  const rows = useMemo(() => codeRows || [], [codeRows]);
  const [envelopes, setEnvelopes] = useState({});

  // A view just saved comes with its envelope.
  const [seenFocus, setSeenFocus] = useState(null);
  if (seenFocus !== codeFocus) {
    setSeenFocus(codeFocus);
    const view = codeFocus?.view;
    if (view?.view_id) setEnvelopes((prev) => ({ ...prev, [view.view_id]: view }));
  }

  // The replies' blocks not yet saved as views: newest first, minus the ones
  // saved this visit and the ones a loaded view already holds word for word
  // (a block saved on an earlier visit).
  const replies = useMemo(() => {
    const bodies = new Set(Object.values(envelopes).map((env) => env?.spec?.body).filter(Boolean));
    return [...replyBlocks]
      .filter((b) => !(savedReplyIds && savedReplyIds.has(b.id)) && !bodies.has(b.body))
      .reverse();
  }, [replyBlocks, savedReplyIds, envelopes]);

  // Full envelopes (filename/language/version/body) for whatever the list rows
  // don't have cached yet — the index rows above carry only kind/title.
  useEffect(() => {
    const missing = rows.map((r) => r.view_id).filter((id) => !envelopes[id]);
    if (!missing.length) return undefined;
    let cancelled = false;
    Promise.all(missing.map((id) => getView(id).then((r) => [id, r.data]).catch(() => [id, null])))
      .then((pairs) => {
        if (cancelled) return;
        setEnvelopes((prev) => {
          const next = { ...prev };
          for (const [id, env] of pairs) if (env) next[id] = env;
          return next;
        });
      });
    return () => { cancelled = true; };
  }, [rows, envelopes]);

  const groups = useMemo(() => {
    const byFilename = new Map();
    for (const row of rows) {
      const env = envelopes[row.view_id];
      const filename = env?.spec?.filename || row.title || row.view_id;
      const list = byFilename.get(filename) || [];
      list.push({
        view_id: row.view_id,
        version: env?.spec?.version,
        language: env?.spec?.language || '',
        updated_at: row.updated_at || row.created_at || '',
      });
      byFilename.set(filename, list);
    }
    const arr = Array.from(byFilename.entries()).map(([filename, items]) => {
      const sorted = [...items].sort((a, b) => (b.version ?? 0) - (a.version ?? 0));
      return { filename, items: sorted, latest: sorted[0] };
    });
    arr.sort((a, b) => (b.latest?.updated_at || '').localeCompare(a.latest?.updated_at || ''));
    return arr;
  }, [rows, envelopes]);

  const focus = useMemo(() => {
    const id = codeFocus?.replyId || codeFocus?.view?.view_id || null;
    return id ? { id, nonce: codeFocus.nonce } : null;
  }, [codeFocus]);

  return useMemo(() => ({
    conversationId: currentConvId,
    groups, replies, envelopes, setEnvelopes, listLoading, listError, focus,
    count: groups.length + replies.length,
    first: groups[0]?.latest?.view_id || replies[0]?.id || null,
    has: (id) => rows.some((r) => r.view_id === id) || replies.some((b) => b.id === id),
  }), [currentConvId, groups, replies, envelopes, listLoading, listError, focus, rows]);
}
