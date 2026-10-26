import React from 'react';
import { useI18n } from '../../i18n';
import { STATUS_STYLES, hasScore, scoreColor } from './loopFormat';

export function StatusBadge({ status, className = '' }) {
  const { t } = useI18n();
  return (
    <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${STATUS_STYLES[status] || 'bg-gray-100 text-gray-600'} ${className}`}>
      {t(`loops.statuses.${status}`, { defaultValue: status })}
    </span>
  );
}

/** The score bar chart: is the run converging or just spending? */
export function Trajectory({ iterations }) {
  const { t } = useI18n();
  const scored = iterations.filter((i) => hasScore(i.score));
  if (!scored.length) {
    return <p className="text-xs text-gray-500 italic">{t('loops.noScoresYetTheFirst')}</p>;
  }
  const best = Math.max(...scored.map((i) => i.score));
  return (
    <div className="flex items-end gap-2 h-24">
      {iterations.map((it) => (
        <div key={it.iteration} className="flex-1 flex flex-col items-center justify-end gap-1 min-w-[28px] max-w-[72px] h-full">
          <span className="text-[11px] font-semibold text-gray-700">
            {hasScore(it.score) ? Math.round(it.score) : '·'}
          </span>
          <div
            className={`w-full rounded-t ${scoreColor(it.score)} ${it.score === best ? 'ring-2 ring-indigo-400' : ''}`}
            style={{ height: `${Math.max(4, it.score ?? 0)}%` }}
            title={it.reason || ''}
          />
          <span className="text-[11px] text-gray-500">{it.iteration}</span>
        </div>
      ))}
    </div>
  );
}
