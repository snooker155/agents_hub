import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  applyNodeChanges,
  applyEdgeChanges,
} from 'reactflow';
import 'reactflow/dist/style.css';
import {
  RefreshCw, Boxes, GitBranch, Sparkles, Save, Undo2,
  Brain, MessageSquare, PanelRightClose, PanelRightOpen,
  Send, PlusCircle, Trash2, Network, X, Pencil, Square,
  FileText, Globe,
} from 'lucide-react';
import {
  getProjectGraph, saveProjectGraph, resetProjectGraph, relayoutProjectGraph,
  getProjectGraphMessages, clearProjectGraphMessages, streamProjectGraphChat,
  stopProjectGraphChat,
} from '../../api';
import { FeedItem } from './ChatFeed';
import ContextMeter from '../ContextMeter';
import { useContextUsage } from '../contextUsage';
import { useI18n } from '../../i18n';
import { useInlineChatOpen } from '../pageChat/pageChat';

// `icon` (a lucide component), `borderStyle`, and `radius` are optional per-kind
// tweaks; styleNode applies them. Most kinds are a plain rounded box.
// Colors come from the --graph-* vars in index.css so a theme switch repaints
// the boxes without re-styling the nodes.
const KIND_STYLE = {
  frontend:  { bg: 'var(--graph-frontend-bg)',  border: 'var(--graph-frontend-border)' },
  backend:   { bg: 'var(--graph-backend-bg)',   border: 'var(--graph-backend-border)' },
  datastore: { bg: 'var(--graph-datastore-bg)', border: 'var(--graph-datastore-border)' },
  // External system / third party: a dashed "outside the boundary" box with a globe.
  external:  { bg: 'var(--graph-external-bg)',  border: 'var(--graph-external-border)', icon: Globe, borderStyle: 'dashed' },
  module:    { bg: 'var(--graph-module-bg)',    border: 'var(--graph-module-border)' },
  project:   { bg: 'var(--graph-project-bg)',   border: 'var(--graph-project-border)' },
  task:      { bg: 'var(--graph-task-bg)',      border: 'var(--graph-task-border)' },
  subtask:   { bg: 'var(--graph-module-bg)',    border: 'var(--graph-module-border)' },
  decision:  { bg: 'var(--graph-decision-bg)',  border: 'var(--graph-decision-border)' },
  actor:     { bg: 'var(--graph-actor-bg)',     border: 'var(--graph-actor-border)' },
  // Artifact / document / output (process view): a warm "paper" sheet with a file
  // icon and squared corners so it reads as a tangible deliverable.
  artifact:  { bg: 'var(--graph-artifact-bg)',  border: 'var(--graph-artifact-border)', icon: FileText, radius: 3 },
  empty:     { bg: 'var(--graph-empty-bg)',     border: 'var(--graph-empty-border)' },
};

const SOURCE_BADGE = {
  auto:      { label: 'Auto', cls: 'bg-gray-100 text-gray-500' },
  manual:    { label: 'Edited', cls: 'bg-amber-100 text-amber-700' },
  generated: { label: 'AI-built', cls: 'bg-violet-100 text-violet-700' },
};

function styleNode(n) {
  const kind = n.data?.kind || 'module';
  const s = KIND_STYLE[kind] || KIND_STYLE.module;
  const name = n.data?.name ?? n.data?.label ?? n.id;
  const subtitle = n.data?.subtitle;
  const group = n.data?.group || '';
  const Icon = s.icon;
  return {
    ...n,
    data: {
      name, kind, subtitle, group,
      label: (
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, lineHeight: 1.2 }}>
          {Icon ? <Icon style={{ width: 13, height: 13, color: s.border, flexShrink: 0, marginTop: 1 }} /> : null}
          <div style={{ minWidth: 0 }}>
            <div style={{ fontWeight: 600, fontSize: 12 }}>{name}</div>
            {subtitle ? <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>{subtitle}</div> : null}
          </div>
        </div>
      ),
    },
    style: {
      background: s.bg, border: `1.5px ${s.borderStyle || 'solid'} ${s.border}`,
      borderRadius: s.radius ?? 10,
      // react-flow's default node color is a hard-coded near-black.
      color: 'var(--text-primary)',
      padding: '6px 10px', width: 200, fontSize: 12,
    },
  };
}

