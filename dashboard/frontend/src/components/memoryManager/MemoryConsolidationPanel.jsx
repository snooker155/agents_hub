/**
 * Memory consolidation ("dreams", fifth-cycle stage 2): fold a pool's own
 * content and a handful of its recent sessions into a NEW pool, reviewed
 * here next to the source as a diff, before a person switches an agent's
 * binding to it or discards it. The source pool is never changed by this
 * panel; every action goes through memory/consolidation.py by way of
 * dashboard/backend/routes/memory_consolidation.py.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { Database, Loader, Play, RotateCcw, Trash2, X } from 'lucide-react';
import { getAgents } from '../../api';
import {
  applyMemoryConsolidation, discardMemoryConsolidation, getMemoryConsolidation,
  listMemoryConsolidations, startMemoryConsolidation,
} from '../../api/memoryConsolidation';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';
import { lineDiff } from '../../lib/lineDiff';

const STATUS_BADGE = {
  queued: 'bg-gray-100 text-gray-600',
  running: 'bg-blue-100 text-blue-700',
  done: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
  discarded: 'bg-gray-100 text-gray-400',
};

const OP_BADGE = {
  added: 'bg-emerald-100 text-emerald-700',
  changed: 'bg-blue-100 text-blue-700',
  removed: 'bg-red-100 text-red-700',
};

function toText(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function DiffRow({ row }) {
  if (row.op === 'changed') {
    const rows = lineDiff(toText(row.before), toText(row.after));
    return (
      <div className="rounded-lg border border-gray-200 p-2">
        <div className="flex items-center gap-2 mb-1">
          <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${OP_BADGE.changed}`}>changed</span>
          <span className="text-xs font-medium text-gray-700 truncate">{row.key}</span>
        </div>
        <pre className="text-xs font-mono whitespace-pre-wrap">
          {rows.map((r, i) => (
            <div key={i} className={r.type === 'added' ? 'bg-emerald-50 text-emerald-800' : r.type === 'removed' ? 'bg-red-50 text-red-800 line-through' : 'text-gray-500'}>
              {r.text || ' '}
            </div>
          ))}
        </pre>
      </div>
    );
  }
  const badge = OP_BADGE[row.op] || 'bg-gray-100 text-gray-600';
  const text = toText(row.op === 'removed' ? row.before : row.after);
  return (
    <div className="rounded-lg border border-gray-200 p-2">
      <div className="flex items-center gap-2 mb-1">
        <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${badge}`}>{row.op}</span>
        <span className="text-xs font-medium text-gray-700 truncate">{row.key}</span>
      </div>
      <pre className="text-xs font-mono whitespace-pre-wrap text-gray-600">{text}</pre>
    </div>
  );
}

function DiffSection({ title, rows }) {
  if (!rows || rows.length === 0) return null;
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-semibold text-gray-500 uppercase">{title}</div>
      {rows.map((row, i) => <DiffRow key={`${row.key}-${i}`} row={row} />)}
    </div>
  );
}

function MemoryConsolidationPanel({ poolId, onClose, onApplied }) {
  const { t } = useI18n();
  const toast = useToast();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [sessionLimit, setSessionLimit] = useState(10);
  const [selected, setSelected] = useState(null);
  const [agents, setAgents] = useState([]);
  const [applyAgentId, setApplyAgentId] = useState('');
  const [busy, setBusy] = useState(false);
  const pollRef = useRef(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const resp = await listMemoryConsolidations(poolId, { limit: 20 });
      const items = resp.data.consolidations || [];
      setRows(items);
      if (!selected && items[0]) setSelected(items[0].id);
    } catch (e) {
      toast.error(t('memoryConsolidation.errors.load'), errorDetail(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [poolId, toast, t]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    getAgents().then(({ data }) => setAgents(Array.isArray(data) ? data : (data?.items || []))).catch(() => setAgents([]));
  }, []);

  // Poll while the selected job (or the newest one) is still in flight.
  useEffect(() => {
    const active = rows.find(r => r.id === selected) || rows[0];
    if (!active || (active.status !== 'queued' && active.status !== 'running')) {
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      return undefined;
    }
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await getMemoryConsolidation(active.id);
        setRows(prev => prev.map(r => (r.id === data.id ? data : r)));
      } catch {
        // transient; the next tick retries
      }
    }, 3000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [rows, selected]);

  const handleStart = async () => {
    setStarting(true);
    try {
      const { data } = await startMemoryConsolidation(poolId, { session_limit: Number(sessionLimit) || 10 });
      setRows(prev => [data, ...prev]);
      setSelected(data.id);
    } catch (e) {
      toast.error(t('memoryConsolidation.errors.start'), errorDetail(e));
    } finally {
      setStarting(false);
    }
  };

  const handleApply = async (jobId) => {
    if (!applyAgentId) return;
    setBusy(true);
    try {
      await applyMemoryConsolidation(jobId, { agent_id: applyAgentId });
      toast.success(t('memoryConsolidation.applied'));
      onApplied?.();
      load();
    } catch (e) {
      toast.error(t('memoryConsolidation.errors.apply'), errorDetail(e));
    } finally {
      setBusy(false);
    }
  };

  const handleDiscard = async (jobId) => {
    setBusy(true);
    try {
      await discardMemoryConsolidation(jobId);
      load();
    } catch (e) {
      toast.error(t('memoryConsolidation.errors.discard'), errorDetail(e));
    } finally {
      setBusy(false);
    }
  };

  const activeRow = rows.find(r => r.id === selected) || rows[0] || null;
  const diff = activeRow?.diff || null;

  return (
    <div className="fixed inset-0 bg-black/40 flex items-stretch justify-end z-50">
    <div className="bg-white w-full max-w-lg h-full shadow-xl flex flex-col">
    <div className="p-4 space-y-4 overflow-y-auto">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
          <Database className="w-4 h-4 text-indigo-500" /> {t('memoryConsolidation.title')}
        </h3>
        {onClose && (
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-4 h-4" />
          </button>
        )}
      </div>
      <p className="text-xs text-gray-400">{t('memoryConsolidation.hint')}</p>

      <div className="flex items-center gap-2">
        <input
          type="number" min="1" max="50" value={sessionLimit}
          onChange={(e) => setSessionLimit(e.target.value)}
          className="w-20 border border-gray-300 rounded-lg px-2 py-1.5 text-sm"
        />
        <span className="text-xs text-gray-500">{t('memoryConsolidation.sessionsLabel')}</span>
        <button
          type="button" onClick={handleStart} disabled={starting}
          className="ml-auto bg-indigo-600 text-white px-3 py-1.5 rounded-lg text-xs font-medium hover:bg-indigo-700 flex items-center gap-1.5 disabled:opacity-50"
        >
          {starting ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
          {t('memoryConsolidation.runNow')}
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-6 text-gray-400 text-sm">
          <Loader className="w-4 h-4 animate-spin mr-2" /> {t('common.loading')}
        </div>
      ) : rows.length === 0 ? (
        <p className="text-xs text-gray-400 italic">{t('memoryConsolidation.none')}</p>
      ) : (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-1.5">
            {rows.map(r => (
              <button
                key={r.id} type="button" onClick={() => setSelected(r.id)}
                className={`px-2 py-1 rounded-lg text-xs border ${selected === r.id ? 'border-indigo-400 bg-indigo-50' : 'border-gray-200'}`}
              >
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold mr-1.5 ${STATUS_BADGE[r.status] || 'bg-gray-100'}`}>
                  {t(`memoryConsolidation.status.${r.status}`)}
                </span>
                {new Date(r.created_at).toLocaleString()}
              </button>
            ))}
          </div>

          {activeRow && (
            <div className="border-t border-gray-100 pt-3 space-y-3">
              {activeRow.status === 'failed' && (
                <p className="text-xs text-red-600">{activeRow.error}</p>
              )}
              {(activeRow.status === 'queued' || activeRow.status === 'running') && (
                <p className="text-xs text-gray-400 flex items-center gap-1.5"><Loader className="w-3.5 h-3.5 animate-spin" /> {t(`memoryConsolidation.status.${activeRow.status}`)}</p>
              )}
              {activeRow.status === 'done' && (
                <>
                  {activeRow.summary && <p className="text-xs text-gray-600 italic">{activeRow.summary}</p>}
                  {diff && (
                    <div className="space-y-3 max-h-80 overflow-y-auto">
                      <DiffSection title={t('memoryConsolidation.blocks')} rows={diff.blocks} />
                      <DiffSection title={t('memoryConsolidation.notes')} rows={diff.notes} />
                      <DiffSection title={t('memoryConsolidation.slots')} rows={diff.slots} />
                      {!diff.blocks?.length && !diff.notes?.length && !diff.slots?.length && (
                        <p className="text-xs text-gray-400 italic">{t('memoryConsolidation.noChanges')}</p>
                      )}
                    </div>
                  )}
                  <div className="flex items-center gap-2 pt-2 border-t border-gray-100">
                    <select
                      value={applyAgentId} onChange={(e) => setApplyAgentId(e.target.value)}
                      className="flex-1 border border-gray-300 rounded-lg px-2 py-1.5 text-xs"
                    >
                      <option value="">{t('memoryConsolidation.pickAgent')}</option>
                      {agents.map(a => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
                    </select>
                    <button
                      type="button" onClick={() => handleApply(activeRow.id)} disabled={busy || !applyAgentId}
                      className="bg-indigo-600 text-white px-2.5 py-1.5 rounded-lg text-xs font-medium hover:bg-indigo-700 flex items-center gap-1.5 disabled:opacity-50"
                    >
                      <RotateCcw className="w-3.5 h-3.5" /> {t('memoryConsolidation.switchBinding')}
                    </button>
                    <button
                      type="button" onClick={() => handleDiscard(activeRow.id)} disabled={busy}
                      className="bg-red-50 text-red-600 px-2.5 py-1.5 rounded-lg text-xs font-medium hover:bg-red-100 flex items-center gap-1.5 disabled:opacity-50 border border-red-200"
                    >
                      <Trash2 className="w-3.5 h-3.5" /> {t('memoryConsolidation.discard')}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
    </div>
    </div>
  );
}

export { MemoryConsolidationPanel };
