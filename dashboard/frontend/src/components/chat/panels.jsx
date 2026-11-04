/**
 * The side panel's two halves: what the agent's process looked like, and the
 * files it changed, with the diff for each.
 */
import GraphMirror from '../GraphMirror';
import FitText from '../FitText';
import ProcessGraph, { TokenPill } from '../ProcessGraph';
import ViewCard from '../../views/ViewCard';
import { useI18n } from '../../i18n';
import { AArrowDown, AArrowUp, ChevronDown, ChevronLeft, ChevronUp, Code2, ExternalLink, FileText, LayoutGrid, Loader2, Workflow } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { getWorkspaceFileContent } from '../../api';
import CodeBlock from '../CodeBlock';
import { codeLanguageFor } from '../../lib/codeLanguage';
import { renderContent } from './markdown';
import { ARTIFACT_OP_META, listedArtifacts } from './artifactOps';
import { CodeDetail, CodeSection } from './CodePanel';
import FileWorkbench from '../code/FileWorkbench';
import { ChatPageContext } from './context';

function ProcessPanelContent({ processInsights, topology = null, graphRun = null, workspace = null }) {
  const { t } = useI18n();
  const graphKey = (processInsights?.message_runs || [])
    .map((mr, idx) => `${mr?.run_id || mr?.message_id || idx}`)
    .join('|');
  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-3">
      <div className="flex flex-wrap gap-1">
        <TokenPill label={t('chat.sessionIn')} value={processInsights?.token_usage?.inbound_tokens || 0} />
        <TokenPill label={t('chat.sessionOut')} value={processInsights?.token_usage?.outbound_tokens || 0} />
        <TokenPill label={t('chat.sessionTotal')} value={processInsights?.token_usage?.total_tokens || 0} />
        {/* The bill above sums every LLM call of every turn — an agent loop
            resends the conversation each step. This is the largest single
            prompt the session sent, the same figure the context meter tracks. */}
        {(processInsights?.context_peak || 0) > 0 && (
          <TokenPill
            label={t('chat.sessionPeak')}
            value={
              processInsights.context_window_tokens > 0
                ? `${processInsights.context_peak} / ${processInsights.context_window_tokens}`
                : processInsights.context_peak
            }
            title={t('chat.sessionPeakTooltip')}
          />
        )}
      </div>
      {/* An imported agent that is a graph inside: its own shape, with the node
          it is in right now lit and the ones it has been through marked. The
          hub does not run this graph, so there is nothing to click — the value
          is seeing which branch a live run took. */}
      {topology?.nodes?.length > 0 && (
        <div className="border border-gray-100 rounded-lg p-3">
          <div className="flex items-center gap-1.5 mb-2">
            <Workflow className="w-3.5 h-3.5 text-indigo-500" />
            <span className="text-xs font-semibold text-gray-700">{t('chat.agentGraph')}</span>
            {graphRun?.active && (
              <span className="text-[11px] text-indigo-600 font-medium truncate">{graphRun.active}</span>
            )}
          </div>
          <GraphMirror
            topology={topology}
            activeNode={graphRun?.active || null}
            visitedNodes={graphRun?.visited || []}
          />
        </div>
      )}
      <ProcessGraph
        key={graphKey}
        messageRuns={processInsights.message_runs || []}
        evalCaseWorkspace={workspace}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Build view — unified diff renderer (no external deps)
// ---------------------------------------------------------------------------
// `wrap` folds long lines instead of scrolling sideways: prose (a replayed
// answer) is one line per paragraph, code is not.
function DiffView({ diff, wrap = false }) {
  const { t } = useI18n();
  if (!diff) {
    return <div className="px-3 py-2 text-xs text-gray-400 italic">{t('chat.noTextualDiffAvailable')}</div>;
  }
  const lines = diff.split('\n');
  return (
    <div className="font-mono text-[13px] leading-relaxed overflow-x-auto">
      {lines.map((line, i) => {
        let cls = 'text-gray-600';
        let bg = '';
        if (line.startsWith('+++') || line.startsWith('---')) {
          cls = 'text-gray-400';
        } else if (line.startsWith('@@')) {
          cls = 'text-indigo-500';
          bg = 'bg-indigo-50/60';
        } else if (line.startsWith('+')) {
          cls = 'text-emerald-700';
          bg = 'bg-emerald-50';
        } else if (line.startsWith('-')) {
          cls = 'text-red-700';
          bg = 'bg-red-50';
        }
        return (
          <div key={i} className={`px-3 ${wrap ? 'whitespace-pre-wrap break-words' : 'whitespace-pre'} ${bg} ${cls}`}>
            {line || ' '}
          </div>
        );
      })}
    </div>
  );
}

function ArtifactItem({ artifact, defaultOpen = false }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  const meta = ARTIFACT_OP_META[artifact.op] || ARTIFACT_OP_META.modify;
  return (
    <div data-artifact-path={artifact.path} className="rounded-lg border border-gray-200 bg-white overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-gray-50"
      >
        <span className={`w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold flex-shrink-0 ${meta.cls}`}>
          {meta.label}
        </span>
        <span className="flex-1 min-w-0 text-xs font-medium text-gray-700 truncate" title={artifact.path}>
          {artifact.path}
        </span>
        <span className="flex items-center gap-1.5 flex-shrink-0 text-[10px] font-mono">
          {artifact.additions > 0 && <span className="text-emerald-600">+{artifact.additions}</span>}
          {artifact.deletions > 0 && <span className="text-red-600">−{artifact.deletions}</span>}
        </span>
        {open ? <ChevronUp className="w-3 h-3 text-gray-400" /> : <ChevronDown className="w-3 h-3 text-gray-400" />}
      </button>
      {open && (
        <div className="border-t border-gray-100 py-2 max-h-[400px] overflow-y-auto">
          {artifact.binary ? (
            <div className="px-3 py-2 text-xs text-gray-400 italic">
              {artifact.truncated ? t('chat.fileTooLarge') : t('chat.binaryFile')}
            </div>
          ) : (
            <DiffView diff={artifact.diff} />
          )}
        </div>
      )}
    </div>
  );
}

const PREVIEW_LINES = 5;

/** The first lines of a text, without trailing blank ones; `more` tells
 * whether lines were left out. */
function headLines(lines) {
  const body = [...lines];
  while (body.length && !body[body.length - 1]) body.pop();
  return { lines: body.slice(0, PREVIEW_LINES), more: body.length > PREVIEW_LINES };
}

/** The start of the diff, file headers dropped. */
const diffHead = (artifact) => headLines((artifact.diff || '').split('\n')
  .filter((l) => !l.startsWith('+++') && !l.startsWith('---')));

/** A new file's text, read off its diff with the plus signs taken away. */
const addedText = (artifact) => (artifact.diff || '').split('\n')
  .filter((l) => !l.startsWith('+++') && !l.startsWith('---') && !l.startsWith('@@'))
  .map((l) => (l.startsWith('+') ? l.slice(1) : l)).join('\n');

// What a file holds now, read once per file and change for the cards of the
// Files mode (a changed file's diff has only its hunks, not its text).
const fileHeads = new Map();

function useFileHead(workspace, artifact, enabled) {
  const key = `${workspace}:${artifact.path}:${artifact.diff?.length || 0}:${artifact.additions || 0}:${artifact.deletions || 0}`;
  const [state, setState] = useState(() => fileHeads.get(key) ?? null);
  useEffect(() => {
    if (!enabled || !workspace || fileHeads.has(key)) return undefined;
    let cancelled = false;
    getWorkspaceFileContent(workspace, artifact.path)
      .then((r) => { fileHeads.set(key, r.data?.content ?? ''); if (!cancelled) setState(fileHeads.get(key)); })
      .catch(() => { if (!cancelled) setState(''); });
    return () => { cancelled = true; };
  }, [enabled, workspace, key, artifact.path]);
  return enabled ? (fileHeads.get(key) ?? state) : null;
}

const DIFF_LINE_CLS = (line) => (line.startsWith('@@') ? 'text-indigo-500'
  : line.startsWith('+') ? 'text-emerald-700 bg-emerald-50'
    : line.startsWith('-') ? 'text-red-700 bg-red-50' : 'text-gray-600');

/** A file in the narrow panel's list: a block across the whole list with its
 * name and folder over its first lines (an ellipsis when there is more), like
 * a tool result block in a coding agent. Files mode shows what the file holds
 * now (a new file's text comes from its diff, a changed one is read from the
 * workspace); Diff mode shows the start of the diff and the counts. */
function FileCard({ artifact, onPick, mode, workspace }) {
  const { t } = useI18n();
  const meta = ARTIFACT_OP_META[artifact.op] || ARTIFACT_OP_META.modify;
  const parts = artifact.path.split('/');
  const name = parts.pop();
  const textless = artifact.binary || artifact.op === 'delete';
  const diff = mode === 'diff';
  const read = useFileHead(workspace, artifact, !textless && !diff && artifact.op !== 'add' && Boolean(workspace));
  const content = artifact.op === 'add' || !workspace ? addedText(artifact) : read;
  const { lines, more } = textless ? { lines: [], more: false }
    : diff ? diffHead(artifact) : headLines((content ?? '').split('\n'));
  return (
    <button type="button" onClick={onPick} data-artifact-path={artifact.path} data-testid="artifact-card" title={artifact.path}
      className="group block w-full text-left rounded-lg border border-gray-300 bg-white overflow-hidden shadow-sm transition-colors hover:border-indigo-300 hover:bg-indigo-50/40">
      {/* No selected mark: narrow, the list and the open item never show at
          once, and the panel always has one picked. */}
      <span className="flex items-center gap-2 px-3 py-2 border-b border-gray-300 transition-colors bg-gray-200 group-hover:border-indigo-200 group-hover:bg-indigo-100">
        <span className={`w-4 h-4 rounded flex items-center justify-center text-[9px] font-bold shrink-0 ${meta.cls}`}>{meta.label}</span>
        {/* A long name steps its font down to fit; the folder gives way first. */}
        <FitText sizes={[14, 13, 12, 11]} className="min-w-0 font-semibold text-gray-900 transition-colors group-hover:text-indigo-700">{name}</FitText>
        {parts.length > 0 && <span className="min-w-0 shrink-[4] truncate text-xs text-gray-500">{parts.join('/')}</span>}
        {diff && (
          <span className="ml-auto flex items-center gap-1.5 shrink-0 text-[10px] font-mono">
            {artifact.additions > 0 && <span className="text-emerald-600">+{artifact.additions}</span>}
            {artifact.deletions > 0 && <span className="text-red-600">−{artifact.deletions}</span>}
          </span>
        )}
      </span>
      {textless ? (
        <span className="block px-3 py-2 text-xs text-gray-400 italic">
          {artifact.op === 'delete' ? t('chat.artifactDeleted') : t('chat.binaryFile')}
        </span>
      ) : !diff && content == null ? (
        <span className="flex items-center px-3 py-2 text-gray-400"><Loader2 className="w-3.5 h-3.5 animate-spin" /></span>
      ) : (
        <span className="block py-2 font-mono text-[12px] leading-5" data-testid="artifact-card-preview" data-mode={mode}>
          {lines.map((line, i) => (
            <span key={i} className={`block px-3 whitespace-pre overflow-hidden text-ellipsis ${diff ? DIFF_LINE_CLS(line) : 'text-gray-700'}`}>
              {line || ' '}
            </span>
          ))}
          {more && <span className="block px-3 leading-4 text-gray-400" data-testid="preview-more">…</span>}
        </span>
      )}
    </button>
  );
}

/** The file as it is now in the workspace folder, read when asked for. */
function ArtifactContent({ workspace, path }) {
  const { t } = useI18n();
  // Mounted per file (keyed by path above), so the fetch runs once per file.
  const [state, setState] = useState({ loading: Boolean(workspace) });
  useEffect(() => {
    if (!workspace) return undefined;
    let cancelled = false;
    getWorkspaceFileContent(workspace, path)
      .then((r) => { if (!cancelled) setState({ content: r.data?.content ?? '' }); })
      .catch((e) => {
        if (!cancelled) setState({ error: e?.response?.data?.detail || t('chat.artifactContentFailed') });
      });
    return () => { cancelled = true; };
  }, [workspace, path, t]);
  if (!workspace) return <div className="p-3 text-xs text-red-600">{t('chat.artifactNoWorkspace')}</div>;
  if (state.loading) {
    return (
      <div className="flex items-center gap-2 p-3 text-xs text-gray-500">
        <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t('chat.artifactLoading')}
      </div>
    );
  }
  if (state.error) return <div className="p-3 text-xs text-red-600">{state.error}</div>;
  const language = codeLanguageFor(path);
  // A Markdown file reads as the document it is, in the panel itself, not
  // as a code block of its source.
  if (language === 'markdown') {
    return <div className="px-4 py-3 text-sm text-gray-800" data-testid="artifact-markdown" data-quote-source={path}>{renderContent(state.content || '')}</div>;
  }
  // The file verbatim in its <pre>: a passage selected there is quoted with
  // its lines (chat/quote.js).
  if (language) return <div className="p-3" data-quote-source={path} data-quote-lines><CodeBlock language={language} code={state.content} /></div>;
  return <pre className="p-3 font-mono text-[13px] leading-relaxed whitespace-pre-wrap break-words text-gray-700" data-quote-source={path} data-quote-lines>{state.content}</pre>;
}

