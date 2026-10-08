import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Brain, Loader2, RefreshCw } from 'lucide-react';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import { getModelStructure } from '../api/modelStructure';
import { loadModelView, readLocalModelView, saveModelView } from '../lib/modelView';
import Structure2D from '../components/modelStructure/Structure2D';
import TensorTable from '../components/modelStructure/TensorTable';
import ModelCard from '../components/modelStructure/ModelCard';
import StatTiles from '../components/modelStructure/StatTiles';
import DtypeLegend from '../components/modelStructure/DtypeLegend';
import { buildGraph } from '../components/modelStructure/graph';
import PageLoader from '../components/PageLoader';

// three.js is heavy and only needed once someone opens the 3D view.
const Structure3D = lazy(() => import('../components/modelStructure/Structure3D'));

// Feature 6: the read-only structure of one model, drawn in 2D (reactflow) or
// 3D (three) from one parsed graph. The selected block is state here, so
// switching views keeps it. An API model answers with a card instead.

function safeDecode(value) {
  try { return decodeURIComponent(value || ''); } catch { return value || ''; }
}

function errorText(err) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === 'string') return detail;
  if (detail) return JSON.stringify(detail);
  return err?.message || String(err);
}

function ViewToggle({ value, onChange }) {
  const { t } = useI18n();
  return (
    <div role="group" aria-label={t('modelStructure.viewLabel')} className="inline-flex rounded-lg border border-gray-200 bg-white p-0.5">
      {['2d', '3d'].map((v) => (
        <button
          key={v}
          type="button"
          aria-pressed={value === v}
          onClick={() => onChange(v)}
          className={`rounded-md px-3 py-1 text-sm font-medium transition-colors ${
            value === v ? 'bg-indigo-600 text-white' : 'text-gray-600 hover:text-indigo-600'
          }`}
        >
          {t(`modelStructure.view${v}`)}
        </button>
      ))}
    </div>
  );
}

export default function ModelDetail() {
  const { t } = useI18n();
  const params = useParams();
  const provider = params.provider || '';
  const model = safeDecode(params.model);

  const [reloadKey, setReloadKey] = useState(0);
  // The answer is kept with the request it answers, so "loading" is simply
  // "the answer on hand is for a different request" and needs no state.
  const [result, setResult] = useState(null);
  const requestKey = `${provider}\n${model}\n${reloadKey}`;
  const loading = result?.key !== requestKey;
  const data = loading ? null : result.data;
  const error = loading ? null : result.error;
  const [view, setView] = useState(readLocalModelView);
  // undefined until the person picks: the first block is shown. null after
  // a click outside every block: nothing is selected.
  const [pickedId, setPickedId] = useState(undefined);
  const touchedView = useRef(false);

  // The account's saved view, when there is one, wins over this browser's,
  // unless the person already flipped the toggle before it answered.
  useEffect(() => {
    let alive = true;
    loadModelView().then((v) => { if (alive && !touchedView.current) setView(v); });
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    let alive = true;
    getModelStructure(provider, model)
      .then(({ data: body }) => { if (alive) setResult({ key: requestKey, data: body, error: null }); })
      .catch((err) => { if (alive) setResult({ key: requestKey, data: null, error: errorText(err) }); });
    return () => { alive = false; };
  }, [provider, model, requestKey]);

  const isStructure = data?.kind === 'structure';
  const graph = useMemo(() => (isStructure ? buildGraph(data) : { nodes: [], edges: [] }), [data, isStructure]);

  // Start on the first block so the table is not empty on arrival, and fall
  // back to it when a reload no longer has the block that was picked.
  const selected = useMemo(
    () => (pickedId === null
      ? null
      : graph.nodes.find((n) => n.id === pickedId) || graph.nodes[0] || null),
    [graph, pickedId],
  );
  const selectedId = selected?.id ?? null;

  const changeView = useCallback((v) => {
    touchedView.current = true;
    setView(v);
    saveModelView(v);
  }, []);

  const name = data?.model?.name || data?.model?.id || model;
  const Renderer = view === '3d' ? Structure3D : Structure2D;

  return (
    <PageContainer>
      <PageHeader
        icon={Brain}
        title={name}
        description={t('modelStructure.description')}
        backTo="/models"
        backLabel={t('modelStructure.backToModels')}
        badges={(
          <span className="rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-xs text-gray-500">{provider}</span>
        )}
        actions={isStructure && graph.nodes.length > 0 ? <ViewToggle value={view} onChange={changeView} /> : null}
      />

      {loading && (
        <div className="flex items-center gap-2 text-sm text-gray-500" role="status">
          <Loader2 className="h-4 w-4 animate-spin" /> {t('modelStructure.loading')}
        </div>
      )}

      {!loading && error && (
        <div className="flex flex-wrap items-center gap-3 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700" role="alert">
          <span>{t('modelStructure.loadError', { error })}</span>
          <button
            type="button"
            onClick={() => setReloadKey((k) => k + 1)}
            className="inline-flex items-center gap-1 rounded-md border border-red-300 bg-white px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-100"
          >
            <RefreshCw className="h-3 w-3" /> {t('modelStructure.retry')}
          </button>
        </div>
      )}

      {!loading && !error && data?.kind === 'card' && <ModelCard model={data.model} />}

      {!loading && !error && isStructure && (
        <div className="flex flex-col gap-4">
          <StatTiles model={data.model || {}} memory={data.memory || {}} />
          {data.source && (
            <p className="text-xs text-gray-500">{t('modelStructure.source', { source: data.source })}</p>
          )}
          {graph.nodes.length === 0 ? (
            <div className="rounded-xl border border-dashed border-gray-300 bg-white p-6 text-center text-sm text-gray-500">
              {t('modelStructure.noBlocks')}
            </div>
          ) : (
            <>
              {/* A horizontal strip: the chain in one row, other blocks under it. */}
              <div className="h-[320px] overflow-hidden rounded-xl border border-gray-200 bg-gray-50">
                <Suspense
                  fallback={(
                    <div className="h-full text-sm text-gray-500"><PageLoader /></div>
                  )}
                >
                  <Renderer graph={graph} selectedId={selectedId} onSelect={setPickedId} />
                </Suspense>
              </div>
              <DtypeLegend nodes={graph.nodes} />
              <TensorTable key={selected?.id || 'none'} block={selected} />
            </>
          )}
        </div>
      )}
    </PageContainer>
  );
}
