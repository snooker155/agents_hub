/**
 * The version history of one skill: every content change, who made it and
 * how (an edit, a sync from the repository, an install, a restore), a line
 * diff against the version before it, and the two things a person does with
 * a past version: put the skill back to it, or pin the agent the skill is
 * attached to on it, so the agent keeps the text it was tested with.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { History, Pin, PinOff, RotateCcw, X } from 'lucide-react';
import {
  getSkillVersion, listSkillVersions, pinSkillVersion, restoreSkillVersion,
} from '../../api/skillVersions';
import { useI18n } from '../../i18n';
import { lineDiff } from '../../lib/lineDiff';
import { errorDetail, useToast } from '../toast';
import { snapshotText } from './skillText';

const OP_BADGE = {
  create: 'bg-emerald-100 text-emerald-700',
  update: 'bg-blue-100 text-blue-700',
  restore: 'bg-amber-100 text-amber-700',
  import: 'bg-indigo-100 text-indigo-700',
  sync: 'bg-indigo-100 text-indigo-700',
  install: 'bg-gray-100 text-gray-700',
  origin: 'bg-purple-100 text-purple-700',
};

function fmtAt(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export default function SkillHistoryModal({ skill, onClose, onChanged }) {
  const { t } = useI18n();
  const toast = useToast();
  const [versions, setVersions] = useState([]);
  const [pinned, setPinned] = useState(skill.pinned_version ?? null);
  const [current, setCurrent] = useState(skill.version ?? null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [snapshots, setSnapshots] = useState({});
  const [busy, setBusy] = useState(false);

  // A repo skill's catalog entry mirrors its folder: it is changed by a sync,
  // never restored here. An attached copy of it is the agent's own.
  const readOnly = skill.source === 'repo' && !skill.agent_id;
  const attached = Boolean(skill.agent_id);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await listSkillVersions(skill.id);
      const rows = data?.versions || [];
      setVersions(rows);
      setPinned(data?.pinned ?? null);
      setCurrent(data?.current ?? null);
      setSelected((prev) => prev ?? rows[0]?.version ?? null);
    } catch (e) {
      toast.error(t('skillsCatalog.history.loadFailed'), errorDetail(e));
    } finally {
      setLoading(false);
    }
  }, [skill.id, t, toast]);

  useEffect(() => { load(); }, [load]);

  // The selected version and the one before it, for the diff.
  const previous = useMemo(() => {
    const idx = versions.findIndex((v) => v.version === selected);
    return idx >= 0 ? versions[idx + 1] || null : null;
  }, [versions, selected]);

  useEffect(() => {
    const wanted = [selected, previous?.version].filter((v) => v != null && !(v in snapshots));
    if (!wanted.length) return;
    let cancelled = false;
    Promise.all(wanted.map((v) => getSkillVersion(skill.id, v)))
      .then((resps) => {
        if (cancelled) return;
        setSnapshots((prev) => {
          const next = { ...prev };
          resps.forEach((r) => { next[r.data.version] = r.data.snapshot; });
          return next;
        });
      })
      .catch((e) => toast.error(t('skillsCatalog.history.loadFailed'), errorDetail(e)));
    return () => { cancelled = true; };
  }, [selected, previous, snapshots, skill.id, t, toast]);

  const diff = useMemo(() => {
    if (selected == null || !(selected in snapshots)) return null;
    const before = previous ? snapshots[previous.version] : null;
    if (previous && !before) return null;
    return lineDiff(snapshotText(before, t), snapshotText(snapshots[selected], t));
  }, [selected, previous, snapshots, t]);

  const act = async (fn, successKey) => {
    setBusy(true);
    try {
      const { data } = await fn();
      toast.success(t(successKey, { version: selected }));
      onChanged?.(data);
      await load();
    } catch (e) {
      toast.error(t('skillsCatalog.history.actionFailed'), errorDetail(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" role="dialog"
      aria-modal="true" aria-label={t('skillsCatalog.history.title', { name: skill.name })}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-4xl max-h-[90vh] flex flex-col">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <h3 className="text-lg font-bold text-gray-800 flex items-center gap-2">
            <History className="w-5 h-5 text-indigo-600" />
            {t('skillsCatalog.history.title', { name: skill.name })}
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label={t('common.close')}>
            <X className="w-5 h-5" />
          </button>
        </div>

        {attached && (
          <div className="px-6 py-2 text-xs border-b border-gray-100 flex items-center gap-2 text-gray-600">
            {pinned ? (
              <>
                <Pin className="w-3.5 h-3.5 text-amber-600" />
                <span>{t('skillsCatalog.history.pinnedTo', { version: pinned })}</span>
                <button
                  onClick={() => act(() => pinSkillVersion(skill.id, null), 'skillsCatalog.history.unpinned')}
                  disabled={busy}
                  className="ml-auto inline-flex items-center gap-1 px-2 py-1 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-50"
                >
                  <PinOff className="w-3.5 h-3.5" /> {t('skillsCatalog.history.unpin')}
                </button>
              </>
            ) : (
              <span>{t('skillsCatalog.history.followsLatest')}</span>
            )}
          </div>
        )}

        <div className="flex-1 min-h-0 grid grid-cols-1 md:grid-cols-[220px_1fr]">
          <ul className="border-r border-gray-100 overflow-y-auto max-h-[60vh]" aria-label={t('skillsCatalog.history.versions')}>
            {loading && <li className="px-4 py-3 text-xs text-gray-400">{t('skillsCatalog.history.loading')}</li>}
            {!loading && versions.length === 0 && (
              <li className="px-4 py-3 text-xs text-gray-400">{t('skillsCatalog.history.empty')}</li>
            )}
            {versions.map((v) => (
              <li key={v.version}>
                <button
                  onClick={() => setSelected(v.version)}
                  className={`w-full text-left px-4 py-2 text-xs border-b border-gray-50 hover:bg-gray-50 ${selected === v.version ? 'bg-indigo-50' : ''}`}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="font-semibold text-gray-800">v{v.version}</span>
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${OP_BADGE[v.op] || 'bg-gray-100 text-gray-700'}`}>
                      {t(`skillsCatalog.history.op.${v.op}`)}
                    </span>
                    {v.version === current && (
                      <span className="text-[10px] text-emerald-700">{t('skillsCatalog.history.current')}</span>
                    )}
                    {v.version === pinned && <Pin className="w-3 h-3 text-amber-600" aria-label={t('skillsCatalog.history.pinned')} />}
                  </div>
                  <div className="text-[11px] text-gray-400 mt-0.5">{fmtAt(v.at)}</div>
                  <div className="text-[11px] text-gray-500 truncate">
                    {v.actor_kind === 'system' ? t('skillsCatalog.history.bySystem') : `${v.actor_kind}: ${v.actor_id || ''}`}
                  </div>
                  {v.note && <div className="text-[11px] text-gray-500 truncate" title={v.note}>{v.note}</div>}
                </button>
              </li>
            ))}
          </ul>

          <div className="flex flex-col min-h-0">
            <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-100 text-xs">
              <span className="text-gray-500">
                {previous
                  ? t('skillsCatalog.history.diffAgainst', { version: selected, previous: previous.version })
                  : t('skillsCatalog.history.firstVersion')}
              </span>
              <div className="ml-auto flex gap-2">
                {attached && selected != null && selected !== pinned && (
                  <button
                    onClick={() => act(() => pinSkillVersion(skill.id, selected), 'skillsCatalog.history.pinnedDone')}
                    disabled={busy}
                    className="inline-flex items-center gap-1 px-2 py-1 rounded border border-gray-200 hover:bg-gray-50 disabled:opacity-50"
                  >
                    <Pin className="w-3.5 h-3.5" /> {t('skillsCatalog.history.pin', { version: selected })}
                  </button>
                )}
                {!readOnly && selected != null && selected !== current && (
                  <button
                    onClick={() => {
                      if (!window.confirm(t('skillsCatalog.history.confirmRestore', { version: selected }))) return;
                      act(() => restoreSkillVersion(skill.id, selected), 'skillsCatalog.history.restored');
                    }}
                    disabled={busy}
                    className="inline-flex items-center gap-1 px-2 py-1 rounded bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
                  >
                    <RotateCcw className="w-3.5 h-3.5" /> {t('skillsCatalog.history.restore')}
                  </button>
                )}
              </div>
            </div>
            {readOnly && (
              <p className="px-4 py-2 text-[11px] text-gray-500 border-b border-gray-100">
                {t('skillsCatalog.history.repoReadOnly')}
              </p>
            )}
            <pre className="flex-1 overflow-auto max-h-[55vh] text-xs font-mono p-4 whitespace-pre-wrap" data-testid="skill-diff">
              {diff === null
                ? t('skillsCatalog.history.loading')
                : diff.map((row, i) => (
                  <div
                    key={i}
                    className={row.type === 'added' ? 'bg-emerald-50 text-emerald-800'
                      : row.type === 'removed' ? 'bg-red-50 text-red-800 line-through' : 'text-gray-700'}
                  >
                    {row.type === 'added' ? '+ ' : row.type === 'removed' ? '- ' : '  '}{row.text}
                  </div>
                ))}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
}