// The size an open file is read at, kept across files and visits: a scale of
// the whole content (code, Markdown or diff alike), stepped by the header's
// smaller / larger buttons.
const FONT_SCALES = [0.8, 0.9, 1, 1.1, 1.25, 1.4, 1.6];
const FONT_SCALE_KEY = 'agent_hub_artifact_font_scale';

function useArtifactFontScale() {
  const [index, setIndex] = useState(() => {
    try {
      const i = FONT_SCALES.indexOf(Number(localStorage.getItem(FONT_SCALE_KEY)));
      return i === -1 ? FONT_SCALES.indexOf(1) : i;
    } catch { return FONT_SCALES.indexOf(1); }
  });
  const step = (by) => setIndex((i) => {
    const next = Math.min(FONT_SCALES.length - 1, Math.max(0, i + by));
    try { localStorage.setItem(FONT_SCALE_KEY, String(FONT_SCALES[next])); } catch { /* storage unavailable */ }
    return next;
  });
  return {
    scale: FONT_SCALES[index],
    smaller: index > 0 ? () => step(-1) : null,
    larger: index < FONT_SCALES.length - 1 ? () => step(1) : null,
  };
}

/** One changed file: what it holds now (``content``) or what changed (``diff``),
 * or, for a code file, at work in the editor (Editor, where it also runs;
 * components/code/FileWorkbench.jsx). `onBack`, when given (a narrow panel
 * showing the file over its list), puts "To the list" before the name on
 * the file's line, as breadcrumbs. */
