import React, { useEffect, useState, useRef, useCallback, useMemo } from 'react';
import { Link, useParams, useNavigate } from 'react-router-dom';
import {
  Shapes, Undo2, Plus, Layers, Sparkles, X, Circle, Bookmark,
  BookmarkPlus, ExternalLink, SlidersHorizontal, PanelRightClose, PanelRightOpen,
} from 'lucide-react';
import {
  getView, getViewOps, createStudioView, applyViewOps, revertView, setViewState,
  getViewCheckpoints, saveViewCheckpoint, revertViewToCheckpoint,
  getViewChat, clearViewChat, stopViewChat, viewChatUrl,
} from '../api';
import { useWorkspace } from '../components/workspace';
import { useChannel } from '../components/stream';
import { applyOp } from '../views/opsClient';
import ViewRenderer from '../views/ViewRenderer';
import ControlsPanel from '../views/ControlsPanel';
import EntityChat from '../components/EntityChat';
import { usePageChat, usePageChatPanel } from '../components/pageChat/pageChat';

import { AppBar } from '../components/PageLayout';
import { useI18n } from '../i18n';
// Kind labels live in the i18n `viewKinds` namespace so the Studio picker and
// the Views gallery name the same thing the same way.
const KIND_VALUES = [
  'graph', 'scene3d', 'simulation', 'math', 'process', 'chart',
  'table', 'html', 'diagram', 'latex', 'slides', 'document',
];
const kinds = (t) => KIND_VALUES.map((value) => ({ value, label: t(`viewKinds.${value}`) }));

// ── outliner: object tree derived from the live doc ──────────────────────────
// Which spec subtrees form the tree, per kind (annotations/controls are added
// for every kind below). Collections are keyed maps; arrays also work.
const OUTLINER_GROUPS = {
  graph: [['Nodes', 'nodes'], ['Edges', 'edges']],
  scene3d: [['Objects', 'objects'], ['Lights', 'lights']],
  process: [['Nodes', 'nodes'], ['Edges', 'edges'], ['Lanes', 'lanes']],
  slides: [['Slides', 'slides']],
  table: [['Columns', 'columns']],
  math: [['Params', 'params']],
  simulation: [['Entities', 'entities'], ['Params', 'params']],
};

function elementLabel(id, el) {
  if (el == null || typeof el !== 'object') {
    return typeof el === 'string' ? el : `${id}: ${JSON.stringify(el)}`;
  }
  if (el.label) return el.label;
  if (el.title) return el.title;
  if (el.name) return el.name;
  if (el.source) return `${el.source}→${el.target}`;
  if (el.type) return `${el.type} · ${id}`;
  return id;
}

