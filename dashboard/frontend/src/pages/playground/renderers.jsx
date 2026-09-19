import React from 'react';
import { useI18n } from '../../i18n';

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

function JsonView({ frame }) {
  return (
    <pre className="text-xs text-gray-700 bg-gray-50 rounded-lg p-3 overflow-auto max-h-96">
      {JSON.stringify(frame, null, 2)}
    </pre>
  );
}

const RENDERERS = { market: MarketView, social: SocialView, custom: CustomView };

export default function WorldView({ frame }) {
  const { t } = useI18n();
  if (!frame || Object.keys(frame).length === 0) {
    return <Empty label={t('playgroundRenderers.theWorldHasNotTicked')} />;
  }
  const Renderer = RENDERERS[frame.renderer] || JsonView;
  return <Renderer frame={frame} />;
}