function ArtifactDetail({ artifact, workspace, mode, onModeChange = null, onBack = null }) {
  const { t } = useI18n();
  const meta = ARTIFACT_OP_META[artifact.op] || ARTIFACT_OP_META.modify;
  // On the Chat page, Discuss / Edit / the run report fill its composer.
  const chatPage = useContext(ChatPageContext);
  const chat = useMemo(() => (chatPage?.setInput ? {
    setInput: (text) => { chatPage.setInput(text); chatPage.textareaRef?.current?.focus(); },
  } : null), [chatPage]);
  // Whether the editor is open, kept per file: the detail is keyed by the file's path.
  const [editor, setEditor] = useState(false);
  const editable = Boolean(workspace) && artifact.op !== 'delete' && !artifact.binary
    && Boolean(codeLanguageFor(artifact.path)) && codeLanguageFor(artifact.path) !== 'markdown';
  const showEditor = editor && editable && mode === 'content';
  const font = useArtifactFontScale();
  return (
    <div className="flex-1 min-w-0 flex flex-col min-h-0">
      <div className="px-3 py-2 border-b border-gray-100 flex items-center gap-2 shrink-0">
        {onBack ? (
          <>
            <button type="button" onClick={onBack} data-testid="artifacts-back"
              className="shrink-0 inline-flex items-center gap-0.5 rounded text-xs font-medium text-gray-500 hover:text-indigo-700">
              <ChevronLeft className="w-3.5 h-3.5" />
              {t('chat.artifactsBack')}
            </button>
            <span className="shrink-0 text-gray-300" aria-hidden="true">/</span>
          </>
        ) : null}
        <span className={`w-5 h-5 rounded flex items-center justify-center text-[10px] font-bold shrink-0 ${meta.cls}`}>
          {meta.label}
        </span>
        <FitText className="flex-1 min-w-0 font-semibold text-gray-800" title={artifact.path}>{artifact.path}</FitText>
        {/* How many lines changed is a matter of the diff, not of the file. */}
        {mode === 'diff' && (
          <span className="flex items-center gap-1.5 shrink-0 text-[10px] font-mono">
            {artifact.additions > 0 && <span className="text-emerald-600">+{artifact.additions}</span>}
            {artifact.deletions > 0 && <span className="text-red-600">−{artifact.deletions}</span>}
          </span>
        )}
        {/* Smaller / larger text for reading; the editor keeps its own size. */}
        {!showEditor && (
          <span className="flex items-center shrink-0 rounded-md border border-gray-200 overflow-hidden" data-testid="artifact-font">
            <button type="button" onClick={font.smaller || undefined} disabled={!font.smaller}
              title={t('chat.fontSmaller')} aria-label={t('chat.fontSmaller')}
              className="p-1 text-gray-500 hover:bg-gray-100 hover:text-gray-800 disabled:opacity-40 disabled:hover:bg-transparent">
              <AArrowDown className="w-3.5 h-3.5" />
            </button>
            <button type="button" onClick={font.larger || undefined} disabled={!font.larger}
              title={t('chat.fontLarger')} aria-label={t('chat.fontLarger')}
              className="p-1 border-l border-gray-200 text-gray-500 hover:bg-gray-100 hover:text-gray-800 disabled:opacity-40 disabled:hover:bg-transparent">
              <AArrowUp className="w-3.5 h-3.5" />
            </button>
          </span>
        )}
        {/* The file's own page under Artifacts; a deleted file has none. */}
        {artifact.url && artifact.op !== 'delete' ? (
          <Link
            to={artifact.url}
            title={t('chat.openArtifactTitle')}
            data-testid="artifact-open"
            className="inline-flex shrink-0 items-center gap-1 rounded-md border border-gray-200 px-2 py-0.5 text-xs text-gray-600 no-underline transition-colors hover:border-indigo-300 hover:bg-indigo-50"
          >
            <ExternalLink className="h-3 w-3" />
            {t('chat.openArtifact')}
          </Link>
        ) : null}
        {editable && mode === 'content' ? (
          <button
            type="button"
            onClick={() => setEditor(!showEditor)}
            title={showEditor ? t('chat.code.closeEditor') : t('chat.code.openEditorHint')}
            aria-pressed={Boolean(showEditor)}
            data-testid="artifact-editor"
            className={`inline-flex shrink-0 items-center gap-1 rounded-md border px-2 py-0.5 text-xs transition-colors ${
              showEditor ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-gray-200 text-gray-600 hover:border-indigo-300 hover:bg-indigo-50'}`}
          >
            <Code2 className="h-3 w-3" />
            {t('chat.code.editor')}
          </button>
        ) : null}
        {/* Files / Diff on the file's own line, where the panel has no header of its own. */}
        {onModeChange ? <ArtifactsModeSwitch mode={mode} onChange={onModeChange} t={t} /> : null}
      </div>
      {showEditor ? (
        <div className="flex-1 min-h-0 flex flex-col" data-testid="artifact-file-editor" data-quote-source={artifact.path}>
          <FileWorkbench workspace={workspace} path={artifact.path} chat={chat} className="flex-1 min-h-0" />
        </div>
      ) : (
      <div className="flex-1 min-h-0 overflow-auto" data-testid="artifact-detail">
        {/* zoom scales the text and its layout together, whatever the inner sizes. */}
        <div style={{ zoom: font.scale }} data-font-scale={font.scale}>
        {mode === 'content' ? (
          <ArtifactContent key={`${workspace}:${artifact.path}`} workspace={workspace} path={artifact.path} />
        ) : artifact.binary ? (
          <div className="px-3 py-2 text-xs text-gray-400 italic">
            {artifact.truncated ? t('chat.fileTooLarge') : t('chat.binaryFile')}
          </div>
        ) : (
          <div className="py-2" data-quote-source={t('chat.quote.changesTo', { path: artifact.path })}><DiffView diff={artifact.diff} /></div>
        )}
        </div>
      </div>
      )}
    </div>
  );
}

