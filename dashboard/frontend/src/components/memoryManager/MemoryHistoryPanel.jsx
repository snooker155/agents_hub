/**
 * The version history of one item in a memory pool (a block, a note, a slot),
 * or of the whole pool when no item is given: every change, who made it, a
 * line diff against the previous version of that same item, and the two
 * actions a person takes on a past row: put it back, or scrub its content.
 */
import { useCallback, useEffect, useState } from 'react';
import { ChevronDown, ChevronUp, EyeOff, History, RotateCcw, X } from 'lucide-react';
import { listMemoryVersions, redactMemoryVersion, restoreMemoryVersion } from '../../api/memoryVersions';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';
import { lineDiff } from '../../lib/lineDiff';

const OP_BADGE = {
  create: 'bg-emerald-100 text-emerald-700',
  update: 'bg-blue-100 text-blue-700',
  delete: 'bg-red-100 text-red-700',
  restore: 'bg-amber-100 text-amber-700',
  redact: 'bg-gray-200 text-gray-700',
};

// A version's value is a dict for a block or a slot, a dict for a note, a
// plain redaction marker string, or null on a delete. Reduced to text once,
// the same way, so the diff below never has to know which kind it is looking at.
function toText(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function fmtAt(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function MemoryHistoryPanel({ poolId, kind, itemKey, itemLabel, onClose, onChanged }) {
  const { t } = useI18n();
  const toast = useToast();
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null);
  const [busy, setBusy] = useState(null);
  const [alsoCurrentByRow, setAlsoCurrentByRow] = useState({});

  // Promise chain rather than try/await: the lint rule cannot tell that no
  // state is set before the first await of an async function with a catch.
  // The effect calls this directly (`loading` starts true); reloads after an
  // action go through `load`, which raises the busy flag first.
  const fetchRows = useCallback(() => (
    listMemoryVersions(poolId, { kind, item_key: itemKey, limit: 200 })
      .then((resp) => setRows(resp.data.versions || []))
      .catch((e) => toast.error(t('memoryVersions.errors.load'), errorDetail(e)))
      .finally(() => setLoading(false))
  ), [poolId, kind, itemKey, t, toast]);

  const load = useCallback(() => {
    setLoading(true);
    return fetchRows();
  }, [fetchRows]);

  useEffect(() => { fetchRows(); }, [fetchRows]);

  // The previous version of the SAME item, wherever this list happens to sit
  // (one item's own history, or the whole pool's): the diff is always against
  // that item's own last row, never against whatever the list shows above it.
  const previousOf = (row) => {
    const sameItem = rows.filter((r) => r.kind === row.kind && r.item_key === row.item_key);
    const pos = sameItem.findIndex((r) => r.id === row.id);
    return pos >= 0 && pos + 1 < sameItem.length ? sameItem[pos + 1] : null;
  };

  const handleRestore = async (row) => {
    if (!window.confirm(t('memoryVersions.confirmRestore', { version: row.version }))) return;
    setBusy(row.id);
    try {
      await restoreMemoryVersion(poolId, row.id);
      toast.success(t('memoryVersions.restored'));
      await load();
      onChanged?.();
    } catch (e) {
      toast.error(t('memoryVersions.errors.restore'), errorDetail(e));
    } finally {
      setBusy(null);
    }
  };

  const handleRedact = async (row) => {
    if (!window.confirm(t('memoryVersions.confirmRedact', { version: row.version }))) return;
    const alsoCurrent = !!alsoCurrentByRow[row.id];
    setBusy(row.id);
    try {
      await redactMemoryVersion(poolId, row.id, { also_current: alsoCurrent });
      toast.success(t('memoryVersions.redactedDone'));
      await load();
      if (alsoCurrent) onChanged?.();
    } catch (e) {
      toast.error(t('memoryVersions.errors.redact'), errorDetail(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-stretch justify-end z-50">
      <div className="bg-white w-full max-w-lg h-full shadow-xl flex flex-col">
        <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between bg-gray-50">
          <h3 className="font-semibold text-gray-800 flex items-center gap-2 min-w-0">
            <History className="w-4 h-4 text-indigo-500 shrink-0" />
            <span className="truncate">
              {itemLabel ? t('memoryVersions.historyOf', { item: itemLabel }) : t('memoryVersions.poolHistory')}
            </span>
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 shrink-0">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {loading ? (
            <p className="text-sm text-gray-400 text-center py-8">{t('common.loading')}</p>
          ) : rows.length === 0 ? (
            <p className="text-sm text-gray-400 text-center py-8 italic">{t('memoryVersions.noHistoryYet')}</p>
          ) : rows.map((row) => {
            const prev = previousOf(row);
            const diff = lineDiff(toText(prev?.value), toText(row.value));
            const isOpen = expanded === row.id;
            return (
              <div key={row.id} className="border border-gray-200 rounded-lg overflow-hidden">
                <button
                  type="button"
                  onClick={() => setExpanded(isOpen ? null : row.id)}
                  className="w-full flex items-center justify-between gap-2 px-3 py-2 bg-gray-50 hover:bg-gray-100 text-left"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className={`text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded-full shrink-0 ${OP_BADGE[row.op] || 'bg-gray-100 text-gray-600'}`}>
                      {t(`memoryVersions.ops.${row.op}`)}
                    </span>
                    <span className="text-xs text-gray-500 truncate">
                      {t('memoryVersions.versionN', { n: row.version })}
                      {!itemKey ? ` · ${t(`memoryVersions.kinds.${row.kind}`)}:${row.item_key}` : ''}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 text-xs text-gray-400">
                    <span>{row.actor_kind === 'system' ? t('memoryVersions.system') : `${row.actor_kind}${row.actor_id ? `:${row.actor_id}` : ''}`}</span>
                    <span>{fmtAt(row.at)}</span>
                    {isOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                  </div>
                </button>
                {isOpen && (
                  <div className="p-3 space-y-3">
                    {row.redacted ? (
                      <p className="text-xs italic text-amber-700 bg-amber-50 border border-amber-100 rounded px-2 py-1.5">
                        {t('memoryVersions.redactedMarker')}
                      </p>
                    ) : (
                      <pre className="text-xs font-mono bg-gray-900 text-gray-100 rounded p-2 overflow-x-auto max-h-56">
                        {diff.map((d, idx) => (
                          <div
                            key={idx}
                            className={
                              d.type === 'added' ? 'bg-emerald-900/40 text-emerald-300'
                                : d.type === 'removed' ? 'bg-red-900/40 text-red-300 line-through'
                                  : ''
                            }
                          >
                            {(d.type === 'added' ? '+ ' : d.type === 'removed' ? '- ' : '  ') + d.text}
                          </div>
                        ))}
                      </pre>
                    )}
                    <div className="flex items-center justify-between gap-3 flex-wrap">
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => handleRestore(row)}
                          disabled={busy === row.id}
                          className="flex items-center gap-1 text-xs border border-indigo-200 text-indigo-600 px-2.5 py-1.5 rounded-lg hover:bg-indigo-50 disabled:opacity-40"
                        >
                          <RotateCcw className="w-3 h-3" /> {t('memoryVersions.restore')}
                        </button>
                        {!row.redacted && (
                          <button
                            type="button"
                            onClick={() => handleRedact(row)}
                            disabled={busy === row.id}
                            className="flex items-center gap-1 text-xs border border-red-200 text-red-600 px-2.5 py-1.5 rounded-lg hover:bg-red-50 disabled:opacity-40"
                          >
                            <EyeOff className="w-3 h-3" /> {t('memoryVersions.redact')}
                          </button>
                        )}
                      </div>
                      {!row.redacted && (
                        <label className="flex items-center gap-1.5 text-xs text-gray-500">
                          <input
                            type="checkbox"
                            checked={!!alsoCurrentByRow[row.id]}
                            onChange={(e) => setAlsoCurrentByRow((prev) => ({ ...prev, [row.id]: e.target.checked }))}
                          />
                          {t('memoryVersions.alsoCurrent')}
                        </label>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export { MemoryHistoryPanel };
