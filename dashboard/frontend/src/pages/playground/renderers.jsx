import React, { Suspense, lazy, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useI18n } from '../../i18n';

// KaTeX is heavy and only a lab with formulas needs it, so the view that
// wraps it is loaded on first use rather than with the playground.
const LatexView = lazy(() => import('../../views/renderers/LatexView'));

/**
 * World renderers, keyed by the `renderer` an environment declares.
 *
 * Adding an environment must touch zero frontend code *for its parameters and
 * actions* — those are declared by the environment and rendered generically. A
 * genuinely new world view is the one thing that does need a component, so the
 * mapping is explicit and falls back to a readable JSON dump rather than a
 * blank pane.
 */

function Empty({ label }) {
  return <div className="text-sm text-gray-400 italic py-8 text-center">{label}</div>;
}

/** Sparkline over the trade tape — a trend without pulling in a chart library. */
function PriceLine({ prices }) {
  if (!prices || prices.length < 2) return null;
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const span = max - min || 1;
  const pts = prices
    .map((p, i) => `${(i / (prices.length - 1)) * 100},${30 - ((p - min) / span) * 28}`)
    .join(' ');
  return (
    <svg viewBox="0 0 100 30" preserveAspectRatio="none" className="w-full h-16">
      <polyline
        points={pts} fill="none" stroke="currentColor"
        className="text-indigo-500" strokeWidth="1" vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

function MarketView({ frame }) {
  const { t } = useI18n();
  if (!frame?.agents) return <Empty label={t('playgroundRenderers.noWorldStateYet')} />;
  const { book = {}, tape = [], agents = [] } = frame;
  return (
    <div className="space-y-4">
      <div className="flex items-baseline gap-4 flex-wrap">
        <div>
          <div className="text-xs text-gray-500 uppercase font-bold">{frame.ticker}</div>
          <div className="text-3xl font-bold text-gray-900">{frame.last_price ?? '—'}</div>
        </div>
        <div className="text-sm text-gray-500">
          {t('playgroundRenderers.bid')}{' '}
          <span className="font-semibold text-green-700">{frame.best_bid ?? '—'}</span>
          {' / '}
          {t('playgroundRenderers.ask')}{' '}
          <span className="font-semibold text-red-700">{frame.best_ask ?? '—'}</span>
        </div>
      </div>

      <PriceLine prices={frame.price_history} />

      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="text-[11px] font-bold text-gray-500 uppercase mb-1">{t('playgroundRenderers.bids')}</div>
          {(book.bids || []).length === 0
            ? <div className="text-xs text-gray-400 italic">{t('playgroundRenderers.empty')}</div>
            : (book.bids || []).map((o, i) => (
              <div key={i} className="flex justify-between text-xs py-0.5">
                <span className="text-green-700 font-semibold">{o.price}</span>
                <span className="text-gray-600">{o.qty}</span>
                <span className="text-gray-400 truncate ml-2">{o.agent}</span>
              </div>
            ))}
        </div>
        <div>
          <div className="text-[11px] font-bold text-gray-500 uppercase mb-1">{t('playgroundRenderers.asks')}</div>
          {(book.asks || []).length === 0
            ? <div className="text-xs text-gray-400 italic">{t('playgroundRenderers.empty')}</div>
            : (book.asks || []).map((o, i) => (
              <div key={i} className="flex justify-between text-xs py-0.5">
                <span className="text-red-700 font-semibold">{o.price}</span>
                <span className="text-gray-600">{o.qty}</span>
                <span className="text-gray-400 truncate ml-2">{o.agent}</span>
              </div>
            ))}
        </div>
      </div>

      <div>
        <div className="text-[11px] font-bold text-gray-500 uppercase mb-1">{t('playgroundRenderers.positions')}</div>
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-400">
              <th className="text-left font-medium">{t('playgroundRenderers.agent')}</th>
              <th className="text-right font-medium">{t('playgroundRenderers.cash')}</th>
              <th className="text-right font-medium">{t('playgroundRenderers.shares')}</th>
              <th className="text-right font-medium">{t('playgroundRenderers.equity')}</th>
            </tr>
          </thead>
          <tbody>
            {agents.map((a) => (
              <tr key={a.name} className="border-t border-gray-50">
                <td className="py-1 font-medium text-gray-800">{a.name}</td>
                <td className="py-1 text-right text-gray-600">{a.cash?.toFixed(2)}</td>
                <td className="py-1 text-right text-gray-600">{a.shares}</td>
                <td className="py-1 text-right font-semibold text-gray-900">{a.equity?.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {tape.length > 0 && (
        <div>
          <div className="text-[11px] font-bold text-gray-500 uppercase mb-1">{t('playgroundRenderers.recentTrades')}</div>
          <div className="space-y-0.5">
            {tape.slice().reverse().map((t, i) => (
              <div key={i} className="text-xs text-gray-600">
                <span className="font-semibold text-gray-900">{t.qty}</span> @{' '}
                <span className="font-semibold text-indigo-700">{t.price}</span>
                {' — '}{t.buyer} &larr; {t.seller}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function SocialView({ frame }) {
  const { t } = useI18n();
  if (!frame?.locations) return <Empty label={t('playgroundRenderers.noWorldStateYet')} />;
  return (
    <div className="space-y-4">
      <div className="text-xs text-gray-500">
        {t('playgroundRenderers.timeLine', { time: frame.time_of_day, hour: frame.hour })}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {frame.locations.map((loc) => (
          <div key={loc.name} className="rounded-lg border border-gray-200 p-3">
            <div className="text-sm font-bold text-gray-900 capitalize">{loc.name}</div>
            {loc.occupants.length === 0 ? (
              <div className="text-xs text-gray-400 italic mt-1">{t('playgroundRenderers.empty')}</div>
            ) : (
              <div className="flex flex-wrap gap-1 mt-1.5">
                {loc.occupants.map((o) => (
                  <span key={o} className="text-[11px] px-1.5 py-0.5 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-100">
                    {o}
                  </span>
                ))}
              </div>
            )}
            {loc.items?.length > 0 && (
              <div className="text-[11px] text-gray-500 mt-1.5">
                {t('playgroundRenderers.onTheFloor', { items: loc.items.join(', ') })}
              </div>
            )}
          </div>
        ))}
      </div>

      <div>
        <div className="text-[11px] font-bold text-gray-500 uppercase mb-1">{t('playgroundRenderers.characters')}</div>
        <div className="space-y-1.5">
          {(frame.agents || []).map((a) => (
            <div key={a.name} className="text-xs">
              <span className="font-semibold text-gray-900">{a.name}</span>
              <span className="text-gray-500">{' '}{t('playgroundRenderers.inLocation', { location: a.location })}</span>
              {a.inventory?.length > 0 && (
                <span className="text-gray-500">
                  {' · '}{t('playgroundRenderers.carrying', { items: a.inventory.join(', ') })}
                </span>
              )}
              {Object.keys(a.attitudes || {}).length > 0 && (
                <span className="text-gray-400">
                  {' · '}
                  {Object.entries(a.attitudes)
                    .map(([who, v]) => `${who}:${v > 0 ? '+' : ''}${v}`)
                    .join(' ')}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * An authored world.
 *
 * The same reading as the social world — who is where, holding what — plus the
 * two things only an authored world has: the values its author declared, and
 * the fixtures those values usually live on. The globals go first and largest:
 * they are the world's own state, the thing the author wrote the actions to
 * move, and watching them change is watching the world work.
 */
function CustomView({ frame }) {
  const { t } = useI18n();
  if (!frame?.locations) return <Empty label={t('playgroundRenderers.noWorldStateYet')} />;
  const globals = Object.entries(frame.globals || {});
  return (
    <div className="space-y-4">
      <div className="text-xs text-gray-500">
        {frame.time_of_day
          ? t('playgroundRenderers.timeLine', { time: frame.time_of_day, hour: frame.hour })
          : t('playgroundRenderers.hourLine', { hour: frame.hour })}
      </div>

      {globals.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {globals.map(([name, value]) => (
            <div key={name} className="rounded-lg border border-gray-200 px-3 py-1.5">
              <div className="text-[10px] font-bold text-gray-500 uppercase">{name}</div>
              <div className="text-sm font-bold text-gray-900">{String(value)}</div>
            </div>
          ))}
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {frame.locations.map((loc) => (
          <div key={loc.name} className="rounded-lg border border-gray-200 p-3">
            <div className="text-sm font-bold text-gray-900 capitalize">{loc.name}</div>
            {(loc.occupants || []).length === 0 ? (
              <div className="text-xs text-gray-400 italic mt-1">{t('playgroundRenderers.empty')}</div>
            ) : (
              <div className="flex flex-wrap gap-1 mt-1.5">
                {loc.occupants.map((o) => (
                  <span key={o} className="text-[11px] px-1.5 py-0.5 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-100">
                    {o}
                  </span>
                ))}
              </div>
            )}
            {loc.items?.length > 0 && (
              <div className="text-[11px] text-gray-500 mt-1.5">
                {t('playgroundRenderers.onTheFloor', { items: loc.items.join(', ') })}
              </div>
            )}
            {loc.entities?.length > 0 && (
              <div className="text-[11px] text-gray-400 mt-1">
                {t('playgroundRenderers.here', { things: loc.entities.join(', ') })}
              </div>
            )}
          </div>
        ))}
      </div>

      <div>
        <div className="text-[11px] font-bold text-gray-500 uppercase mb-1">
          {t('playgroundRenderers.characters')}
        </div>
        <div className="space-y-1.5">
          {(frame.agents || []).map((a) => (
            <div key={a.name} className="text-xs">
              <span className="font-semibold text-gray-900">{a.name}</span>
              {a.role && <span className="text-gray-400">{' '}({a.role})</span>}
              <span className="text-gray-500">{' '}{t('playgroundRenderers.inLocation', { location: a.location })}</span>
              {a.inventory?.length > 0 && (
                <span className="text-gray-500">
                  {' · '}{t('playgroundRenderers.carrying', { items: a.inventory.join(', ') })}
                </span>
              )}
              {Object.keys(a.stats || {}).length > 0 && (
                <span className="text-gray-400">
                  {' · '}
                  {Object.entries(a.stats).map(([k, v]) => `${k}:${v}`).join(' ')}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>

      {(frame.entities || []).length > 0 && (
        <div>
          <div className="text-[11px] font-bold text-gray-500 uppercase mb-1">
            {t('playgroundRenderers.entities')}
          </div>
          <div className="space-y-1">
            {frame.entities.map((e) => (
              <div key={e.name} className="text-xs text-gray-600">
                <span className="font-semibold text-gray-900">{e.name}</span>
                <span className="text-gray-400">{' '}({e.kind})</span>
                <span>{' '}{t('playgroundRenderers.inLocation', { location: e.location })}</span>
                {Object.keys(e.state || {}).length > 0 && (
                  <span className="text-gray-400">
                    {' · '}
                    {Object.entries(e.state).map(([k, v]) => `${k}=${v}`).join(' ')}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Status colours for a hypothesis or an experiment. Tailwind tokens only,
    so the palette switch recolours them with the rest of the page. */
const LAB_STATUS_STYLES = {
  proposed: 'bg-gray-100 text-gray-700 border-gray-200',
  testing: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  confirmed: 'bg-green-50 text-green-700 border-green-200',
  refuted: 'bg-red-50 text-red-700 border-red-200',
  needs_repeat: 'bg-amber-50 text-amber-700 border-amber-200',
  designed: 'bg-gray-100 text-gray-700 border-gray-200',
  running: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  done: 'bg-green-50 text-green-700 border-green-200',
  failed: 'bg-red-50 text-red-700 border-red-200',
};

function LabStatusChip({ status }) {
  const { t } = useI18n();
  return (
    <span
      data-testid="lab-status"
      className={`shrink-0 text-[10px] font-semibold px-1.5 py-0.5 rounded-full border ${
        LAB_STATUS_STYLES[status] || LAB_STATUS_STYLES.proposed
      }`}
    >
      {t(`playgroundRenderers.lab.status.${status}`, { defaultValue: status })}
    </span>
  );
}

function formatMetric(value) {
  if (typeof value !== 'number') return String(value);
  return Number.isInteger(value) ? String(value) : value.toPrecision(4);
}

function LabExperiment({ experiment }) {
  const { t } = useI18n();
  const metrics = Object.entries(experiment.metrics || {});
  const critique = (experiment.critiques || []).slice(-1)[0];
  return (
    <div className="rounded-md border border-gray-100 bg-gray-50 px-2 py-1.5 text-[11px] space-y-0.5">
      <div className="flex items-center gap-1.5">
        <span className="font-mono font-semibold text-gray-800">{experiment.id}</span>
        <LabStatusChip status={experiment.status} />
        {experiment.seed != null && (
          <span className="text-gray-500">{t('playgroundRenderers.lab.seed', { seed: experiment.seed })}</span>
        )}
        <span className="text-gray-400 truncate">{experiment.author}</span>
      </div>
      {experiment.design && <div className="text-gray-600">{experiment.design}</div>}
      {metrics.length > 0 && (
        <div className="font-mono text-gray-700">
          {metrics.map(([k, v]) => `${k}=${formatMetric(v)}`).join('  ')}
        </div>
      )}
      {experiment.result?.error && (
        <div className="text-red-600">{experiment.result.error}</div>
      )}
      {experiment.analysis && <div className="text-gray-600 italic">{experiment.analysis}</div>}
      {critique && (
        <div className="text-amber-700">
          {t('playgroundRenderers.lab.latestCritique', { author: critique.author })}{' '}
          {critique.text}
        </div>
      )}
    </div>
  );
}

function LabHypothesis({ node, experiments }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const mine = (node.experiments || []).map((id) => experiments[id]).filter(Boolean);
  return (
    <li data-testid="lab-hypothesis" data-depth={node.depth || 0}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-start gap-1.5 text-left py-1 hover:bg-gray-50 rounded"
        aria-expanded={open}
      >
        {open
          ? <ChevronDown className="w-3.5 h-3.5 mt-0.5 shrink-0 text-gray-400" />
          : <ChevronRight className="w-3.5 h-3.5 mt-0.5 shrink-0 text-gray-400" />}
        <span className="font-mono text-[11px] font-semibold text-gray-500 mt-0.5">{node.id}</span>
        <span className="flex-1 text-xs text-gray-800">{node.text}</span>
        <LabStatusChip status={node.status} />
      </button>
      {open && (
        <div className="ml-5 mb-1 space-y-1">
          {node.decision_note && (
            <div className="text-[11px] text-gray-500">
              {t('playgroundRenderers.lab.decidedBy', { who: node.decided_by })}{' '}
              {node.decision_note}
            </div>
          )}
          {mine.length === 0
            ? <div className="text-[11px] text-gray-400 italic">{t('playgroundRenderers.lab.noExperiments')}</div>
            : mine.map((e) => <LabExperiment key={e.id} experiment={e} />)}
        </div>
      )}
      {(node.children || []).length > 0 && (
        <ul className="ml-4 pl-2 border-l border-gray-100">
          {node.children.map((c) => (
            <LabHypothesis key={c.id} node={c} experiments={experiments} />
          ))}
        </ul>
      )}
    </li>
  );
}

function LabSection({ section, text }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="border border-gray-100 rounded-md">
      <button
        type="button" onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-1.5 px-2 py-1.5 text-xs font-semibold text-gray-800"
        aria-expanded={open}
      >
        {open ? <ChevronDown className="w-3.5 h-3.5 text-gray-400" /> : <ChevronRight className="w-3.5 h-3.5 text-gray-400" />}
        {section}
      </button>
      {open && <div className="px-2 pb-2 text-xs text-gray-600 whitespace-pre-wrap">{text}</div>}
    </div>
  );
}

/**
 * The research lab: the question, the budget, the hypothesis tree with each
 * node's experiments on demand, the report and the formulas.
 */
export function LabView({ frame }) {
  const { t } = useI18n();
  if (!frame || !Array.isArray(frame.hypotheses)) {
    return <Empty label={t('playgroundRenderers.noWorldStateYet')} />;
  }
  const budget = frame.budget || {};
  const used = budget.experiments_used || 0;
  const max = budget.experiments_max || 0;
  const pct = max ? Math.min(100, Math.round((used / max) * 100)) : 0;
  const experiments = Object.fromEntries((frame.experiments || []).map((e) => [e.id, e]));
  return (
    <div className="space-y-4">
      {frame.question && (
        <div>
          <div className="text-[11px] font-bold text-gray-500 uppercase mb-0.5">{t('playgroundRenderers.lab.question')}</div>
          <div className="text-sm text-gray-900">{frame.question}</div>
        </div>
      )}

      <div>
        <div className="flex items-baseline justify-between text-[11px] mb-1">
          <span className="font-bold text-gray-500 uppercase">{t('playgroundRenderers.lab.budget')}</span>
          <span className="font-semibold text-gray-700" data-testid="lab-budget">
            {t('playgroundRenderers.lab.budgetUsed', { used, max })}
          </span>
        </div>
        <div className="h-1.5 rounded-full bg-gray-100 overflow-hidden">
          <div
            className={`h-full ${pct >= 100 ? 'bg-red-500' : pct >= 75 ? 'bg-amber-500' : 'bg-indigo-500'}`}
            style={{ width: `${pct}%` }}
          />
        </div>
        {frame.stop_reason && (
          <div className="mt-1 text-[11px] text-gray-500">
            {t(`playground.stopReason.${frame.stop_reason}`, { defaultValue: frame.stop_reason })}
          </div>
        )}
      </div>

      <div>
        <div className="text-[11px] font-bold text-gray-500 uppercase mb-1">{t('playgroundRenderers.lab.hypotheses')}</div>
        {frame.hypotheses.length === 0
          ? <div className="text-xs text-gray-400 italic">{t('playgroundRenderers.lab.noHypotheses')}</div>
          : (
            <ul className="space-y-0.5">
              {frame.hypotheses.map((h) => (
                <LabHypothesis key={h.id} node={h} experiments={experiments} />
              ))}
            </ul>
          )}
      </div>

      {(frame.report || []).length > 0 && (
        <div>
          <div className="text-[11px] font-bold text-gray-500 uppercase mb-1">{t('playgroundRenderers.lab.report')}</div>
          <div className="space-y-1">
            {frame.report.map((r) => <LabSection key={r.section} section={r.section} text={r.text} />)}
          </div>
        </div>
      )}

      {(frame.formulas || []).length > 0 && (
        <div>
          <div className="text-[11px] font-bold text-gray-500 uppercase mb-1">{t('playgroundRenderers.lab.formulas')}</div>
          <div className="space-y-1">
            {frame.formulas.map((f, i) => (
              <div key={i} className="text-xs">
                <div className="text-gray-500">{f.label}</div>
                <Suspense fallback={<code className="text-gray-700">{f.latex}</code>}>
                  <LatexView view={{ spec: { latex: f.latex } }} />
                </Suspense>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function JsonView({ frame }) {
  return (
    <pre className="text-xs text-gray-700 bg-gray-50 rounded-lg p-3 overflow-auto max-h-96">
      {JSON.stringify(frame, null, 2)}
    </pre>
  );
}

const RENDERERS = {
  market: MarketView, social: SocialView, custom: CustomView, lab: LabView,
};

export default function WorldView({ frame }) {
  const { t } = useI18n();
  if (!frame || Object.keys(frame).length === 0) {
    return <Empty label={t('playgroundRenderers.theWorldHasNotTicked')} />;
  }
  const Renderer = RENDERERS[frame.renderer] || JsonView;
  return <Renderer frame={frame} />;
}