function toRawNode(n) {
  return {
    id: n.id, type: 'default', position: n.position,
    data: { label: n.data?.name ?? n.id, kind: n.data?.kind || 'module', subtitle: n.data?.subtitle || '', group: n.data?.group || '' },
  };
}

// Kinds offered in the manual editor (mirror the kinds the Architect agent uses).
const NODE_KINDS = ['frontend', 'backend', 'datastore', 'external', 'module',
                    'task', 'subtask', 'decision', 'actor', 'artifact'];

// A node id that doesn't collide with any existing node.
function uniqueNodeId(nodes) {
  const taken = new Set(nodes.map((n) => n.id));
  let i = nodes.length + 1;
  let id = `node-${i}`;
  while (taken.has(id)) { i += 1; id = `node-${i}`; }
  return id;
}

// Build an edge with the same shape the agent/back-end produces, so manual and
// AI-built edges save and render identically.
function makeEdge(source, target, label = '') {
  return {
    id: `${source}->${target}`, source, target, label,
    animated: true, markerEnd: { type: 'arrowclosed' },
  };
}

// ── Inspector for the selected node/edge (manual editing) ──────────────────
const FIELD = 'w-full text-sm border border-gray-300 rounded-md px-2 py-1.5 focus:outline-none focus:border-indigo-400 focus:ring-1 focus:ring-indigo-100';

