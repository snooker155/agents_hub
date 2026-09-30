import React from 'react';
import { Gauge } from 'lucide-react';
import { useI18n } from '../../i18n';
import { runConfig } from './config';

/** How the run itself is doing — a readout, which is why it is in the sidebar. */
export function RunMeters({ run, ticks, scenario }) {
  const { t } = useI18n();
  const tokens = ticks.reduce(
    (sum, tk) => sum + (tk.decisions || []).reduce(
      (n, d) => n + (d.inbound_tokens || 0) + (d.outbound_tokens || 0), 0,
    ),
    0,
  );
  const calls = ticks.reduce((n, tk) => n + (tk.decisions || []).length, 0);
  // Everything the run is measured *against* comes from its own snapshot: the
  // scenario is edited in place, so reading a finished run against the live row
  // would show it capped at a number it never ran with and cast with roles it
  // never had.
  const config = runConfig(run, scenario);
  const rows = [
    [t('playground.meters.activation'), t(`playground.activation.${run.activation || config.activation || 'synchronous'}`)],
    [t('playground.meters.ticks'), `${run.ticks_done || 0} / ${config.max_ticks}`],
    [t('playground.meters.calls'), calls],
    [t('playground.meters.tokens'), tokens.toLocaleString()],
    [t('playground.meters.cost'), `$${(run.total_cost || 0).toFixed(4)}`],
    [t('playground.meters.agents'), (config.roles || []).length],
  ];
  if (run.stop_reason) {
    // A world that wrote its own ending says which one it reached; "terminal
    // state" is all the shipped environments can say, and all a user would
    // otherwise see of a condition they authored themselves.
    const ending = run.final_state?.ending;
    rows.push([t('playground.meters.endedBecause'),
      (run.stop_reason === 'terminal' && ending)
        ? ending
        : t(`playground.stopReason.${run.stop_reason}`, { defaultValue: run.stop_reason })]);
  }
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
      <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-3 flex items-center gap-1.5">
        <Gauge className="w-4 h-4 text-indigo-500" /> {t('playground.runMeters')}
      </h3>
      <dl className="space-y-1">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-2 text-[11px]">
            <dt className="text-gray-500">{label}</dt>
            <dd className="font-semibold text-gray-800 text-right">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export default RunMeters;
