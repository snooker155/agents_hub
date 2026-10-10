/**
 * The Code side panel: every `kind: "code"` view produced in this
 * conversation, grouped by filename (an agent revising a snippet may answer
 * with a new view of the same filename rather than a new version of the same
 * one, so grouping happens on the frontend, across view ids), with the
 * selected snippet at work in a CodeWorkbench (components/code): editor,
 * versions, run and save actions.
 *
 * The list itself is the page's (useConversationCode.js): the top bar counts
 * the snippets on the Code button, this panel shows them.
 */
import { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { useChatPage } from './context';
import { getView } from '../../api';
import { createCodeFromReply, getSnippetVersions } from '../../api/code';
import { replyBlockLabel } from './replyCode';
import CodeWorkbench from '../code/CodeWorkbench';

function SnippetListItem({ group, selected, onSelect }) {
  const { latest } = group;
  return (
    <button
      type="button"
      onClick={() => onSelect(latest.view_id)}
      className={`w-full text-left px-3 py-2 border-b border-gray-100 dark:border-gray-800 border-l-2 ${
        selected
          ? 'bg-indigo-50 dark:bg-indigo-950 border-l-indigo-500'
          : 'border-l-transparent hover:bg-gray-50 dark:hover:bg-gray-800'
      }`}
    >
      <div className="text-xs font-medium text-gray-800 dark:text-gray-100 truncate">{group.filename}</div>
      <div className="text-[10px] text-gray-500 dark:text-gray-400 flex items-center gap-1">
        {latest.language && <span className="uppercase">{latest.language}</span>}
        {latest.version != null && <span>v{latest.version}</span>}
        {group.items.length > 1 && <span>· {group.items.length}</span>}
      </div>
    </button>
  );
}

/** A fenced block of a reply, listed under "From replies" until it is saved. */
function ReplyListItem({ block, selected, onSelect, t }) {
  return (
    <button
      type="button"
      onClick={() => onSelect(block.id)}
      data-testid="reply-snippet"
      className={`w-full text-left px-3 py-2 border-b border-gray-100 dark:border-gray-800 border-l-2 ${
        selected
          ? 'bg-indigo-50 dark:bg-indigo-950 border-l-indigo-500'
          : 'border-l-transparent hover:bg-gray-50 dark:hover:bg-gray-800'
      }`}
    >
      <div className="text-xs font-medium text-gray-800 dark:text-gray-100 truncate">{replyBlockLabel(block, t('chat.codeBlock.plain'))}</div>
      <div className="text-[10px] text-gray-500 dark:text-gray-400 flex items-center gap-1">
        {block.language && <span className="uppercase">{block.language}</span>}
        <span>· {t('chat.code.replyUnsaved')}</span>
      </div>
    </button>
  );
}

const isReplyId = (id) => typeof id === 'string' && id.startsWith('reply:');

export default function CodePanel() {
  const {
    codeFocus, codeListError: listError, codeListLoading: listLoading, codeRows: rows, currentConv, currentConvId,
    markReplySaved = () => {}, projects, replyBlocks = [], savedReplyIds = null, selectedWorkspace,
    setCodeFocus = () => {}, setInput, t, textareaRef,
  } = useChatPage();

  const [envelopes, setEnvelopes] = useState({});
  const [pickedViewId, setSelectedViewId] = useState(null);

  // Another conversation: its snippets are not this one's. Drop the selection
  // at once rather than keep showing the old snippet (still in the envelope
  // cache) until the new list arrives.
  const [seenConvId, setSeenConvId] = useState(currentConvId);
  if (seenConvId !== currentConvId) {
    setSeenConvId(currentConvId);
    setSelectedViewId(null);
  }

  // "Open in Code panel" on a reply's code block selects that block under
  // "From replies"; a view just saved comes with its envelope and is selected.
  const [seenFocus, setSeenFocus] = useState(null);
  if (seenFocus !== codeFocus) {
    setSeenFocus(codeFocus);
    const view = codeFocus?.view;
    if (codeFocus?.replyId) {
      setSelectedViewId(codeFocus.replyId);
    } else if (view?.view_id) {
      setEnvelopes((prev) => ({ ...prev, [view.view_id]: view }));
      setSelectedViewId(view.view_id);
    }
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

  // Select the newest snippet when nothing is selected, or when the selected one
  // has left the list (a conversation switch, a list refetch without it).
  // Derived, not mirrored into state by an effect.
  const pickedStillListed = pickedViewId && (rows.some((r) => r.view_id === pickedViewId)
    || replies.some((b) => b.id === pickedViewId));
  const selectedViewId = pickedStillListed
    ? pickedViewId
    : (groups[0]?.latest?.view_id || replies[0]?.id || null);

  const isReply = isReplyId(selectedViewId);
  const selectedReply = isReply ? replies.find((b) => b.id === selectedViewId) || null : null;
  const replyWorkspace = currentConv?.workspace || selectedWorkspace;
  // A block's own version history, kept by the backend under a key of this
  // conversation and block, without a view (api/code.js getSnippetVersions).
  // Loaded when the block is selected: its latest version is what the editor
  // opens with, and Versions and diff read the same history.
  const replyKey = selectedReply ? `${currentConvId || 'conv'}:${selectedReply.id}` : null;
  const [replyVersions, setReplyVersions] = useState({ key: null, versions: [] });
  useEffect(() => {
    if (!replyKey) return undefined;
    let cancelled = false;
    getSnippetVersions(replyKey, replyWorkspace)
      .then((res) => { if (!cancelled) setReplyVersions({ key: replyKey, versions: res.data?.versions || [] }); })
      .catch(() => { if (!cancelled) setReplyVersions({ key: replyKey, versions: [] }); });
    return () => { cancelled = true; };
  }, [replyKey, replyWorkspace]);
  const replyLatest = replyVersions.key === replyKey && replyVersions.versions.length
    ? replyVersions.versions[replyVersions.versions.length - 1] : null;

  // A reply's block is shown through the same shape a view has, so the
  // workbench needs no second path. Kept by identity: the workbench resets on
  // a new envelope object, so a saved version (a new latest) is what the
  // editor shows next. `base` is the reply's own text, version 1 of a history.
  const replyEnvelope = useMemo(() => (selectedReply ? {
    view_id: selectedReply.id, kind: 'code', reply: true,
    spec: {
      filename: selectedReply.name || selectedReply.filename, language: selectedReply.language,
      body: replyLatest ? replyLatest.body : selectedReply.body,
      version: replyLatest ? replyLatest.version : undefined,
      base: selectedReply.body,
    },
  } : null), [selectedReply, replyLatest]);
  const selectedEnvelope = isReply ? replyEnvelope : (selectedViewId ? envelopes[selectedViewId] : null);

  // "Save as view", and only on request: the block (as edited in the
  // workbench) becomes a code view owned by the reply's run, joins the list
  // and stays selected; the reply's copy leaves "From replies". Nothing else
  // makes a view: a block is run, versioned and saved to a project as it is.
  const [savingView, setSavingView] = useState(false);
  const [saveViewError, setSaveViewError] = useState('');
  const [draftForView, setDraftForView] = useState(null);
  const handleSaveAsView = async () => {
    if (!selectedReply) return;
    setSavingView(true);
    setSaveViewError('');
    try {
      const { data } = await createCodeFromReply({
        body: draftForView ?? replyEnvelope.spec.body, language: selectedReply.language,
        filename: selectedReply.name || selectedReply.filename,
        runId: selectedReply.run_id, workspace: replyWorkspace,
      });
      markReplySaved(selectedReply.id);
      setCodeFocus({ view: data, nonce: Date.now() });
    } catch (e) {
      setSaveViewError(e?.response?.data?.detail || t('chat.code.saveFailed'));
    } finally {
      setSavingView(false);
    }
  };
  const saveAsView = useMemo(() => (isReply ? { onClick: handleSaveAsView, busy: savingView, error: saveViewError } : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [isReply, savingView, saveViewError, selectedReply, replyEnvelope, draftForView]);

  const onVersionSaved = (data) => {
    if (isReply) setReplyVersions({ key: replyKey, versions: Array.isArray(data) ? data : [] });
    else if (data?.view_id) setEnvelopes((prev) => ({ ...prev, [data.view_id]: data }));
  };

  const chat = useMemo(() => ({
    setInput: (text) => { setInput(text); textareaRef?.current?.focus(); },
  }), [setInput, textareaRef]);

  const selectSnippet = (viewId) => {
    setSelectedViewId(viewId);
  };

  return (
    <div className="flex-1 min-h-0 flex overflow-hidden">
      {/* Snippet list */}
      <div className="w-48 flex-shrink-0 border-r border-gray-200 dark:border-gray-700 overflow-y-auto">
        {listLoading && groups.length === 0 ? (
          <div className="flex items-center gap-2 text-xs text-gray-500 p-3">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t('chat.code.loadingList')}
          </div>
        ) : listError ? (
          <div className="p-3 text-xs text-red-600">{listError}</div>
        ) : groups.length === 0 && replies.length === 0 ? (
          <div className="p-3 text-xs text-gray-500 italic">{t('chat.code.empty')}</div>
        ) : (
          <>
            {groups.map((group) => (
              <SnippetListItem
                key={group.filename}
                group={group}
                selected={group.items.some((it) => it.view_id === selectedViewId)}
                onSelect={selectSnippet}
              />
            ))}
            {replies.length > 0 && (
              <>
                <div className="px-3 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400" title={t('chat.code.fromRepliesHint')}>
                  {t('chat.code.fromReplies')}
                </div>
                {replies.map((block) => (
                  <ReplyListItem key={block.id} block={block} selected={block.id === selectedViewId} onSelect={selectSnippet} t={t} />
                ))}
              </>
            )}
          </>
        )}
      </div>

      {/* The selected snippet at work */}
      <div className="flex-1 min-w-0 flex flex-col">
        {!selectedViewId || !selectedEnvelope ? (
          <div className="flex-1 flex items-center justify-center text-xs text-gray-500 dark:text-gray-400 p-6 text-center leading-relaxed">
            {groups.length === 0 && replies.length === 0 && !listLoading ? t('chat.code.emptyHint') : t('chat.code.selectASnippet')}
          </div>
        ) : (
          <CodeWorkbench
            envelope={selectedEnvelope}
            isReply={isReply}
            replyKey={replyKey}
            workspace={isReply ? replyWorkspace : selectedEnvelope.workspace}
            projects={projects}
            onVersionSaved={onVersionSaved}
            onDraftChange={isReply ? setDraftForView : undefined}
            saveAsView={saveAsView}
            chat={chat}
            className="flex-1 min-h-0"
          />
        )}
      </div>
    </div>
  );
}
