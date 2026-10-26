/**
 * The code of a conversation, in its Artifacts panel (panels.jsx): every
 * `kind: "code"` view produced in it, grouped by filename (an agent revising
 * a snippet may answer with a new view of the same filename rather than a new
 * version of the same one, so grouping happens on the frontend, across view
 * ids), and the code blocks of its replies not yet saved as views; the
 * selected one at work in a CodeWorkbench (components/code): editor,
 * versions, run and save actions.
 *
 * Three parts: useChatCode.js (the list, from the page: useConversationCode.js),
 * CodeSection (its rows in the panel's list) and CodeDetail (the workbench).
 */
import { useEffect, useMemo, useState } from 'react';
import { Code2, Loader2 } from 'lucide-react';
import { useI18n } from '../../i18n';
import { useChatPage } from './context';
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

/** The code's rows in the Artifacts panel's list; `selectedId` is a view or reply id. */
export function CodeSection({ code, selectedId, onSelect, className = '' }) {
  const { t } = useI18n();
  if (!code || (!code.count && !code.listLoading && !code.listError)) return null;
  return (
    <section className={className} data-testid="artifacts-code">
      <div className="flex items-center gap-1.5 px-3 pb-1 text-xs font-semibold text-gray-600">
        <Code2 className="w-3.5 h-3.5 text-indigo-500" />
        {t('chat.code.sectionCount', { count: code.count })}
      </div>
      {code.listLoading && !code.count ? (
        <div className="flex items-center gap-2 text-xs text-gray-500 px-3 py-1.5">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t('chat.code.loadingList')}
        </div>
      ) : code.listError ? (
        <div className="px-3 py-1.5 text-xs text-red-600">{code.listError}</div>
      ) : null}
      {code.groups.map((group) => (
        <SnippetListItem
          key={group.filename}
          group={group}
          selected={group.items.some((it) => it.view_id === selectedId)}
          onSelect={onSelect}
        />
      ))}
      {code.replies.length > 0 && (
        <>
          <div className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400" title={t('chat.code.fromRepliesHint')}>
            {t('chat.code.fromReplies')}
          </div>
          {code.replies.map((block) => (
            <ReplyListItem key={block.id} block={block} selected={block.id === selectedId} onSelect={onSelect} t={t} />
          ))}
        </>
      )}
    </section>
  );
}

/** The selected snippet at work: a saved view, or a reply's block. */
export function CodeDetail({ code, viewId }) {
  const {
    currentConv, currentConvId, markReplySaved = () => {}, projects, selectedWorkspace,
    setCodeFocus = () => {}, setInput, t, textareaRef,
  } = useChatPage();
  const { envelopes, setEnvelopes, replies } = code;

  const isReply = isReplyId(viewId);
  const selectedReply = isReply ? replies.find((b) => b.id === viewId) || null : null;
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
  const selectedEnvelope = isReply ? replyEnvelope : (viewId ? envelopes[viewId] : null);

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

  if (!selectedEnvelope) {
    return (
      <div className="flex-1 flex items-center justify-center text-xs text-gray-500 dark:text-gray-400 p-6 text-center leading-relaxed">
        <Loader2 className="w-4 h-4 animate-spin" />
      </div>
    );
  }
  return (
    <div className="flex-1 min-w-0 flex flex-col" data-testid="artifact-code-detail">
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
    </div>
  );
}