function Outliner({ doc, selection, onSelect }) {
  const { t } = useI18n();
  const spec = doc?.spec || {};
  const asEntries = (c) => (Array.isArray(c)
    ? c.map((e, i) => [e?.id ?? String(i), e])
    : Object.entries(c || {}));
  const groups = [];
  for (const [name, key] of OUTLINER_GROUPS[doc?.kind] || []) {
    const entries = asEntries(spec[key]);
    if (entries.length) groups.push([name, entries]);
  }
  const annotations = asEntries(doc?.annotations);
  if (annotations.length) groups.push(['Annotations', annotations]);
  const controls = asEntries(doc?.controls);
  if (controls.length) groups.push(['Controls', controls]);
  if (!groups.length) return <div className="text-xs text-gray-400 p-3">{t('studio.noObjectsYet')}</div>;
  return (
    <div className="text-sm">
      {groups.map(([name, entries]) => (
        <div key={name} className="mb-2">
          <div className="px-3 py-1 text-[11px] uppercase tracking-wide text-gray-400">{name} ({entries.length})</div>
          {entries.map(([id, el]) => (
            <button
              key={id}
              onClick={() => onSelect(id)}
              className={`w-full text-left px-3 py-1 flex items-center gap-2 hover:bg-gray-100 dark:hover:bg-gray-800 ${
                selection === id ? 'bg-indigo-50 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300' : 'text-gray-600 dark:text-gray-300'}`}
            >
              <Circle className="w-2 h-2 fill-current opacity-50" />
              <span className="truncate">{elementLabel(id, el)}</span>
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

// ── linked view pane: a live secondary view sharing this session's canvas ────
// Renders one view named in the main view's `link.views` and keeps it live on
// its own op channel — e.g. the throughput chart beside a traffic simulation
// (it picks the shared frames up from the timebase bus, see viewBus).
function LinkedPane({ viewId, onSelect }) {
  const { t } = useI18n();
  const [doc, setDoc] = useState(null);
  useEffect(() => {
    let cancelled = false;
    getView(viewId).then((r) => { if (!cancelled) setDoc(r.data); }).catch(() => setDoc(null));
    return () => { cancelled = true; };
  }, [viewId]);
  useChannel(viewId ? `view:${viewId}` : null, useCallback((ev) => {
    if (ev.type === 'view_op' && ev.op) setDoc((d) => (d ? applyOp(d, ev.op) : d));
    else if (ev.type === 'view_reset') setDoc((d) => (d ? { ...d, spec: ev.doc?.spec, controls: ev.doc?.controls } : d));
  }, []));
  if (!doc) return <div className="text-xs text-gray-400 p-3">{t('studio.loadingLinkedView')}</div>;
  return (
    <div className="flex-1 min-w-0 flex flex-col border-l first:border-l-0 border-gray-200 dark:border-gray-700">
      <div className="px-2 py-1 text-[11px] text-gray-500 border-b border-gray-200 dark:border-gray-700 truncate">
        {doc.title || doc.kind} <span className="text-gray-400">{t('studio.linked')}</span>
      </div>
      <div className="flex-1 min-h-0 overflow-auto p-1">
        <ViewRenderer view={doc} onSelect={onSelect} className="h-full" />
      </div>
    </div>
  );
}

// ── checkpoint menu: save / restore named states of the op history ───────────
function CheckpointMenu({ viewId, maxSeq, onReverted }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [cps, setCps] = useState({});

  const refresh = useCallback(async () => {
    try { const r = await getViewCheckpoints(viewId); setCps(r.data?.checkpoints || {}); }
    catch { setCps({}); }
  }, [viewId]);

  const toggle = async () => { if (!open) await refresh(); setOpen((o) => !o); };

  const save = async () => {
    const name = window.prompt(t('studio.checkpointNamePrompt'), `at-op-${maxSeq}`);
    if (!name || !name.trim()) return;
    try { await saveViewCheckpoint(viewId, name.trim()); await refresh(); } catch { /* keep menu open */ }
  };

  const restore = async (name) => {
    try { await revertViewToCheckpoint(viewId, name); setOpen(false); onReverted(); } catch { /* noop */ }
  };

  const names = Object.entries(cps).sort((a, b) => a[1] - b[1]);
  return (
    <div className="relative">
      <button onClick={toggle} title={t('studio.checkpoints')}
        className="inline-flex items-center gap-1 px-2.5 py-1.5 text-sm rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
        <Bookmark className="w-4 h-4" /> {t('studio.checkpoints')}
      </button>
      {open && (
        <div className="absolute right-0 mt-1 w-56 z-20 rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg py-1 text-sm">
          <button onClick={save}
            className="w-full text-left px-3 py-1.5 flex items-center gap-2 text-indigo-600 dark:text-indigo-400 hover:bg-gray-50 dark:hover:bg-gray-800">
            <BookmarkPlus className="w-4 h-4" /> {t('studio.saveCheckpoint')}
          </button>
          {names.length > 0 && <div className="my-1 border-t border-gray-100 dark:border-gray-800" />}
          {names.map(([name, seq]) => (
            <button key={name} onClick={() => restore(name)} title={`Revert to op ${seq}`}
              className="w-full text-left px-3 py-1.5 flex items-center justify-between gap-2 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
              <span className="truncate">{name}</span>
              <span className="text-[10px] font-mono text-gray-400">#{seq}</span>
            </button>
          ))}
          {!names.length && <div className="px-3 py-1.5 text-xs text-gray-400">{t('studio.noCheckpointsYet')}</div>}
        </div>
      )}
    </div>
  );
}

// ── new-view picker ──────────────────────────────────────────────────────────
function NewViewPicker({ onCreate }) {
  const { t } = useI18n();
  const [kind, setKind] = useState('graph');
  const [title, setTitle] = useState('');
  return (
    <div className="flex flex-col items-center justify-center h-full text-center gap-4">
      <Sparkles className="w-10 h-10 text-indigo-500" />
      <div>
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">{t('studio.startAVisualization')}</h2>
        <p className="text-sm text-gray-500">{t('studio.pickAKindThenBuild')}</p>
      </div>
      <div className="flex items-center gap-2">
        <select value={kind} onChange={(e) => setKind(e.target.value)}
          className="rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2 text-sm">
          {kinds(t).map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
        </select>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t('studio.titleOptional')}
          className="rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2 text-sm" />
        <button onClick={() => onCreate(kind, title)}
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-md bg-indigo-600 text-white text-sm hover:bg-indigo-700">
          <Plus className="w-4 h-4" /> {t('studio.create')}
        </button>
      </div>
    </div>
  );
}

// ── studio chat (the Visualizer, pinned to this view) ────────────────────────

/**
 * The open view's build chat.
 *
 * The same entity chat every other builder page has: the transcript lives on
 * the server under `view:<id>`, so a reload resumes the session and the
 * floating page-chat panel can host the very conversation this column shows
 * rather than opening a second one about the same view.
 *
 * `registerSend` is what makes the view's own buttons — a control the agent
 * authored, a `sendToAgent` bridge inside an html view — post into this chat.
 *
 * The callbacks are memoised on the view id because EntityChat loads its
 * transcript in an effect keyed on them; fresh closures each render would
 * refetch the conversation continuously.
 */
function useViewChatDescriptor(viewId, registerSend) {
  const { t } = useI18n();

  const loadChat = useCallback(() => getViewChat(viewId), [viewId]);
  const clearChat = useCallback(() => clearViewChat(viewId), [viewId]);
  const stopChat = useCallback(() => stopViewChat(viewId), [viewId]);

  return useMemo(() => (viewId ? {
    scope: `view:${viewId}`,
    path: viewChatUrl(viewId),
    loadChat, clearChat, stopChat, registerSend,
    title: t('studio.visualizer'),
    emptyHint: t('studio.chatEmptyHint'),
    suggestions: [
      t('studio.suggestBuild'),
      t('studio.suggestControls'),
      t('studio.suggestExplain'),
    ],
  } : null), [viewId, loadChat, clearChat, stopChat, registerSend, t]);
}

// ── the Studio page ──────────────────────────────────────────────────────────
export default function Studio() {
  const { t } = useI18n();
  const { viewId } = useParams();
  const navigate = useNavigate();
  const { selectedWorkspace } = useWorkspace();
  const [loadedDoc, setDoc] = useState(null);
  // With no view selected there is nothing to show. Deriving it beats an
  // effect whose only job was to reset the state.
  const doc = viewId ? loadedDoc : null;
  const [maxSeq, setMaxSeq] = useState(0);
  const [selection, setSelection] = useState(null);
  const [loading, setLoading] = useState(() => Boolean(viewId));
  const chatSendRef = useRef(null);
  // Stable across renders on purpose: EntityChat registers in an effect keyed
  // on it, and a fresh function each render would re-register continuously.
  const registerChatSend = useCallback((fn) => { chatSendRef.current = fn; }, []);

  // The view's build chat, drawn either in the column on the right or in the
  // floating panel — one descriptor, so it is the same conversation either way.
  const viewChat = useViewChatDescriptor(viewId, registerChatSend);
  usePageChat(viewChat);
  const { inlineSuppressed: panelHoldsChat } = usePageChatPanel();
  // Folding the chat away is also how the canvas gets the full width, and how
  // the floating page-chat launcher comes back — it stands down while a chat
  // of the page's own is on screen.
  const [showChat, setShowChat] = useState(true);
  // The chat's Clear lands in this panel's own header, left of the fold-away
  // button — state rather than a ref so the chat re-renders once the slot
  // exists to portal into.
  const [chatClearSlot, setChatClearSlot] = useState(null);

  // Load the view + its op log when the id changes.
  useEffect(() => {
    if (!viewId) return;
    Promise.all([getView(viewId), getViewOps(viewId)])
      .then(([v, o]) => {
        setDoc(v.data);
        const list = o.data?.ops || [];
        setMaxSeq(list.length ? list[list.length - 1].seq : 0);
        setSelection(v.data?.state?.selection || null);
      })
      .catch(() => setDoc(null))
      .finally(() => setLoading(false));
  }, [viewId]);

  // Live op stream: apply each op to the local doc as it arrives.
  useChannel(viewId ? `view:${viewId}` : null, useCallback((ev) => {
    if (ev.type === 'view_op' && ev.op) {
      setDoc((d) => (d ? applyOp(d, ev.op) : d));
      setMaxSeq((s) => Math.max(s, ev.op.seq || 0));
    } else if (ev.type === 'view_reset') {
      setDoc((d) => (d ? { ...d, spec: ev.doc?.spec, controls: ev.doc?.controls } : d));
      setMaxSeq(ev.seq || 0);
    }
  }, []));

  const onCreate = async (kind, title) => {
    const r = await createStudioView(kind, title, selectedWorkspace || undefined);
    navigate(`/studio/${r.data.view_id}`);
  };

  const onSelect = useCallback((id) => {
    setSelection(id);
    if (viewId) setViewState(viewId, { ...(doc?.state || {}), selection: id }).catch(() => {});
  }, [viewId, doc]);

  const onControlOp = useCallback((op, persist = true) => {
    // Optimistic local apply; the server echo will confirm. Ephemeral changes
    // (a play-control animation frame) skip persistence so the op log only
    // records values worth keeping.
    setDoc((d) => (d ? applyOp(d, op) : d));
    if (persist && viewId) applyViewOps(viewId, [op]).catch(() => {});
  }, [viewId]);

  const reload = useCallback(async () => {
    if (!viewId) return;
    try {
      const [v, o] = await Promise.all([getView(viewId), getViewOps(viewId)]);
      setDoc(v.data);
      const list = o.data?.ops || [];
      setMaxSeq(list.length ? list[list.length - 1].seq : 0);
    } catch { /* keep current doc */ }
  }, [viewId]);

  const onUndo = async () => {
    if (!viewId || maxSeq <= 0) return;
    await revertView(viewId, maxSeq - 1);
    const v = await getView(viewId);
    setDoc(v.data);
    setMaxSeq(maxSeq - 1);
  };

  if (!viewId) {
    return (
      <div className="h-full overflow-hidden p-6">
        <NewViewPicker onCreate={onCreate} />
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      <AppBar
        icon={Shapes}
        title={doc?.title || 'Studio'}
        backTo="/views"
        backLabel={t('studio.views')}
        badges={doc?.kind && (
          <span className="text-xs px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500">{doc.kind}</span>
        )}
        actions={<>
          <button onClick={onUndo} disabled={maxSeq <= 0}
            className="inline-flex items-center gap-1 px-2.5 py-1.5 text-sm rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-40">
            <Undo2 className="w-4 h-4" /> {t('studio.undo')}
          </button>
          <CheckpointMenu viewId={viewId} maxSeq={maxSeq} onReverted={reload} />
          <Link to={`/views/${viewId}`} title={t('studio.openTheFullViewPage')}
            className="inline-flex items-center gap-1 px-2.5 py-1.5 text-sm rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
            <ExternalLink className="w-4 h-4" /> {t('studio.fullView')}
          </Link>
          <button onClick={() => navigate('/studio')}
            className="inline-flex items-center gap-1 px-2.5 py-1.5 text-sm rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
            <Plus className="w-4 h-4" /> {t('studio.new')}
          </button>
          {/* Last in the row: bringing the chat back is the counterpart of the
              fold-away button at the far right of the screen it returns to. */}
          {!showChat && !panelHoldsChat && (
            <button onClick={() => setShowChat(true)} title={t('studio.showChat')}
              className="inline-flex items-center gap-1 px-2.5 py-1.5 text-sm rounded-md border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
              <PanelRightOpen className="w-4 h-4" />
            </button>
          )}
        </>}
      />

      {/* two panes */}
      <div className="flex-1 flex min-h-0">
        {/* view block */}
        <div className="flex-1 flex min-w-0">
          {/* Left rail: what the view is made of, and the knobs that drive it.
              One column rather than two opposite walls — the controls are read
              against the object list they act on, and splitting them across
              the viewport meant looking away from one to reach the other. The
              viewport gets the width the right-hand panel used to hold. */}
          <div className="w-60 border-r border-gray-200 dark:border-gray-700 flex-shrink-0 flex flex-col min-h-0">
            <div className="shrink-0 px-3 py-2 text-xs font-semibold text-gray-500 flex items-center gap-1">
              <Layers className="w-3.5 h-3.5" /> {t('studio.outliner')}
            </div>
            {/* The object list takes whatever the controls leave: it is the
                longer of the two and the one you scroll. */}
            <div className="flex-1 min-h-0 overflow-y-auto">
              <Outliner doc={doc} selection={selection} onSelect={onSelect} />
            </div>
            {/* Half the rail each: the controls are as much of the view as the
                objects are, and a panel that sized itself to its contents
                moved the split every time the agent added a slider. */}
            <div className="h-1/2 shrink-0 overflow-y-auto border-t border-gray-200 dark:border-gray-700">
              <div className="px-3 py-2 text-xs font-semibold text-gray-500 flex items-center gap-1">
                <SlidersHorizontal className="w-3.5 h-3.5" /> {t('studio.controls')}
              </div>
              <div className="px-3 pb-3">
                <ControlsPanel view={doc} onOp={onControlOp}
                  onAction={(text) => chatSendRef.current && chatSendRef.current(text)} />
                {!(doc?.controls && (Array.isArray(doc.controls) ? doc.controls.length : Object.keys(doc.controls).length)) && (
                  <div className="text-xs text-gray-400">{t('studio.theAgentAddsControlsHere')}</div>
                )}
              </div>
            </div>
          </div>
          {/* viewport + linked views + op strip */}
          <div className="flex-1 flex flex-col min-w-0">
            <div className="flex-1 relative bg-gray-50 dark:bg-gray-950 min-h-0">
              {loading && <div className="absolute inset-0 grid place-items-center text-gray-400">{t('studio.loading')}</div>}
              {doc && (
                <ViewRenderer
                  view={doc}
                  onSelect={onSelect}
                  onOp={onControlOp}
                  onSendToAgent={(text) => chatSendRef.current && chatSendRef.current(text)}
                  onState={(s) => viewId && setViewState(viewId, { ...(doc?.state || {}), ...s }).catch(() => {})}
                  className="h-full"
                />
              )}
            </div>
            {(doc?.link?.views || []).length > 0 && (
              <div className="h-56 flex border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900">
                {(doc.link.views || []).slice(0, 3).map((vid) => (
                  <LinkedPane key={vid} viewId={vid}
                    onSelect={doc?.link?.selection === false ? undefined : onSelect} />
                ))}
              </div>
            )}
          </div>
        </div>

        {/* The studio chat, folded away while the floating panel is showing
            it: same endpoint, same thread, drawn in one place at a time. */}
        {showChat && !panelHoldsChat && (
          <div className="w-[28rem] border-l border-gray-200 dark:border-gray-700 flex-shrink-0 flex flex-col">
            <div className="flex items-center px-3 py-2 border-b border-gray-100 dark:border-gray-800">
              <div className="flex items-center gap-1.5 text-sm font-medium text-gray-700 dark:text-gray-200">
                <Sparkles className="w-4 h-4 text-violet-500" /> {t('studio.visualizer')}
              </div>
              <span ref={setChatClearSlot} className="ml-auto flex items-center" />
              <button onClick={() => setShowChat(false)} title={t('studio.hideChat')}
                className="ml-1 p-1 rounded-md text-gray-400 hover:text-gray-600 hover:bg-gray-50 dark:hover:bg-gray-800">
                <PanelRightClose className="w-4 h-4" />
              </button>
            </div>
            {/* Keyed on the view: another view is another thread, and the
                previous one's feed must not flash in it while it loads. */}
            <EntityChat
              key={viewId}
              {...viewChat}
              header={false}
              clearTarget={chatClearSlot}
              heightClass="min-h-0 max-h-none"
              className="flex-1 min-h-0 p-3"
              composerClassName="mt-auto pt-3"
            />
          </div>
        )}
      </div>
    </div>
  );
}
