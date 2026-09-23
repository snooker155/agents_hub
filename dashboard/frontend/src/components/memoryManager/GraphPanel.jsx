/**
 * Graph panel — knowledge graph scoped to a pool, as a node/edge list or a
 * force-directed visual layout (GraphVisual, below).
 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Plus, Trash2, X, RefreshCw, Sparkles, GitMerge, Eraser,
} from 'lucide-react';
import {
  getMemoryGraph, linkMemoryGraph, deleteMemoryGraphNode, deleteMemoryGraphEdge,
  extractMemoryGraph, mergeMemoryGraphSlots, pruneMemoryGraphMirrors,
} from '../../api';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';
import { colorForType } from './helpers';

function GraphPanel({ poolId, stats, onChange }) {
  const { t } = useI18n();
  const toast = useToast();
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [loading, setLoading] = useState(false);
  const [view, setView] = useState('list'); // 'list' | 'visual'
  const [showAdd, setShowAdd] = useState(false);
  const [showExtract, setShowExtract] = useState(false);
  const [extractText, setExtractText] = useState('');
  const [extractRunning, setExtractRunning] = useState(false);
  const [extractResult, setExtractResult] = useState(null);
  const [mergeResult, setMergeResult] = useState(null);
  const [mergeRunning, setMergeRunning] = useState(false);
  const [orphans, setOrphans] = useState([]); // dry-run preview of prunable mirrors
  const [pruneResult, setPruneResult] = useState(null);
  const [pruneRunning, setPruneRunning] = useState(false);

  // Add-edge form state
  const [srcType, setSrcType] = useState('');
  const [srcName, setSrcName] = useState('');
  const [tgtType, setTgtType] = useState('');
  const [tgtName, setTgtName] = useState('');
  const [relation, setRelation] = useState('');

  const load = useCallback(async () => {
    if (!poolId) return;
    setLoading(true);
    try {
      const r = await getMemoryGraph(poolId);
      setNodes(r.data?.nodes || []);
      setEdges(r.data?.edges || []);
    } catch {
      setNodes([]); setEdges([]);
    } finally {
      setLoading(false);
    }
    // Preview orphaned slot/note mirror nodes (no backing slot/note) so the
    // prune button can show a count and stay hidden when there's nothing to do.
    try {
      const p = await pruneMemoryGraphMirrors(poolId, { dryRun: true });
      setOrphans(p.data?.removed || []);
    } catch {
      setOrphans([]);
    }
  }, [poolId]);

  useEffect(() => { load(); }, [load]);

  const handleAddEdge = async (e) => {
    e.preventDefault();
    try {
      await linkMemoryGraph(poolId, {
        source: { type: srcType.trim(), name: srcName.trim() },
        target: { type: tgtType.trim(), name: tgtName.trim() },
        relation: relation.trim(),
      });
      setSrcType(''); setSrcName(''); setTgtType(''); setTgtName(''); setRelation('');
      setShowAdd(false);
      await load();
      onChange?.();
    } catch (err) {
      alert(err?.response?.data?.detail || t('memoryManager.errors.addEdge'));
    }
  };

  const handleDeleteNode = async (nodeId) => {
    if (!window.confirm(t('memoryManager.confirmDeleteNode'))) return;
    try {
      await deleteMemoryGraphNode(poolId, nodeId);
      await load();
      onChange?.();
    } catch (e) {
      toast.error(t('memoryManager.errors.deleteNode'), errorDetail(e));
    }
  };

  const handleDeleteEdge = async (edgeId) => {
    if (!window.confirm(t('memoryManager.confirmDeleteEdge'))) return;
    try {
      await deleteMemoryGraphEdge(poolId, edgeId);
      await load();
      onChange?.();
    } catch (e) {
      toast.error(t('memoryManager.errors.deleteEdge'), errorDetail(e));
    }
  };

  const handleExtract = async (e) => {
    e.preventDefault();
    if (!extractText.trim()) return;
    setExtractRunning(true);
    setExtractResult(null);
    try {
      const r = await extractMemoryGraph(poolId, extractText.trim());
      setExtractResult(r.data);
      await load();
      onChange?.();
    } catch (err) {
      setExtractResult({ ok: false, errors: [err?.response?.data?.detail || t('memoryManager.errors.extraction')] });
    } finally {
      setExtractRunning(false);
    }
  };

  const allTypes = Array.from(new Set(nodes.map(n => n.type)));

  // Slot mirror nodes that have a same-name typed entity twin (separator-
  // insensitive, mirroring the backend's matching) — candidates for auto-merge.
  const slotTwinPairs = useMemo(() => {
    const canon = (s) => String(s || '').toLowerCase().replace(/[_-]/g, ' ').replace(/\s+/g, ' ').trim();
    return nodes
      .filter(n => n.type === 'slot')
      .map(sn => ({ slot: sn, twin: nodes.find(n => n.type !== 'slot' && canon(n.name) === canon(sn.name)) }))
      .filter(p => p.twin);
  }, [nodes]);

  const handleMergeSlots = async () => {
    const lines = slotTwinPairs
      .map(pair => `• ${t('memoryManager.slotLabel')} "${pair.slot.name}"  →  ${pair.twin.type} "${pair.twin.name}"`)
      .join('\n');
    const ok = window.confirm(
      `${t('memoryManager.confirmMerge', { count: slotTwinPairs.length })}\n\n${lines}\n\n${t('memoryManager.mergeHint')}`
    );
    if (!ok) return;
    setMergeRunning(true);
    setMergeResult(null);
    try {
      const r = await mergeMemoryGraphSlots(poolId);
      setMergeResult(r.data);
      await load();
      onChange?.();
    } catch (err) {
      setMergeResult({ merged: [], errors: [err?.response?.data?.detail || t('memoryManager.errors.merge')] });
    } finally {
      setMergeRunning(false);
    }
  };

  const handlePrune = async () => {
    const lines = orphans.map(o => `• ${o.type} "${o.name}"`).join('\n');
    const ok = window.confirm(
      `Remove ${orphans.length} orphaned graph node${orphans.length === 1 ? '' : 's'} whose backing slot/note no longer exists?\n\n${lines}\n\nTyped entity nodes are not affected.`
    );
    if (!ok) return;
    setPruneRunning(true);
    setPruneResult(null);
    try {
      const r = await pruneMemoryGraphMirrors(poolId, { dryRun: false });
      setPruneResult(r.data);
      await load();
      onChange?.();
    } catch (err) {
      setPruneResult({ removed: [], errors: [err?.response?.data?.detail || t('memoryManager.errors.prune')] });
    } finally {
      setPruneRunning(false);
    }
  };

  return (
    <div className="flex-1 overflow-y-auto p-4 space-y-3">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="text-xs text-gray-500">
          {t('memoryManager.nodesAndEdges', { nodes: nodes.length, edges: edges.length })}
          {stats?.node_cap && ` · ${t('memoryManager.capPair', { nodes: stats.node_cap, edges: stats.edge_cap })}`}
        </div>
        <div className="flex items-center gap-2">
          <div className="flex border border-gray-200 rounded overflow-hidden">
            <button onClick={() => setView('list')}
              className={`text-xs px-2 py-1 ${view === 'list' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}>
              {t('memoryManager.list')}
            </button>
            <button onClick={() => setView('visual')}
              className={`text-xs px-2 py-1 ${view === 'visual' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'}`}>
              {t('memoryManager.visualize')}
            </button>
          </div>
          {slotTwinPairs.length > 0 && (
            <button onClick={handleMergeSlots} disabled={mergeRunning}
              title={t('memoryManager.slotMirrorNodesWithA')}
              className="text-xs flex items-center gap-1 border border-emerald-200 text-emerald-700 px-2 py-1 rounded hover:bg-emerald-50 disabled:opacity-50">
              <GitMerge className={`w-3 h-3 ${mergeRunning ? 'animate-pulse' : ''}`} />
              Merge {slotTwinPairs.length} slot dup{slotTwinPairs.length === 1 ? '' : 's'}
            </button>
          )}
          {orphans.length > 0 && (
            <button onClick={handlePrune} disabled={pruneRunning}
              title={t('memoryManager.slotNoteMirrorNodesWhose')}
              className="text-xs flex items-center gap-1 border border-amber-200 text-amber-700 px-2 py-1 rounded hover:bg-amber-50 disabled:opacity-50">
              <Eraser className={`w-3 h-3 ${pruneRunning ? 'animate-pulse' : ''}`} />
              Prune {orphans.length} orphan{orphans.length === 1 ? '' : 's'}
            </button>
          )}
          <button onClick={() => setShowExtract(s => !s)}
            className="text-xs flex items-center gap-1 border border-purple-200 text-purple-700 px-2 py-1 rounded hover:bg-purple-50">
            <Sparkles className="w-3 h-3" /> {t('memoryManager.extract')}
          </button>
          <button onClick={() => setShowAdd(s => !s)}
            className="text-xs flex items-center gap-1 border border-indigo-200 text-indigo-600 px-2 py-1 rounded hover:bg-indigo-50">
            <Plus className="w-3 h-3" /> {t('memoryManager.edge')}
          </button>
          <button onClick={load} className="text-xs text-indigo-600 hover:text-indigo-800 flex items-center gap-1">
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> {t('memoryManager.refresh')}
          </button>
        </div>
      </div>

      {/* Merge result banner */}
      {mergeResult && (
        <div className={`text-xs border rounded-lg p-2.5 flex items-start gap-2 ${(mergeResult.errors || []).length ? 'border-red-200 bg-red-50/50' : 'border-emerald-200 bg-emerald-50/50'}`}>
          <GitMerge className={`w-3.5 h-3.5 mt-0.5 flex-shrink-0 ${(mergeResult.errors || []).length ? 'text-red-500' : 'text-emerald-600'}`} />
          <div className="flex-1 space-y-0.5">
            {(mergeResult.merged || []).length > 0 ? (
              <div className="text-emerald-800">
                Merged {mergeResult.merged.length} slot node{mergeResult.merged.length === 1 ? '' : 's'}:{' '}
                {mergeResult.merged.map(m => `${m.name} → ${m.into_type}`).join(', ')}
              </div>
            ) : (mergeResult.errors || []).length === 0 && (
              <div className="text-gray-600">{t('memoryManager.noMergeableSlotDuplicatesFound')}</div>
            )}
            {(mergeResult.errors || []).map((e, i) => (
              <div key={i} className="text-red-600">{e}</div>
            ))}
          </div>
          <button onClick={() => setMergeResult(null)} className="text-gray-400 hover:text-gray-600">
            <X className="w-3 h-3" />
          </button>
        </div>
      )}

      {pruneResult && (
        <div className={`text-xs border rounded-lg p-2.5 flex items-start gap-2 ${(pruneResult.errors || []).length ? 'border-red-200 bg-red-50/50' : 'border-amber-200 bg-amber-50/50'}`}>
          <Eraser className={`w-3.5 h-3.5 mt-0.5 flex-shrink-0 ${(pruneResult.errors || []).length ? 'text-red-500' : 'text-amber-600'}`} />
          <div className="flex-1 space-y-0.5">
            {(pruneResult.removed || []).length > 0 ? (
              <div className="text-amber-800">
                Removed {pruneResult.removed.length} orphaned node{pruneResult.removed.length === 1 ? '' : 's'}:{' '}
                {pruneResult.removed.map(o => `${o.type} "${o.name}"`).join(', ')}
              </div>
            ) : (pruneResult.errors || []).length === 0 && (
              <div className="text-gray-600">{t('memoryManager.noOrphanedMirrorNodesFound')}</div>
            )}
            {(pruneResult.errors || []).map((e, i) => (
              <div key={i} className="text-red-600">{e}</div>
            ))}
          </div>
          <button onClick={() => setPruneResult(null)} className="text-gray-400 hover:text-gray-600">
            <X className="w-3 h-3" />
          </button>
        </div>
      )}

      {/* Add-edge form */}
      {showAdd && (
        <form onSubmit={handleAddEdge} className="border border-indigo-100 rounded-lg p-3 bg-indigo-50/30 space-y-2">
          <div className="grid grid-cols-1 md:grid-cols-5 gap-2">
            <input value={srcType} onChange={e => setSrcType(e.target.value)} placeholder={t('memoryManager.sourceType')} required
              className="text-xs border border-gray-200 rounded px-2 py-1" />
            <input value={srcName} onChange={e => setSrcName(e.target.value)} placeholder={t('memoryManager.sourceName')} required
              className="text-xs border border-gray-200 rounded px-2 py-1" />
            <input value={relation} onChange={e => setRelation(e.target.value)} placeholder={t('memoryManager.relation')} required
              className="text-xs border border-gray-200 rounded px-2 py-1" />
            <input value={tgtType} onChange={e => setTgtType(e.target.value)} placeholder={t('memoryManager.targetType')} required
              className="text-xs border border-gray-200 rounded px-2 py-1" />
            <input value={tgtName} onChange={e => setTgtName(e.target.value)} placeholder={t('memoryManager.targetName')} required
              className="text-xs border border-gray-200 rounded px-2 py-1" />
          </div>
          <div className="flex gap-2">
            <button type="submit" className="text-xs bg-indigo-600 text-white px-3 py-1 rounded hover:bg-indigo-700">{t('memoryManager.add')}</button>
            <button type="button" onClick={() => setShowAdd(false)} className="text-xs border border-gray-200 px-3 py-1 rounded">{t('memoryManager.cancel')}</button>
          </div>
        </form>
      )}

      {/* Auto-extract form */}
      {showExtract && (
        <form onSubmit={handleExtract} className="border border-purple-100 rounded-lg p-3 bg-purple-50/30 space-y-2">
          <p className="text-xs text-purple-800">
            {t('memoryManager.pasteProseAndTheLlm')}
          </p>
          <textarea value={extractText} onChange={e => setExtractText(e.target.value)} rows={4}
            placeholder={t('memoryManager.eGAliceOwnsThe')}
            className="w-full text-xs border border-gray-200 rounded px-2 py-1.5 font-mono" />
          <div className="flex items-center gap-2">
            <button type="submit" disabled={extractRunning || !extractText.trim()}
              className="text-xs bg-purple-600 text-white px-3 py-1 rounded hover:bg-purple-700 disabled:opacity-50 flex items-center gap-1">
              <Sparkles className="w-3 h-3" /> {extractRunning ? t('memoryManager.extracting') : t('memoryManager.runExtraction')}
            </button>
            <button type="button" onClick={() => { setShowExtract(false); setExtractResult(null); }}
              className="text-xs border border-gray-200 px-3 py-1 rounded">{t('memoryManager.close')}</button>
          </div>
          {extractResult && (
            <div className="text-xs text-gray-700 bg-white border border-gray-200 rounded p-2">
              {extractResult.ok === false
                ? <span className="text-red-600">{t('common.failed')}: {(extractResult.errors || []).join('; ') || t('memoryManager.unknownError')}</span>
                : <span>{t('memoryManager.triplesResult', { found: extractResult.triples_found || 0, persisted: extractResult.triples_persisted || 0 })}</span>}
            </div>
          )}
        </form>
      )}

      {nodes.length === 0 && edges.length === 0 ? (
        <p className="p-6 text-center text-gray-400 text-sm italic">
          {loading ? t('common.loading') : t('memoryManager.graphEmpty')}
        </p>
      ) : view === 'visual' ? (
        <GraphVisual nodes={nodes} edges={edges} allTypes={allTypes} />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div className="border border-gray-200 rounded-lg overflow-hidden">
            <div className="px-3 py-2 bg-gray-50 border-b border-gray-100 text-xs font-semibold text-gray-700">
              Nodes ({nodes.length})
            </div>
            <div className="divide-y divide-gray-50 max-h-96 overflow-y-auto">
              {nodes.map(n => {
                const [fg, bg] = colorForType(n.type, allTypes);
                return (
                  <div key={n.id} className="flex items-start justify-between gap-2 px-3 py-2 text-xs">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="px-1.5 py-0.5 rounded font-medium" style={{ color: fg, background: bg }}>{n.type}</span>
                        <span className="font-mono text-gray-800 truncate">{n.name}</span>
                      </div>
                      {n.properties && Object.keys(n.properties).length > 0 && (
                        <details className="mt-1">
                          <summary className="text-[10px] text-gray-400 cursor-pointer hover:text-gray-600">{t('memoryManager.properties')}</summary>
                          <pre className="text-[10px] bg-gray-50 rounded p-1 mt-1 overflow-x-auto">{JSON.stringify(n.properties, null, 2)}</pre>
                        </details>
                      )}
                    </div>
                    <button onClick={() => handleDeleteNode(n.id)} className="text-gray-300 hover:text-red-500 p-0.5 shrink-0">
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="border border-gray-200 rounded-lg overflow-hidden">
            <div className="px-3 py-2 bg-gray-50 border-b border-gray-100 text-xs font-semibold text-gray-700">
              Edges ({edges.length})
            </div>
            <div className="divide-y divide-gray-50 max-h-96 overflow-y-auto">
              {edges.map(e => {
                const src = nodes.find(n => n.id === e.source_id);
                const tgt = nodes.find(n => n.id === e.target_id);
                if (!src || !tgt) return null;
                return (
                  <div key={e.id} className="flex items-center justify-between gap-2 px-3 py-2 text-xs">
                    <span className="font-mono text-gray-700 truncate flex-1">
                      <span className="text-gray-500">{src.type}:</span>{src.name}
                      <span className="mx-1 text-indigo-500">─[{e.relation}]→</span>
                      <span className="text-gray-500">{tgt.type}:</span>{tgt.name}
                    </span>
                    <button onClick={() => handleDeleteEdge(e.id)} className="text-gray-300 hover:text-red-500 p-0.5 shrink-0">
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dependency-free force-directed SVG layout. Tiny iterative spring sim that
// converges in ~120 ticks for graphs under ~150 nodes — good enough for this UI.
// ---------------------------------------------------------------------------
function GraphVisual({ nodes, edges, allTypes }) {
  const W = 720, H = 480;
  const positions = useMemo(() => {
    if (nodes.length === 0) return new Map();
    const pos = new Map();
    nodes.forEach((n, i) => {
      const angle = (i / nodes.length) * Math.PI * 2;
      pos.set(n.id, {
        x: W / 2 + Math.cos(angle) * (Math.min(W, H) / 3),
        y: H / 2 + Math.sin(angle) * (Math.min(W, H) / 3),
        vx: 0, vy: 0,
      });
    });
    const k = Math.sqrt((W * H) / Math.max(nodes.length, 1)) * 0.6;
    const iterations = 140;
    // Deterministic jitter so re-renders produce the same layout.
    const jitter = (i, j) => (Math.sin(i * 12.9898 + j * 78.233) * 43758.5453) % 1;
    for (let it = 0; it < iterations; it++) {
      const t = 1 - it / iterations;
      // Repulsion (every pair).
      for (let i = 0; i < nodes.length; i++) {
        const a = pos.get(nodes[i].id);
        for (let j = i + 1; j < nodes.length; j++) {
          const b = pos.get(nodes[j].id);
          let dx = a.x - b.x, dy = a.y - b.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 0.01) { dx = jitter(i, j) * 0.1; dy = jitter(j, i) * 0.1; d2 = 0.02; }
          const force = (k * k) / d2;
          const d = Math.sqrt(d2);
          const fx = (dx / d) * force, fy = (dy / d) * force;
          a.vx += fx; a.vy += fy;
          b.vx -= fx; b.vy -= fy;
        }
      }
      // Attraction along edges.
      for (const e of edges) {
        const a = pos.get(e.source_id), b = pos.get(e.target_id);
        if (!a || !b) continue;
        const dx = a.x - b.x, dy = a.y - b.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const force = (d * d) / k;
        const fx = (dx / d) * force, fy = (dy / d) * force;
        a.vx -= fx; a.vy -= fy;
        b.vx += fx; b.vy += fy;
      }
      // Apply with cooling damping.
      for (const n of nodes) {
        const p = pos.get(n.id);
        const speed = Math.sqrt(p.vx * p.vx + p.vy * p.vy) || 0.001;
        const cap = Math.min(speed, 30 * t);
        p.x += (p.vx / speed) * cap;
        p.y += (p.vy / speed) * cap;
        p.vx *= 0.85; p.vy *= 0.85;
        // Keep inside the viewport with margin.
        p.x = Math.max(40, Math.min(W - 40, p.x));
        p.y = Math.max(30, Math.min(H - 30, p.y));
      }
    }
    return pos;
  }, [nodes, edges]);

  return (
    <div className="border border-gray-200 rounded-lg bg-white overflow-hidden">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minHeight: 360 }}>
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
          </marker>
        </defs>
        {edges.map(e => {
          const a = positions.get(e.source_id), b = positions.get(e.target_id);
          if (!a || !b) return null;
          const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
          return (
            <g key={e.id}>
              <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#cbd5e1" strokeWidth="1" markerEnd="url(#arrow)" />
              <text x={mx} y={my - 3} fontSize="9" fill="#64748b" textAnchor="middle" pointerEvents="none">{e.relation}</text>
            </g>
          );
        })}
        {nodes.map(n => {
          const p = positions.get(n.id);
          if (!p) return null;
          const [fg, bg] = colorForType(n.type, allTypes);
          return (
            <g key={n.id}>
              <circle cx={p.x} cy={p.y} r="14" fill={bg} stroke={fg} strokeWidth="1.5" />
              <text x={p.x} y={p.y + 4} fontSize="9" fill={fg} textAnchor="middle" fontWeight="600" pointerEvents="none">
                {n.name.length > 14 ? n.name.slice(0, 13) + '…' : n.name}
              </text>
              <title>{n.type}: {n.name}</title>
            </g>
          );
        })}
      </svg>
      <div className="px-3 py-2 border-t border-gray-100 bg-gray-50 flex flex-wrap gap-2">
        {allTypes.map(t => {
          const [fg, bg] = colorForType(t, allTypes);
          return (
            <span key={t} className="text-[10px] font-medium px-1.5 py-0.5 rounded" style={{ color: fg, background: bg }}>
              {t}
            </span>
          );
        })}
      </div>
    </div>
  );
}

export { GraphPanel, GraphVisual };