function GraphInspector({ sel, node, edge, nodes, onPatchNode, onPatchEdge, onDelete, onClose }) {
  const { t } = useI18n();
  if (!sel) return null;
  // The selection was deleted out from under us — nothing to show.
  if (sel.kind === 'node' && !node) return null;
  if (sel.kind === 'edge' && !edge) return null;

  const nameOf = (id) => nodes.find((n) => n.id === id)?.data?.name || id;

  return (
    <div className="absolute top-3 right-3 z-20 w-64 bg-white rounded-lg border border-gray-200 shadow-lg">
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-gray-700">
          <Pencil className="w-3.5 h-3.5 text-indigo-500" /> {sel.kind === 'node' ? t('flowProjectGraph.editNode') : t('flowProjectGraph.editEdge')}
        </div>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-600" title={t('flowProjectGraph.close')}><X className="w-4 h-4" /></button>
      </div>

      <div className="p-3 space-y-3">
        {sel.kind === 'node' ? (
          <>
            <div>
              <label className="block text-[11px] font-medium text-gray-500 mb-1">{t('flowProjectGraph.name')}</label>
              <input className={FIELD} value={node.data?.name || ''} autoFocus
                onChange={(e) => onPatchNode({ name: e.target.value })} />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-gray-500 mb-1">{t('flowProjectGraph.kind')}</label>
              <select className={FIELD} value={node.data?.kind || 'module'}
                onChange={(e) => onPatchNode({ kind: e.target.value })}>
                {NODE_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-gray-500 mb-1">{t('flowProjectGraph.subtitle')}</label>
              <input className={FIELD} value={node.data?.subtitle || ''}
                placeholder={t('flowProjectGraph.optionalDetail')}
                onChange={(e) => onPatchNode({ subtitle: e.target.value })} />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-gray-500 mb-1">{t('flowProjectGraph.groupCluster')}</label>
              <input className={FIELD} value={node.data?.group || ''}
                placeholder={t('flowProjectGraph.keepsRelatedNodesTogether')}
                onChange={(e) => onPatchNode({ group: e.target.value })} />
            </div>
          </>
        ) : (
          <>
            <div className="text-[11px] text-gray-500">
              <span className="font-medium text-gray-700">{nameOf(edge.source)}</span>
              <span className="mx-1">→</span>
              <span className="font-medium text-gray-700">{nameOf(edge.target)}</span>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-gray-500 mb-1">{t('flowProjectGraph.label')}</label>
              <input className={FIELD} value={edge.label || ''} autoFocus
                placeholder={t('flowProjectGraph.optional')}
                onChange={(e) => onPatchEdge({ label: e.target.value })} />
            </div>
          </>
        )}

        <button onClick={onDelete}
          className="w-full flex items-center justify-center gap-1.5 text-xs font-medium text-red-600 border border-red-200 hover:bg-red-50 rounded-md px-2 py-1.5">
          <Trash2 className="w-3.5 h-3.5" /> Delete {sel.kind}
        </button>
      </div>
    </div>
  );
}

function ProjectGraph({ projectId }) {
  const { t } = useI18n();
  const [view, setView] = useState('process');   // process view first
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [source, setSource] = useState('auto');
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);        // a chat/generate run is active
  const [stopping, setStopping] = useState(false); // a stop request is in flight
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState('');

  // Chat
  const [feed, setFeed] = useState([]);           // unified transcript + tool steps
  // The architect chat re-sends its transcript every turn, so a long build
  // walks into the model's context ceiling. The meter under the composer shows
  // how close it is; Clear chat is the way back.
  const { usage: contextUsage, observe: observeContext, reset: resetContext } = useContextUsage();
  const [input, setInput] = useState('');
  const [showChat, setShowChat] = useState(true);
  const [showThinking, setShowThinking] = useState(true);  // render the agent's reasoning trace
  const abortRef = useRef(null);
  // While this chat is open its composer owns the bottom-right corner, so the
  // floating page-chat launcher stands down until it is folded away.
  useInlineChatOpen(showChat);

  const onNodesChange = useCallback((changes) => {
    setNodes((nds) => applyNodeChanges(changes, nds));
    if (changes.some((c) => c.type === 'position' || c.type === 'remove')) setDirty(true);
    const removed = changes.filter((c) => c.type === 'remove').map((c) => c.id);
    if (removed.length) setSel((s) => (s && s.kind === 'node' && removed.includes(s.id) ? null : s));
  }, []);
  const onEdgesChange = useCallback((changes) => {
    setEdges((eds) => applyEdgeChanges(changes, eds));
    if (changes.some((c) => c.type === 'remove')) setDirty(true);
    const removed = changes.filter((c) => c.type === 'remove').map((c) => c.id);
    if (removed.length) setSel((s) => (s && s.kind === 'edge' && removed.includes(s.id) ? null : s));
  }, []);

  const applyGraph = useCallback((data) => {
    setNodes((data.nodes || []).map(styleNode));
    setEdges(data.edges || []);
    setSource(data.source || 'auto');
    setDirty(false);
  }, []);

  // Live, incremental application of a single node/edge from the build stream.
  const applyLiveNode = useCallback((node) => {
    setNodes((nds) => {
      const styled = styleNode(node);
      const i = nds.findIndex((n) => n.id === node.id);
      if (i >= 0) { const c = [...nds]; c[i] = styled; return c; }
      return [...nds, styled];
    });
    setSource('generated');
    setDirty(false);
  }, []);
  const applyLiveEdge = useCallback((edge) => {
    setEdges((eds) => {
      const i = eds.findIndex((e) => e.id === edge.id);
      if (i >= 0) { const c = [...eds]; c[i] = { ...c[i], ...edge }; return c; }  // relabel/upsert
      return [...eds, edge];
    });
  }, []);

  // Live removal of a node (and its edges) / a single edge from the build stream.
  const applyLiveDeleteNode = useCallback((id, edgeIds = []) => {
    setNodes((nds) => nds.filter((n) => n.id !== id));
    setEdges((eds) => eds.filter((e) => e.source !== id && e.target !== id
      && !(edgeIds || []).includes(e.id)));
    setSel((s) => (s && s.kind === 'node' && s.id === id ? null : s));
  }, []);
  const applyLiveDeleteEdge = useCallback((id) => {
    setEdges((eds) => eds.filter((e) => e.id !== id));
    setSel((s) => (s && s.kind === 'edge' && s.id === id ? null : s));
  }, []);

  // ── Manual editing ────────────────────────────────────────────────────────
  // Current selection for the inspector: { kind: 'node'|'edge', id } | null.
  const [sel, setSel] = useState(null);
  const rfRef = useRef(null);            // the ReactFlow instance (for placement)
  const canvasWrapRef = useRef(null);    // the canvas viewport element

  const onNodeClick = useCallback((_e, node) => setSel({ kind: 'node', id: node.id }), []);
  const onEdgeClick = useCallback((_e, edge) => setSel({ kind: 'edge', id: edge.id }), []);
  const onPaneClick = useCallback(() => setSel(null), []);

  // Draw an edge when the user connects two node handles.
  const onConnect = useCallback((params) => {
    if (!params.source || !params.target || params.source === params.target) return;
    setEdges((eds) => {
      const id = `${params.source}->${params.target}`;
      if (eds.some((e) => e.id === id)) return eds;
      return [...eds, makeEdge(params.source, params.target)];
    });
    setDirty(true);
  }, []);

  // Add a fresh node near the centre of the current viewport and select it.
  const addNode = useCallback(() => {
    let position = { x: 80, y: 80 };
    try {
      const inst = rfRef.current, el = canvasWrapRef.current;
      if (inst && el && inst.screenToFlowPosition) {
        const r = el.getBoundingClientRect();
        position = inst.screenToFlowPosition({ x: r.left + r.width / 2, y: r.top + r.height / 2 });
      }
    } catch { /* fall back to the default corner position */ }
    const id = uniqueNodeId(nodes);
    const raw = {
      id, type: 'default', position,
      data: { name: t('flowProjectGraph.newNode'), kind: view === 'process' ? 'task' : 'module', subtitle: '', group: '' },
    };
    setNodes((nds) => [...nds, styleNode(raw)]);
    setSel({ kind: 'node', id });
    setDirty(true);
  }, [nodes, t, view]);

  // Patch the selected node's editable fields and re-style it (label rebuilds).
  const patchNode = useCallback((patch) => {
    setNodes((nds) => nds.map((n) => (
      n.id === sel?.id ? styleNode({ ...n, data: { ...n.data, ...patch } }) : n
    )));
    setDirty(true);
  }, [sel]);

  const patchEdge = useCallback((patch) => {
    setEdges((eds) => eds.map((e) => (e.id === sel?.id ? { ...e, ...patch } : e)));
    setDirty(true);
  }, [sel]);

  // Delete the current selection (a node also drops its connected edges).
  const deleteSelected = useCallback(() => {
    if (!sel) return;
    if (sel.kind === 'node') {
      setNodes((nds) => nds.filter((n) => n.id !== sel.id));
      setEdges((eds) => eds.filter((e) => e.source !== sel.id && e.target !== sel.id));
    } else {
      setEdges((eds) => eds.filter((e) => e.id !== sel.id));
    }
    setSel(null);
    setDirty(true);
  }, [sel]);

  const load = useCallback(async (v) => {
    setLoading(true);
    setError('');
    try {
      const [g, m] = await Promise.all([
        getProjectGraph(projectId, v),
        getProjectGraphMessages(projectId, v).catch(() => ({ data: { messages: [], trace: [] } })),
      ]);
      applyGraph(g.data);
      // Prefer the rich trace (thinking + tool/graph steps) so a reload restores
      // the full session; fall back to the bare user/assistant transcript.
      const trace = m.data.trace;
      if (Array.isArray(trace) && trace.length) {
        setFeed(trace);
      } else {
        setFeed((m.data.messages || []).map((x) => ({ k: x.role, text: x.content })));
      }
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('flowProjectGraph.loadFailed'));
      setNodes([]); setEdges([]); setFeed([]);
    } finally {
      setLoading(false);
    }
  }, [projectId, applyGraph, t]);

  useEffect(() => { load(view); }, [view, load]);

  // Drive one build turn over the SSE stream.
  const runChat = useCallback(async (message) => {
    if (!message.trim() || busy) return;
    setBusy(true);
    setError('');
    setShowChat(true);
    setFeed((f) => [...f, { k: 'user', text: message }]);

    const append = (item) => setFeed((f) => [...f, item]);
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await streamProjectGraphChat({
        projectId, view, message, signal: ac.signal,
        onEvent: (ev) => {
          observeContext(ev);
          switch (ev.type) {
            case 'tool_start':
              // graph-builder tools surface as their own node/edge events; show
              // only inspection tools here to avoid noise.
              if (!String(ev.tool || '').startsWith('add_graph')
                  && !String(ev.tool || '').startsWith('delete_graph')
                  && ev.tool !== 'clear_graph') {
                append({ k: 'tool', tool: ev.tool, status: 'running' });
              }
              break;
            case 'graph_node':
              applyLiveNode(ev.node);
              append({ k: 'node', label: (ev.node?.data?.label) || ev.node?.id });
              break;
            case 'graph_edge':
              applyLiveEdge(ev.edge);
              append({ k: 'node', label: '', edge: `${ev.edge.source} → ${ev.edge.target}` });
              break;
            case 'graph_node_delete':
              applyLiveDeleteNode(ev.id, ev.edges);
              append({ k: 'tool', tool: `removed ${ev.id}`, status: 'done' });
              break;
            case 'graph_edge_delete':
              applyLiveDeleteEdge(ev.id);
              append({ k: 'tool', tool: `removed edge ${ev.id}`, status: 'done' });
              break;
            case 'graph_layout':
              // Re-position existing nodes to keep clusters/edges readable as
              // the graph grows (positions computed server-side).
              setNodes((nds) => nds.map((n) => (ev.positions?.[n.id] ? { ...n, position: ev.positions[n.id] } : n)));
              break;
            case 'graph_clear':
              setNodes([]); setEdges([]);
              append({ k: 'tool', tool: t('flowProjectGraph.clearedGraph'), status: 'done' });
              break;
            case 'think':
              // Native model reasoning — the agent's thinking as it builds.
              if (ev.content) append({ k: 'thinking', text: ev.content });
              break;
            case 'thinking':
            case 'native_reasoning': {
              const t = ev.message || ev.content || '';
              // Skip internal log markers ("[llm_start] …") — show real text only.
              if (t && !t.startsWith('[')) append({ k: 'thinking', text: t });
              break;
            }
            case 'error':
              append({ k: 'error', text: ev.error || 'error' });
              break;
            case 'stopped':
              append({ k: 'tool', tool: t('flowProjectGraph.stoppedByYou'), status: 'done' });
              break;
            case 'message':
              if (ev.content) append({ k: 'assistant', text: ev.content });
              break;
            default:
              break;
          }
        },
      });
    } catch (e) {
      if (e.name !== 'AbortError') append({ k: 'error', text: e.message || t('flowProjectGraph.chatFailed') });
    } finally {
      setBusy(false);
      setStopping(false);
      abortRef.current = null;
    }
  }, [busy, projectId, view, applyLiveNode, applyLiveEdge, applyLiveDeleteNode,
      applyLiveDeleteEdge, observeContext, t]);

  useEffect(() => () => { if (abortRef.current) abortRef.current.abort(); }, []);

  const onSend = useCallback(() => {
    const m = input.trim();
    if (!m) return;
    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    runChat(m);
  }, [input, runChat]);

  const onGenerate = useCallback(() => {
    runChat(view === 'process'
      ? t('flowProjectGraph.buildProcessPrompt')
      : t('flowProjectGraph.buildArchitecturePrompt'));
  }, [runChat, t, view]);

  // Stop the in-flight run server-side. Because the agent runs detached from the
  // SSE connection, aborting the fetch alone won't stop it — we must cancel the
  // task on the backend; the stream then closes with the partial result intact.
  const stopChat = useCallback(async () => {
    if (!busy || stopping) return;
    setStopping(true);
    try {
      await stopProjectGraphChat(projectId);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('flowProjectGraph.stopFailed'));
      setStopping(false);
    }
  }, [busy, stopping, projectId, t]);

  const handleSave = useCallback(async () => {
    setSaving(true); setError('');
    try {
      const payload = { nodes: nodes.map(toRawNode), edges: edges.map((e) => ({ ...e })) };
      const res = await saveProjectGraph(projectId, view, payload);
      setSource(res.data.source || 'manual');
      setDirty(false);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Save failed');
    } finally { setSaving(false); }
  }, [projectId, view, nodes, edges]);

  const handleReset = useCallback(async () => {
    setError('');
    try {
      await resetProjectGraph(projectId, view);
      await clearProjectGraphMessages(projectId, view).catch(() => {});
      await load(view);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Reset failed');
    }
  }, [projectId, view, load]);

  // Re-arrange the current canvas with the connection-aware layered layout.
  const handleRelayout = useCallback(async () => {
    if (nodes.length === 0) return;
    setError('');
    try {
      const payload = { nodes: nodes.map(toRawNode), edges: edges.map((e) => ({ ...e })) };
      const res = await relayoutProjectGraph(projectId, view, payload);
      setNodes((res.data.nodes || []).map(styleNode));
      setEdges(res.data.edges || []);
      setDirty(true);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Re-layout failed');
    }
  }, [projectId, view, nodes, edges]);

  // Clear the conversation and start a fresh chat session (the backend rotates
  // the session so the agent's context resets and a new run-thread begins in
  // Messages). The graph is left untouched.
  const clearChat = useCallback(async () => {
    if (busy) return;
    setError('');
    try {
      await clearProjectGraphMessages(projectId, view);
      setFeed([]);
      resetContext();
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Clear chat failed');
    }
  }, [projectId, view, busy, resetContext]);

  const styledNodes = useMemo(() => nodes, [nodes]);

  // Fill the remaining height of the scrolling ancestor so the card ends exactly
  // where the page does — no leftover strip of page scroll under the canvas. The
  // offset is measured inside that scroller (not against the viewport) so it is
  // the same whether or not the page happens to be scrolled while we measure,
  // and the scroller's own bottom padding is left free for the card to sit in.
  const rootRef = useRef(null);
  const [height, setHeight] = useState(0);
  useEffect(() => {
    const el = rootRef.current;
    if (!el) return undefined;

    let scroller = el.parentElement;
    while (scroller && scroller !== document.body) {
      const oy = getComputedStyle(scroller).overflowY;
      if (oy === 'auto' || oy === 'scroll') break;
      scroller = scroller.parentElement;
    }

    const measure = () => {
      if (!rootRef.current) return;
      const rect = rootRef.current.getBoundingClientRect();
      const padBottom = rootRef.current.parentElement
        ? parseFloat(getComputedStyle(rootRef.current.parentElement).paddingBottom) || 0
        : 0;
      let avail;
      if (scroller && scroller !== document.body) {
        const top = rect.top - scroller.getBoundingClientRect().top + scroller.scrollTop;
        avail = scroller.clientHeight - top;
      } else {
        avail = window.innerHeight - rect.top;
      }
      setHeight(Math.max(360, Math.floor(avail - padBottom)));
    };

    measure();
    window.addEventListener('resize', measure);
    const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(measure) : null;
    if (ro && scroller) ro.observe(scroller);
    return () => {
      window.removeEventListener('resize', measure);
      if (ro) ro.disconnect();
    };
  }, []);

  // Autoscroll the chat feed.
  const feedRef = useRef(null);
  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight;
  }, [feed]);

  // Auto-grow the chat input (same behaviour as the main Chat page).
  const textareaRef = useRef(null);
  const resizeTextarea = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 140) + 'px';
  }, []);

  const TABS = [
    { key: 'process', label: t('flowProjectGraph.tabs.process'), icon: GitBranch },
    { key: 'architecture', label: t('flowProjectGraph.tabs.architecture'), icon: Boxes },
  ];
  const badge = SOURCE_BADGE[source] || SOURCE_BADGE.auto;
  const anyBusy = loading || busy;
  const emptyCanvas = nodes.length === 0 && !loading;

  return (
    <div ref={rootRef} className="bg-white rounded-xl border border-gray-200 overflow-hidden flex flex-col" style={{ height: height || undefined }}>
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 flex-wrap gap-2">
        <div className="flex items-center gap-2">
          <div className="flex gap-1">
            {TABS.map(({ key, label, icon: Icon }) => (
              <button key={key} onClick={() => setView(key)}
                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                  view === key ? 'bg-indigo-50 text-indigo-600' : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'}`}>
                <Icon className="w-4 h-4" /> {label}
              </button>
            ))}
          </div>
          <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium ${badge.cls}`}>{badge.label}</span>
          {dirty && <span className="text-[11px] text-amber-600">{t('flowProjectGraph.unsaved')}</span>}
        </div>

        <div className="flex items-center gap-1.5">
          <button onClick={onGenerate} disabled={anyBusy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium text-violet-600 hover:bg-violet-50 disabled:opacity-50"
            title={t('flowProjectGraph.letTheAgentBuildThe')}>
            <Sparkles className={`w-4 h-4 ${busy ? 'animate-pulse' : ''}`} /> {busy ? 'Building…' : 'Generate'}
          </button>
          <button onClick={addNode} disabled={anyBusy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-40"
            title={t('flowProjectGraph.addANodeManuallyThen')}>
            <PlusCircle className="w-4 h-4" /> {t('flowProjectGraph.addNode')}</button>
          <button onClick={handleSave} disabled={saving || !dirty}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium text-indigo-600 hover:bg-indigo-50 disabled:opacity-40"
            title={t('flowProjectGraph.saveEdits')}><Save className="w-4 h-4" /> {t('flowProjectGraph.save')}</button>
          <button onClick={handleRelayout} disabled={anyBusy || nodes.length === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm text-gray-500 hover:bg-gray-50 disabled:opacity-40"
            title={t('flowProjectGraph.autoArrangeNodesConnectionAware')}><Network className="w-4 h-4" /> {t('flowProjectGraph.arrange')}</button>
          <button onClick={handleReset} disabled={anyBusy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm text-gray-500 hover:bg-gray-50 disabled:opacity-40"
            title={t('flowProjectGraph.clearTheGraphAndChat')}><Undo2 className="w-4 h-4" /> {t('flowProjectGraph.reset')}</button>
          <button onClick={() => load(view)} disabled={anyBusy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm text-gray-500 hover:bg-gray-50 disabled:opacity-50"
            title={t('flowProjectGraph.reload')}><RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /></button>
          {!showChat && (
            <button onClick={() => setShowChat(true)} className="px-3 py-1.5 rounded-md text-sm text-gray-500 hover:bg-gray-50" title={t('flowProjectGraph.showChat')}>
              <PanelRightOpen className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {error ? <div className="px-4 py-3 text-sm text-red-600 bg-red-50 border-b border-red-100">{error}</div> : null}

      <div className="flex-1 min-h-0 flex">
        <div className="flex-1 min-w-0 flex flex-col relative">
          <div ref={canvasWrapRef} className="flex-1 min-h-0 relative">
            {emptyCanvas && (
              <div className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none">
                <div className="text-center text-gray-400">
                  <GitBranch className="w-8 h-8 mx-auto mb-2 opacity-40" />
                  <div className="text-sm font-medium">{t('flowProjectGraph.noDataYet')}</div>
                  <div className="text-xs mt-1 max-w-xs">{t('flowProjectGraph.describeYourProjectInThe')} <span className="text-violet-500 font-medium">{t('flowProjectGraph.generate')}</span>{t('flowProjectGraph.or')} <span className="text-gray-600 font-medium">{t('flowProjectGraph.addNode')}</span> to build the {view} graph by hand.</div>
                </div>
              </div>
            )}
            <ReactFlow
              nodes={styledNodes} edges={edges}
              onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
              onConnect={onConnect} onInit={(inst) => { rfRef.current = inst; }}
              onNodeClick={onNodeClick} onEdgeClick={onEdgeClick} onPaneClick={onPaneClick}
              deleteKeyCode={['Backspace', 'Delete']}
              fitView fitViewOptions={{ padding: 0.2 }} minZoom={0.2}
              proOptions={{ hideAttribution: true }}>
              <Background gap={16} color="#eef2f7" />
              <Controls showInteractive={false} />
              <MiniMap pannable zoomable nodeStrokeWidth={2} />
            </ReactFlow>
            <GraphInspector
              sel={sel}
              node={sel?.kind === 'node' ? nodes.find((n) => n.id === sel.id) : null}
              edge={sel?.kind === 'edge' ? edges.find((e) => e.id === sel.id) : null}
              nodes={nodes}
              onPatchNode={patchNode}
              onPatchEdge={patchEdge}
              onDelete={deleteSelected}
              onClose={() => setSel(null)}
            />
          </div>
          <div className="px-4 py-2 text-xs text-gray-400 border-t border-gray-100">
            {view === 'process'
              ? 'Business/process flow. Chat to build it, or edit by hand: Add node, drag a node’s handle to connect, click to edit, Delete to remove. Save to keep your edits.'
              : 'Technical structure. Chat to build it, or edit by hand: Add node, drag a node’s handle to connect, click to edit, Delete to remove. Save to keep your edits.'}
          </div>
        </div>

        {showChat && (
          <div className="w-[28rem] shrink-0 border-l border-gray-200 flex flex-col bg-white">
            <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100">
              <div className="flex items-center gap-1.5 text-sm font-medium text-gray-700">
                <MessageSquare className={`w-4 h-4 ${busy ? 'text-violet-500 animate-pulse' : 'text-gray-400'}`} /> {t('flowProjectGraph.architectChat')}
              </div>
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-1.5 text-xs text-gray-500 cursor-pointer select-none"
                  title={t('flowProjectGraph.showOrHideTheAgent')}>
                  <input type="checkbox" checked={showThinking}
                    onChange={(e) => setShowThinking(e.target.checked)}
                    className="w-3.5 h-3.5 accent-indigo-600 cursor-pointer" />
                  <Brain className="w-3.5 h-3.5 text-amber-400" /> {t('flowProjectGraph.thinking')}
                </label>
                <button onClick={clearChat} disabled={busy}
                  className="flex items-center gap-1 text-xs text-gray-500 hover:text-gray-700 hover:bg-gray-50 px-2 py-1 rounded-md disabled:opacity-40"
                  title={t('flowProjectGraph.clearTheConversationAndStart')}>
                  <Trash2 className="w-3.5 h-3.5" /> {t('flowProjectGraph.clearChat')}
                </button>
                <button onClick={() => setShowChat(false)} className="text-gray-400 hover:text-gray-600" title={t('flowProjectGraph.hideChat')}>
                  <PanelRightClose className="w-4 h-4" />
                </button>
              </div>
            </div>

            <div ref={feedRef} className="flex-1 min-h-0 overflow-y-auto px-3 py-2 space-y-2">
              {feed.length === 0 && !busy ? (
                <div className="text-xs text-gray-400 mt-2">
                  Describe the project or the flow you want — e.g. <span className="italic">{t('flowProjectGraph.customerSubmitsAnOrderPayment')}</span> The agent adds each node to the canvas live as you go.
                </div>
              ) : null}
              {feed.map((e, i) => (
                (showThinking || e.k !== 'thinking') ? <FeedItem key={i} e={e} /> : null
              ))}
              {busy ? <div className="text-[11px] text-violet-400 animate-pulse">{stopping ? 'stopping…' : 'working…'}</div> : null}
            </div>

            <div className="border-t border-gray-100 p-3">
              <ContextMeter usage={contextUsage} onClear={busy ? null : clearChat} className="mb-2" />
              <div className="flex items-center gap-3 bg-white border border-gray-300 rounded-2xl px-4 py-2.5
                focus-within:border-indigo-400 focus-within:ring-2 focus-within:ring-indigo-100 shadow-sm transition-all">
                <textarea
                  ref={textareaRef}
                  rows={1}
                  value={input}
                  disabled={busy}
                  onChange={(e) => { setInput(e.target.value); resizeTextarea(); }}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); } }}
                  placeholder={`Message the architect… (${view})`}
                  className="flex-1 resize-none text-sm text-gray-800 placeholder-gray-400 focus:outline-none bg-transparent leading-relaxed disabled:opacity-50"
                />
                {busy ? (
                  <button onClick={stopChat} disabled={stopping}
                    title={t('flowProjectGraph.stopTheCurrentRun')}
                    className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors">
                    <Square className="w-3 h-3" fill="currentColor" />
                  </button>
                ) : (
                  <button onClick={onSend} disabled={!input.trim()}
                    title={t('flowProjectGraph.sendEnter')}
                    className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
                    <Send className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
              <p className="text-center text-[11px] text-gray-400 mt-1.5">
                {busy ? (stopping ? 'Stopping…' : 'Press ■ to stop the run') : 'Enter to send · Shift+Enter for new line'}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default ProjectGraph;
