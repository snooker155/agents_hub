import React from 'react';
import { KeySquare } from 'lucide-react';
import { useI18n } from '../../i18n';
import { toolDecisionsOf } from './toolDecisionsOf';

// The decision words of tools/permission_policy.py (run, deny, ask).
const TONES = {
  run: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  deny: 'bg-red-50 text-red-700 border-red-200',
  ask: 'bg-amber-50 text-amber-800 border-amber-300',
};

/**
 * What the tool policy decided for each gated call of the run
 * (`run.loop.tool_decisions`, tools/permission_policy.py Decision.loop_entry):
 * the tool, the mode it ran under, the decision, who made it and why. A
 * cached repeat of an earlier decision is marked as such.
 */
export default function ToolDecisions({ run }) {
  const { t } = useI18n();
  const decisions = toolDecisionsOf(run);
  const th = 'px-3 py-2 text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide';
  const td = 'px-3 py-2 align-top';
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-3" data-testid="tool-decisions">
      <div className="flex items-center gap-2">
        <KeySquare className="w-4 h-4 text-gray-400" />
        <h4 className="text-xs font-semibold text-gray-600 uppercase tracking-wide">
          {t('runLoop.toolDecisions')} ({decisions.length})
        </h4>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="border-b border-gray-100">
            <tr>
              <th className={th}>{t('runLoop.decisionTool')}</th>
              <th className={th}>{t('runLoop.decisionMode')}</th>
              <th className={th}>{t('runLoop.decisionResult')}</th>
              <th className={th}>{t('runLoop.decisionBy')}</th>
              <th className={th}>{t('runLoop.decisionReason')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {decisions.map((d, i) => (
              <tr key={i}>
                <td className={`${td} font-mono text-xs text-gray-800`}>{d.tool}</td>
                <td className={`${td} font-mono text-xs text-gray-500`}>{d.mode || '—'}</td>
                <td className={td}>
                  <span className={`inline-flex items-center px-1.5 py-0.5 rounded border text-[11px] font-medium whitespace-nowrap ${TONES[d.decision] || 'bg-gray-50 text-gray-600 border-gray-200'}`}>
                    {d.decision}
                  </span>
                  {d.cached && <span className="ml-1.5 text-[11px] text-gray-400">{t('runLoop.decisionCached')}</span>}
                </td>
                <td className={`${td} text-xs text-gray-500`}>{d.by || '—'}</td>
                <td className={`${td} text-xs text-gray-500 break-words`}>{d.reason || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
