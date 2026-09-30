import React, { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { Gauge, LayoutGrid, Loader, MessagesSquare, Repeat } from 'lucide-react';
import { getScenarioRunViews, startReproducibilityEval } from '../../api/lab';
import EntityChat from '../../components/EntityChat';
import InPanelNote from '../../components/pageChat/InPanelNote';
import { usePageChatPanel } from '../../components/pageChat/pageChat';
import { useI18n } from '../../i18n';
import WorldView from './renderers';
import { RunParams } from './history';
import { RunMeters } from './run-meters';

/** One of the two readings of a run. A switch rather than two panes, so the
    column belongs to whichever one you asked for — and set at the weight of
    the panel headings it replaced, since that is what it is: the title of what
    you are looking at, which happens to be clickable. */
export function PaneTab({ active, onClick, icon: Icon, label, badge = 0 }) {
  return (
    <button
      type="button" onClick={onClick}
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-bold uppercase tracking-wide transition-colors ${
        active ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
      }`}
    >
      <Icon className={`w-3.5 h-3.5 ${active ? 'text-indigo-500' : 'text-gray-400'}`} />
      {label}
      {badge > 0 && (
        <span className="px-1.5 rounded-full bg-indigo-50 text-indigo-600 text-[10px] font-bold">
          {badge > 99 ? '99+' : badge}
        </span>
      )}
    </button>
  );
}


/** The sidebar card's header: the switch *is* the title, in two halves across
    the card's full width. A pill above the card plus a heading inside it named
    the same panel twice and left the card looking untitled — this is one
    header that happens to be clickable, which is what a tab is. */
export function ColumnTab({ active, onClick, icon: Icon, label }) {
  return (
    <button
      type="button" onClick={onClick}
      className={`flex items-center justify-center gap-1.5 px-3 py-3 text-xs font-bold uppercase tracking-wide border-b-2 transition-colors ${
        active
          ? 'bg-white border-indigo-500 text-gray-800'
          : 'bg-gray-50 border-transparent text-gray-400 hover:text-gray-600 hover:bg-gray-100'
      }`}
    >
      <Icon className={`w-4 h-4 shrink-0 ${active ? 'text-indigo-500' : 'text-gray-400'}`} />
      <span className="truncate">{label}</span>
    </button>
  );
}


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
 * The scenario page's right-hand column.
 *
 * Two tabs over one column, not two columns: what the scenario *is* and a chat
 * that can change it are both read at the full height of the sidebar, and only
 * ever one at a time. The readouts stay the default — you open this page to
 * look at something, and the chat is what you turn to when looking is not
 * enough.
 *
 * The chat's callbacks are memoised on the scenario id on purpose: EntityChat
 * loads its transcript in an effect keyed on them, so fresh closures every
 * render would refetch the conversation on every keystroke.
 */
export function ScenarioSidebar({
  tab, onTab, scenario, envSpec, currentTick, run, ticks, live, chat,
}) {
  const { t } = useI18n();
  const { inlineSuppressed: panelHoldsChat } = usePageChatPanel();

  /* The header of whichever panel is open — one bar, two halves, the card's
     full width, and the only place either panel is named. */
  const tabs = (
    <div className="shrink-0 grid grid-cols-2 border-b border-gray-200">
      <ColumnTab
        active={tab === 'params'} onClick={() => onTab('params')}
        icon={Gauge} label={t('playground.parameters')}
      />
      <ColumnTab
        active={tab === 'chat'} onClick={() => onTab('chat')}
        icon={MessagesSquare} label={t('playground.scenarioChat')}
      />
    </div>
  );

  return (
    <div className="flex flex-col xl:min-h-0">
      {tab === 'chat' ? (
        /* The chat owns the whole column: a build conversation that has to be
           scrolled in a 12-line box is not one you will use. The tab bar is
           the card's header, so the card carries no padding of its own — the
           body below it does. */
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden flex flex-col xl:flex-1 xl:min-h-0">
          {tabs}
          {/* The chat fills the body, so its composer lands on the card's
              floor rather than under the last message, and the negative
              margins take its rule out to the card's edges past the padding —
              the same footing the transcript's trigger composer stands on. */}
          <div className="p-5 flex flex-col flex-1 min-h-0">
            {/* While the floating panel is holding this same conversation,
                the tab says where it went instead of running a second copy. */}
            {panelHoldsChat ? <InPanelNote /> : (
              <EntityChat
                {...chat}
                header={false}
                /* Below xl the page is an ordinary scroll, so the feed is a
                   bounded box. In the column it takes exactly what is left —
                   including nothing, on a short window with the transport bar
                   up: it scrolls itself, so shrinking is right and growing past
                   the column's floor is not. */
                heightClass="max-h-[26rem] min-h-[14rem] xl:max-h-none xl:min-h-0"
                className="flex-1 min-h-0"
                composerClassName="mt-3 xl:mt-auto -mx-5 px-5"
              />
            )}
          </div>
        </div>
      ) : (
        /* Readouts: the world as it stands, and how the run is doing. The
           column scrolls on its own so a long sidebar cannot stretch the row
           past the bottom of the screen. */
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
      )}
    </div>
  );
}

export default ScenarioSidebar;
