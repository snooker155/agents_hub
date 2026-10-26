import React, { useCallback, useEffect, useState } from 'react';
import { ArrowRight, Loader2, Plus, Repeat, Trash2, X } from 'lucide-react';
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { getLoops, createLoop, deleteLoop, listFlows } from '../api';
import { useWorkspace } from '../components/workspace';
import { useStream } from '../components/stream';
import { errorDetail, useToast } from '../components/toast';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { StatusBadge } from '../components/loops/loopUi';
import { emptyLoop, hasScore, isLive, scoreColor } from '../components/loops/loopFormat';
import { useI18n } from '../i18n';

/**
 * Loops: a flow that repeats until an agent says the work is good enough.
 *
 * This page is the list; each loop has a page of its own (pages/LoopDetail.jsx)
 * with the flow's graph, its runs pass by pass and the Loop Creator beside
 * them. Older links (`?loop=<id>&run=<run>`) are sent on to that page.
 */
export default function Loops() {
  const { t } = useI18n();
  const toast = useToast();
  const navigate = useNavigate();
  const { selectedWorkspace } = useWorkspace();
  const [params] = useSearchParams();
  const [loops, setLoops] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [flows, setFlows] = useState([]);
  const [showNew, setShowNew] = useState(false);

  const loadLoops = useCallback(() => getLoops(selectedWorkspace)
    .then(({ data }) => setLoops(data.loops || []))
    .catch(() => setLoops([]))
    .finally(() => setLoaded(true)), [selectedWorkspace]);

  useEffect(() => { loadLoops(); }, [loadLoops]);

  useEffect(() => {
    listFlows(selectedWorkspace)
      .then((f) => setFlows(f.data.flows || f.data || []))
      .catch(() => { /* the new-loop dialog just offers no flows */ });
  }, [selectedWorkspace]);

  const { onRefetch } = useStream();
  useEffect(() => onRefetch(loadLoops), [onRefetch, loadLoops]);

  const legacyLoop = params.get('loop');
  if (legacyLoop) {
    const legacyRun = params.get('run');
    const to = `/loops/${encodeURIComponent(legacyLoop)}${legacyRun ? `/runs/${encodeURIComponent(legacyRun)}` : ''}`;
    return <Navigate to={to} replace />;
  }

  const handleCreate = async (name, flowId) => {
    try {
      const { data } = await createLoop({ ...emptyLoop(selectedWorkspace), name, flow_id: flowId });
      setShowNew(false);
      navigate(`/loops/${encodeURIComponent(data.loop_id)}`);
    } catch (e) {
      toast.error(t('loops.createFailed'), errorDetail(e));
    }
  };

  // Deleting a loop takes its runs with it, so it asks first.
  const removeLoop = async (loop) => {
    if (!window.confirm(t('loops.confirmDelete', { name: loop.name }))) return;
    try {
      await deleteLoop(loop.loop_id);
    } catch (e) {
      toast.error(t('loops.deleteFailed'), errorDetail(e));
    }
    loadLoops();
  };

  return (
    <PageContainer>
      <PageHeader
        icon={Repeat}
        title={t('loops.loops')}
        description={t('loops.aFlowRunsItsNodes')}
        actions={
          <button
            onClick={() => setShowNew(true)}
            className="inline-flex items-center px-3 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
          >
            <Plus className="w-4 h-4 mr-1.5" /> {t('loops.newLoop2')}
          </button>
        }
      />

      {loaded && loops.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-10 text-center text-sm text-gray-500">
          {t('loops.noLoopsYet')}
        </div>
      ) : (
        // The card of a flow on the Flows page (pages/AgentFlows.jsx): the two
        // tiles and the row of actions. Fewer to a row than there, so a card
        // is wide enough for its description on a narrower screen too.
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 min-[1800px]:grid-cols-5" data-testid="loop-list">
          {loops.map((l) => {
            const last = l.last_run;
            const score = last && (hasScore(last.final_score) ? last.final_score : last.best_score);
            const meta = [
              l.flow_name,
              t('loops.listUpTo', { count: l.max_iterations }),
              hasScore(l.target_score) ? t('loops.listTarget', { score: l.target_score }) : null,
            ].filter(Boolean).join(' · ');
            return (
              <div key={l.loop_id} className="group relative rounded-lg border border-gray-100 bg-white p-4 shadow-sm transition-shadow hover:shadow-md">
                {/* The whole card opens the page: the name's link covers it (its
                    ::after), the actions sit above it. */}
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex items-center gap-1.5">
                      <Link
                        to={`/loops/${encodeURIComponent(l.loop_id)}`}
                        className="block truncate text-sm font-semibold text-gray-900 group-hover:text-cyan-600 after:absolute after:inset-0 after:rounded-lg"
                      >
                        {l.name}
                      </Link>
                      {isLive(last) && (
                        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-cyan-500" title={t('loops.statuses.running')} />
                      )}
                    </div>
                  </div>
                  {last && (
                    <div className="flex shrink-0 flex-col items-end gap-1">
                      <StatusBadge status={last.status} />
                    </div>
                  )}
                </div>
                {/* The description under the name and the status both, the
                    card's whole width. */}
                <div className="mb-3 mt-1">
                  <div className="line-clamp-3 text-[11px] text-gray-500" title={meta}>
                    {l.description || meta}
                  </div>
                  {!l.flow_exists && <div className="mt-0.5 text-[11px] text-red-600">{t('loops.flowIsMissing')}</div>}
                </div>

                <div className="mb-3 grid grid-cols-2 gap-2">
                  <div className="rounded-md border border-blue-100 bg-blue-50 px-2 py-1.5 text-center">
                    <div className="text-[9px] font-semibold uppercase tracking-wide text-blue-500">{t('loops.lastScore')}</div>
                    <div className="inline-flex items-center gap-1 text-xs font-semibold text-blue-900">
                      {hasScore(score) && <span className={`h-2 w-2 rounded-full ${scoreColor(score)}`} />}
                      {hasScore(score) ? `${Math.round(score)}/100` : t('loops.none')}
                    </div>
                  </div>
                  <div className="rounded-md border border-amber-100 bg-amber-50 px-2 py-1.5 text-center">
                    <div className="text-[9px] font-semibold uppercase tracking-wide text-amber-500">{t('loops.lastRun')}</div>
                    <div className="truncate text-xs font-semibold text-amber-900">
                      {last?.started_at ? new Date(last.started_at).toLocaleString() : t('loops.none')}
                    </div>
                  </div>
                </div>

                <div className="relative z-10 flex justify-end gap-1">
                  <Link
                    to={`/loops/${encodeURIComponent(l.loop_id)}`}
                    className="rounded-lg border border-transparent p-1.5 text-gray-400 transition hover:border-cyan-200 hover:bg-cyan-50 hover:text-cyan-600"
                    title={t('loops.openLoop')}
                  >
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Link>
                  <button
                    onClick={() => removeLoop(l)}
                    title={t('loops.deleteLoop')} aria-label={t('loops.deleteLoop')}
                    className="rounded-lg border border-transparent p-1.5 text-gray-400 transition hover:border-rose-200 hover:bg-rose-50 hover:text-rose-600"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {showNew && (
        <NewLoopModal flows={flows} onClose={() => setShowNew(false)} onCreate={handleCreate} />
      )}
    </PageContainer>
  );
}

function NewLoopModal({ flows, onClose, onCreate }) {
  const { t } = useI18n();
  const [name, setName] = useState('');
  const [flowId, setFlowId] = useState('');
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <h3 className="text-base font-bold text-gray-900">{t('loops.newLoop')}</h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-600">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-5 space-y-4">
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">{t('loops.name')}</label>
            <input
              value={name} onChange={(e) => setName(e.target.value)}
              className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
              placeholder={t('loops.refineTheLaunchPost')}
            />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-600 mb-1">{t('loops.flowToRepeat')}</label>
            <select
              value={flowId} onChange={(e) => setFlowId(e.target.value)}
              className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
            >
              <option value="">{t('loops.selectAFlow')}</option>
              {flows.map((f) => <option key={f.id} value={f.id}>{f.name || f.id}</option>)}
            </select>
          </div>
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-gray-200">
          <button onClick={onClose} className="px-3 py-2 text-sm font-semibold text-gray-600 hover:text-gray-800">
            {t('loops.cancel')}
          </button>
          <button
            onClick={() => onCreate(name.trim(), flowId)}
            disabled={!name.trim() || !flowId}
            className="px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {t('loops.create')}
          </button>
        </div>
      </div>
    </div>
  );
}