/**
 * The files the runs changed and the views they made: a list on the left,
 * the selected one on the right. ``mode`` is the column's switch: ``content``
 * (the default) shows each file as it is now, so a deleted file is not
 * listed; ``diff`` shows what every run changed, deletions included. A view
 * is drawn whole in either. `onModeChange`, when given, puts the switch on
 * the selected file's line, for a panel without a header to hold it.
 * `picked` (`file:<path>` / `view:<id>`) with `onPick` lets the page choose
 * what is open.
 */
// Narrower than this, the list and the open item do not fit side by side: the
// panel shows the list alone and an item opens over the whole panel.
const STACK_BELOW_PX = 560;

/** Whether the element behind the returned ref is narrower than `px`. */
function useNarrowBox(px) {
  const ref = useRef(null);
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const measure = () => setNarrow(el.getBoundingClientRect().width < px);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [px]);
  return [ref, narrow];
}

/** Whether the window is narrower than `px`; never, when `px` is not given. */
const onResize = (notify) => {
  window.addEventListener('resize', notify);
  return () => window.removeEventListener('resize', notify);
};
function useNarrowScreen(px) {
  return useSyncExternalStore(onResize, () => Boolean(px) && window.innerWidth < px, () => false);
}

// `code`: the Chat page's code (useChatCode.js), listed with the
// views and files and opened in its workbench; other pages have none.
// `pick`: `{ key, nonce }` the page asks to show (a file clicked in the chat).
// `sideBySideFrom`: the window width (px) below which the list and the open
// item never stand side by side, however wide the panel; the main chat sets it.
function ArtifactsPanel({
  artifacts, views = [], workspace = null, mode = 'content', onModeChange = null,
  picked: pickedProp, onPick = null, code = null, pick = null, sideBySideFrom = null,
}) {
  const { t } = useI18n();
  const items = useMemo(
    () => listedArtifacts(artifacts, mode)
      .sort((a, b) => (a.path || '').localeCompare(b.path || '')),
    [artifacts, mode],
  );
  const totals = useMemo(() => {
    let add = 0, del = 0;
    for (const a of items) { add += a.additions || 0; del += a.deletions || 0; }
    return { add, del };
  }, [items]);
  // `file:<path>` or `view:<id>`; the newest view, else the first file, until picked.
  // Held here, or by the page when it picks one too (`picked` / `onPick`:
  // a file clicked in the chat beside the panel).
  const [pickedHere, setPickedHere] = useState(null);
  const picked = onPick ? pickedProp : pickedHere;
  // A narrow panel (a narrow screen, or the panel beside the chat) shows the
  // list alone; picking an item opens it over the whole panel, and Back
  // returns to the list. A wide panel shows both and ignores `detailOpen`.
  const [boxRef, narrowBox] = useNarrowBox(STACK_BELOW_PX);
  const stacked = useNarrowScreen(sideBySideFrom) || narrowBox;
  const [detailOpen, setDetailOpen] = useState(false);
  const setPicked = (key) => {
    setDetailOpen(true);
    (onPick || setPickedHere)(key);
  };
  // "Open in Code panel" on a reply's block selects it here.
  const [seenCodeFocus, setSeenCodeFocus] = useState(null);
  if (code && code.focus !== seenCodeFocus) {
    setSeenCodeFocus(code.focus);
    if (code.focus?.id && !onPick) { setPickedHere(`code:${code.focus.id}`); setDetailOpen(true); }
  }
  const [seenPick, setSeenPick] = useState(null);
  if (pick && pick !== seenPick) {
    setSeenPick(pick);
    if (pick.key && !onPick) { setPickedHere(pick.key); setDetailOpen(true); }
  }
  // The page picked one (a file clicked in the chat beside the panel): open it.
  const [seenPicked, setSeenPicked] = useState(pickedProp);
  if (onPick && pickedProp !== seenPicked) {
    setSeenPicked(pickedProp);
    if (pickedProp) setDetailOpen(true);
  }
  const hasCode = Boolean(code?.count || code?.listLoading || code?.listError);
  const fallback = views.length ? `view:${views[views.length - 1].view_id}`
    : code?.first ? `code:${code.first}`
      : items.length ? `file:${items[0].path}` : null;
  const stillThere = picked && (
    (picked.startsWith('file:') && items.some((a) => `file:${a.path}` === picked))
    || (picked.startsWith('view:') && views.some((v) => `view:${v.view_id}` === picked))
    || (picked.startsWith('code:') && Boolean(code?.has(picked.slice(5)))));
  const selected = stillThere ? picked : fallback;

  if (!items.length && !views.length && !hasCode) {
    return (
      <div className="flex-1 overflow-y-auto p-4">
        <p className="text-xs text-gray-500 italic">{t('chat.noArtifactsYet')}</p>
      </div>
    );
  }
  const selectedFile = selected?.startsWith('file:') ? items.find((a) => `file:${a.path}` === selected) : null;
  const selectedView = selected?.startsWith('view:') ? views.find((v) => `view:${v.view_id}` === selected) : null;
  const selectedCode = selected?.startsWith('code:') ? selected.slice(5) : null;
  const showList = !stacked || !detailOpen;
  const showDetail = !stacked || detailOpen;
  const selectedName = selectedView ? (selectedView.title || selectedView.kind) : '';
  return (
    <div ref={boxRef} className={`flex-1 min-h-0 flex overflow-hidden ${stacked && showDetail ? 'flex-col' : ''}`}
      data-testid="artifacts-box" data-stacked={stacked ? 'true' : 'false'}>
      {/* A file or code carries the way back on its own line (ArtifactDetail,
          CodeWorkbench); a view gets this bar. */}
      {stacked && showDetail && !selectedFile && !selectedCode && (
        <button type="button" onClick={() => setDetailOpen(false)} data-testid="artifacts-back"
          className="shrink-0 flex items-center gap-1 px-3 py-1.5 text-xs font-medium text-gray-600 border-b border-gray-200 hover:bg-gray-50 hover:text-indigo-700 text-left min-w-0">
          <ChevronLeft className="w-3.5 h-3.5 shrink-0" />
          <span className="shrink-0">{t('chat.artifactsBack')}</span>
          {selectedName && <span className="truncate text-gray-400 font-normal">· {selectedName}</span>}
        </button>
      )}
      {showList && (
      <div className={`${stacked ? 'flex-1 min-w-0' : 'w-44 min-[1800px]:w-52 flex-shrink-0 border-r border-gray-200'} overflow-y-auto py-2`}>
        {views.length > 0 && (
          <section>
            <div className="flex items-center gap-1.5 px-3 pb-1 text-xs font-semibold text-gray-600">
              <LayoutGrid className="w-3.5 h-3.5 text-indigo-500" />
              {t('chat.viewCount', { count: views.length })}
            </div>
            {[...views].reverse().map((v) => {
              const key = `view:${v.view_id}`;
              return (
                <button key={key} type="button" onClick={() => setPicked(key)}
                  className={`w-full text-left px-3 py-1.5 text-sm truncate hover:bg-gray-50 ${
                    selected === key ? 'bg-indigo-50 text-indigo-700' : 'text-gray-700'}`} title={v.title || v.view_id}>
                  {v.title || v.view_id}
                </button>
              );
            })}
          </section>
        )}
        <CodeSection
          code={code} selectedId={selectedCode} onSelect={(id) => setPicked(`code:${id}`)} cards={stacked}
          className={views.length ? 'mt-2' : ''}
        />
        {items.length > 0 && (
          <section className={views.length || hasCode ? 'mt-2' : ''}>
            <div className="flex items-center gap-2 px-3 pb-1 text-xs text-gray-500">
              <FileText className="w-3.5 h-3.5 text-indigo-500" />
              <span className="font-semibold text-gray-600">{t('chat.fileCount', { count: items.length })}</span>
              {mode === 'diff' && (
                <>
                  <span className="font-mono text-emerald-600">+{totals.add}</span>
                  <span className="font-mono text-red-600">−{totals.del}</span>
                </>
              )}
            </div>
            {/* Narrow, the list has the panel to itself: cards with the start of
                each change. Wide, beside the open file: one row per file. */}
            {stacked ? (
              <div className="mt-1 px-3 space-y-2">
                {items.map((a) => (
                  <FileCard key={a.path} artifact={a} mode={mode} workspace={workspace} onPick={() => setPicked(`file:${a.path}`)} />
                ))}
              </div>
            ) : items.map((a) => {
              const key = `file:${a.path}`;
              const meta = ARTIFACT_OP_META[a.op] || ARTIFACT_OP_META.modify;
              return (
                <button key={key} type="button" onClick={() => setPicked(key)} data-artifact-path={a.path}
                  className={`w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-gray-50 ${
                    selected === key ? 'bg-indigo-50' : ''}`} title={a.path}>
                  <span className={`w-4 h-4 rounded flex items-center justify-center text-[9px] font-bold shrink-0 ${meta.cls}`}>
                    {meta.label}
                  </span>
                  <span className={`flex-1 min-w-0 text-sm truncate ${selected === key ? 'text-indigo-700 font-medium' : 'text-gray-700'}`}>
                    {a.path.split('/').pop()}
                  </span>
                </button>
              );
            })}
          </section>
        )}
      </div>
      )}
      {/* min-h-0: stacked under Back, the item scrolls inside the panel. */}
      {showDetail && <div className="flex-1 min-h-0 min-w-0 flex">{selectedCode ? (
        <CodeDetail code={code} viewId={selectedCode} onBack={stacked ? () => setDetailOpen(false) : null} />
      ) : selectedFile ? (
        <ArtifactDetail key={selectedFile.path} artifact={selectedFile} workspace={workspace} mode={mode} onModeChange={onModeChange}
          onBack={stacked ? () => setDetailOpen(false) : null} />
      ) : selectedView ? (
        <div
          className="flex-1 min-w-0 overflow-auto p-3"
          data-testid="artifact-detail"
          data-quote-source={selectedView.title || selectedView.kind || t('chat.quote.fallbackSource')}
        >
          <ViewCard key={selectedView.view_id} viewRef={selectedView} actions={(
            // The view's panel under Artifacts, in the Views folder.
            <Link
              to={`/artifacts?folder=__views__&view=${encodeURIComponent(selectedView.view_id)}`}
              title={t('chat.openViewArtifactTitle')}
              data-testid="artifact-open"
              className="inline-flex shrink-0 items-center gap-1 rounded-md border border-gray-200 px-2 py-0.5 text-xs text-gray-600 no-underline transition-colors hover:border-indigo-300 hover:bg-indigo-50"
            >
              <ExternalLink className="h-3 w-3" />
              {t('chat.openArtifact')}
            </Link>
          )} />
        </div>
      ) : null}</div>}
    </div>
  );
}

/** Files (what each file holds now) or Diff (what the runs changed). */
function ArtifactsModeSwitch({ mode, onChange, t }) {
  return (
    <div className="inline-flex rounded-md border border-gray-200 overflow-hidden text-xs" role="tablist" aria-label={t('chat.artifactModes.label')}>
      {[['content', t('chat.artifactModes.files')], ['diff', t('chat.artifactModes.diff')]].map(([key, label], i) => (
        <button key={key} type="button" role="tab" aria-selected={mode === key} onClick={() => onChange(key)}
          className={`px-2 py-0.5 ${i ? 'border-l border-gray-200' : ''} ${
            mode === key ? 'bg-indigo-50 text-indigo-700' : 'text-gray-500 hover:bg-gray-50'}`}>
          {label}
        </button>
      ))}
    </div>
  );
}

export { ProcessPanelContent, DiffView, ArtifactItem, ArtifactsModeSwitch, ArtifactsPanel };
