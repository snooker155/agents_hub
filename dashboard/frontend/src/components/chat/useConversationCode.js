/**
 * The `kind: "code"` views this conversation produced, fetched from the
 * conversation's own run ids (`listViews` per run, the filter the Views
 * gallery uses) so a code view_ref that just arrived in the transcript shows
 * up as soon as `conversationRunIds` picks up its run.
 *
 * Held by the page rather than the Code panel: the top bar counts them on the
 * Code button whether or not the panel is open, and the panel reads the same
 * list when it is.
 */
import { useEffect, useMemo, useState } from 'react';
import { listViews } from '../../api';

export function focusRow(view) {
  const now = new Date().toISOString();
  return {
    view_id: view.view_id, kind: 'code', title: view.title || view.spec?.filename || '',
    created_at: view.created_at || now, updated_at: view.updated_at || now,
  };
}

export function useConversationCode({ conversationRunIds, currentConvId, codeFocus, t }) {
  const [rows, setRows] = useState([]);
  const [fetchedKey, setFetchedKey] = useState('');
  const [error, setError] = useState('');

  // A stable key so the fetch only re-runs when the set of run ids changes
  // membership, not on every re-render of the recomputed Set.
  const runIdsKey = useMemo(
    () => Array.from(conversationRunIds || []).sort().join(','),
    [conversationRunIds],
  );

  // Loading is derived: the list is loading while the ids have not been fetched.
  const loading = !!runIdsKey && fetchedKey !== runIdsKey;

  // Reset on input changes, adjusted during render instead of in effects. The
  // order matches what the effects did: ids, then conversation, then focus.
  const [idsFor, setIdsFor] = useState(runIdsKey);
  if (idsFor !== runIdsKey) {
    setIdsFor(runIdsKey);
    setError('');
    if (!runIdsKey) setRows([]);
  }
  // Another conversation: its snippets are not this one's.
  const [convFor, setConvFor] = useState(currentConvId);
  if (convFor !== currentConvId) {
    setConvFor(currentConvId);
    setRows([]);
  }
  // "Open in Code panel" on a reply's code block: the snippet's run is already
  // in the conversation, but the list was fetched before the snippet existed.
  const [focusFor, setFocusFor] = useState(codeFocus);
  if (focusFor !== codeFocus) {
    setFocusFor(codeFocus);
    const view = codeFocus?.view;
    if (view?.view_id) {
      setRows((prev) => (prev.some((r) => r.view_id === view.view_id) ? prev : [focusRow(view), ...prev]));
    }
  }

  useEffect(() => {
    const ids = runIdsKey ? runIdsKey.split(',') : [];
    if (!ids.length) return undefined;
    let cancelled = false;
    Promise.all(ids.map((id) => listViews({ run_id: id }).then((r) => r.data?.views || []).catch(() => [])))
      .then((lists) => {
        if (cancelled) return;
        const merged = new Map();
        for (const list of lists) {
          for (const row of list) {
            if (row.kind === 'code') merged.set(row.view_id, row);
          }
        }
        // A snippet just opened from a reply may predate this list's fetch.
        const focused = codeFocus?.view;
        if (focused?.view_id && !merged.has(focused.view_id)) merged.set(focused.view_id, focusRow(focused));
        setRows(Array.from(merged.values()));
      })
      .catch((e) => { if (!cancelled) setError(e?.response?.data?.detail || t('chat.code.listFailed')); })
      .finally(() => { if (!cancelled) setFetchedKey(runIdsKey); });
    return () => { cancelled = true; };
    // codeFocus is read, not watched: the focus adjustment above adds a focused snippet.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runIdsKey, t]);

  return { codeRows: rows, codeListLoading: loading, codeListError: error };
}
