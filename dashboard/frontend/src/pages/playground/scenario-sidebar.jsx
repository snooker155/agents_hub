import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Gauge, LayoutGrid, Loader, MessagesSquare, Repeat } from 'lucide-react';
import { getScenarioRunViews, startReproducibilityEval } from '../../api/lab';
import { usePageChatPanel } from '../../components/pageChat/pageChat';
import { useI18n } from '../../i18n';
import WorldView from './renderers';
import { RunParams } from './history';
import { RunMeters } from './run-meters';
import { ColumnTab, PaneTab } from '../../components/PageTabs';

// The two tab kinds live in components/PageTabs.jsx now, shared with the
// flow run page; re-exported so the playground's imports stay as they were.
export { PaneTab, ColumnTab };


/**
 * The views a scenario run has published (the lab's results table, chart,
 * formulas and report), as links to their own pages. Refetched as ticks land,
 * because a view appears the tick its first data does. Renders nothing for a
 * run that published none, which is every run of a world without views.
 */
export function RunViewsStrip({ run, tickCount = 0 }) {
  const { t } = useI18n();
  // Keyed by run, so switching runs never shows the previous run's views.
  const [loaded, setLoaded] = useState({ runId: null, views: [] });
  const runId = run?.sim_run_id;
  const status = run?.status;
  useEffect(() => {
    if (!runId) return undefined;
    let cancelled = false;
    (async () => {
      try {
        const { data } = await getScenarioRunViews(runId);
        if (!cancelled) setLoaded({ runId, views: data?.views || [] });
      } catch { /* views are a by-product; none is a fine answer */ }
    })();
    return () => { cancelled = true; };
  }, [runId, status, tickCount]);
  const views = loaded.runId === runId ? loaded.views : [];
  if (views.length === 0) return null;
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
      <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-1 flex items-center gap-1.5">
        <LayoutGrid className="w-4 h-4 text-indigo-500" /> {t('playground.runViews.title')}
      </h3>
      <p className="text-[11px] text-gray-400 mb-2">{t('playground.runViews.hint')}</p>
      <div className="flex flex-wrap gap-1.5">
        {views.map((v) => (
          <Link
            key={v.view_id}
            to={`/views/${v.view_id}`}
            className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-indigo-100 bg-indigo-50 text-[11px] font-semibold text-indigo-700 hover:bg-indigo-100"
          >
            {v.title || v.kind}
            <span className="text-indigo-400 font-normal">{v.kind}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

/**
 * "Reproducibility run": the same scenario N times as an eval, so whether a
 * result holds up is a score on the Evals page rather than a feeling.
 */
export function ReproducibilityButton({ scenarioId, disabled = false, onMessage }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [repeats, setRepeats] = useState(3);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  const start = async () => {
    setBusy(true);
    setResult(null);
    try {
      const { data } = await startReproducibilityEval(scenarioId, { repeats });
      setResult(data);
    } catch (e) {
      onMessage?.(e.response?.data?.detail || t('playground.repro.failed'));
      setOpen(false);
    } finally {
      setBusy(false);
    }
  };

  const evalLink = result
    ? `/evals?set=${encodeURIComponent(result.eval_id)}${
      result.eval_run_id ? `&run=${encodeURIComponent(result.eval_run_id)}` : ''}`
    : '';

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        disabled={disabled}
        title={t('playground.repro.title')}
        className="inline-flex items-center px-3 py-2 text-xs font-semibold text-indigo-700 bg-white border border-indigo-200 rounded-lg hover:bg-indigo-50 disabled:opacity-50"
      >
        <Repeat className="w-3.5 h-3.5 mr-1" /> {t('playground.repro.button')}
      </button>
      {open && (
        <div className="absolute right-0 mt-1 w-64 bg-white border border-gray-200 rounded-lg shadow-lg z-30 p-3 space-y-2">
          <p className="text-[11px] text-gray-500">{t('playground.repro.title')}</p>
          <label className="flex items-center justify-between gap-2 text-xs text-gray-700">
            {t('playground.repro.repeats')}
            <input
              type="number" min={1} max={10} value={repeats}
              onChange={(e) => setRepeats(Math.max(1, Math.min(10, parseInt(e.target.value, 10) || 1)))}
              className="w-16 text-sm border border-gray-300 rounded-md px-2 py-1"
            />
          </label>
          {result ? (
            <div className="text-xs text-green-700 space-y-1">
              <div>{t('playground.repro.started')}</div>
              <Link to={evalLink} className="font-semibold text-indigo-600 hover:text-indigo-800">
                {t('playground.repro.open')}
              </Link>
            </div>
          ) : (
            <button
              type="button" onClick={start} disabled={busy}
              className="w-full inline-flex items-center justify-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-50"
            >
              {busy && <Loader className="w-3.5 h-3.5 mr-1 animate-spin" />}
              {busy ? t('playground.repro.starting') : t('playground.repro.start')}
            </button>
          )}
        </div>
      )}
    </div>
  );
}


/**
 * The scenario page's right-hand column: what the scenario *is* (the world as
 * it stands and how the run is doing).
 *
 * The build chat is the page chat: its tab here opens the floating panel, the
 * same one the corner launcher opens, with the same conversation.
 */
export function ScenarioSidebar({
  scenario, envSpec, currentTick, run, ticks, live,
}) {
  const { t } = useI18n();
  const { open: chatOpen, setOpen: setChatOpen } = usePageChatPanel();

  /* The column's header: the readouts, and the way to the chat. The readouts
     never leave the column; the Chat tab is the panel being open. */
  const tabs = (
    <div className="shrink-0 grid grid-cols-2 border-b border-gray-200">
      <ColumnTab
        active={!chatOpen} onClick={() => setChatOpen(false)}
        icon={Gauge} label={t('playground.parameters')}
      />
      <ColumnTab
        active={chatOpen} onClick={() => setChatOpen(true)}
        icon={MessagesSquare} label={t('playground.scenarioChat')}
      />
    </div>
  );

  return (
    <div className="flex flex-col xl:min-h-0">
        {/* Readouts: the world as it stands, and how the run is doing. The
            column scrolls on its own so a long sidebar cannot stretch the row
            past the bottom of the screen. */}
        <div className="space-y-6 xl:min-h-0 xl:overflow-y-auto xl:pr-1">
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
            {tabs}
            <div className="p-5">
              {/* Which world these readouts are of — a caption under the
                  header, not a second heading competing with it. */}
              <div className="mb-4 text-[11px] text-gray-400 uppercase tracking-wide truncate">
                {t('playground.world')}{envSpec ? ` — ${envSpec.env_name}` : ''}
              </div>
              <WorldView frame={currentTick?.frame} />
              {(envSpec?.params || []).length > 0 && (
                <dl className="mt-4 pt-3 border-t border-gray-100 space-y-1">
                  {envSpec.params.map((p) => (
                    <div key={p.name} className="flex items-baseline justify-between gap-2 text-[11px]">
                      <dt className="text-gray-500 truncate" title={p.description}>{p.name}</dt>
                      <dd className="font-mono text-gray-800 text-right truncate">
                        {String(scenario.env_params?.[p.name]
                          ?? (Array.isArray(p.default) ? p.default.join(', ') : p.default))}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          </div>

          {run && <RunMeters run={run} ticks={ticks} scenario={scenario} />}

          {run && <RunViewsStrip run={run} tickCount={ticks.length} />}

          {run?.scores && Object.keys(run.scores).length > 0 && !live && (
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-3">
                {t('playground.objectives')}
              </h3>
              <div className="space-y-2">
                {Object.entries(run.scores).map(([name, vals]) => (
                  <div key={name} className="text-xs">
                    <span className="font-semibold text-gray-900">{name}</span>
                    <div className="text-gray-600">
                      {Object.entries(vals).map(([k, v]) => `${k}: ${v}`).join(' · ')}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Last in the column: what the run was configured with is
              provenance, looked up when a result needs explaining, so it sits
              below the readouts that are watched while it runs. Its own launch
              snapshot, so it still reads true after the scenario has been
              retuned. */}
          {run && <RunParams run={run} scenario={scenario} />}
        </div>
    </div>
  );
}

export default ScenarioSidebar;
