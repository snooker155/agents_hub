/**
 * The guide itself, read and acted on: two groups of steps ("Set up the hub",
 * administrators only, and "Start using it", everyone), a progress bar, and
 * per-step actions. Used in the Assistant page's Setup tab (`guide` and
 * `act` come from useSetupGuide there); `onAsk(step)` sends a turn naming
 * the step, so this panel never talks to the assistant on its own.
 */
import { useState } from 'react';
import { CheckCircle2, Circle, Loader2, MinusCircle } from 'lucide-react';
import { useI18n } from '../../i18n';
import { useWelcomeTour } from '../docs/WelcomeTour';
import { setupStepText } from './useSetupGuide';

const STATUS_ICON = { done: CheckCircle2, skipped: MinusCircle, working: Loader2 };
const STATUS_CLASS = {
  done: 'text-emerald-500', skipped: 'text-gray-400', working: 'text-indigo-500 animate-spin', todo: 'text-gray-300',
};

function StepRow({ step, busy, onAsk, onSkip, onUnskip, onTour, t }) {
  const Icon = STATUS_ICON[step.status] || Circle;
  const title = setupStepText(t, step, 'title');
  const detail = step.detail || (step.status === 'todo' ? setupStepText(t, step, 'why') : '');
  // The tour runs in the browser, not through the assistant: its own button
  // replaces the usual do-it / skip pair, whatever the step's status.
  const isTour = step.id === 'tour';
  return (
    <li className="flex items-start gap-2 px-3 py-2 border-b border-gray-50 last:border-0" data-testid={`setup-step-${step.id}`}>
      <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${STATUS_CLASS[step.status] || STATUS_CLASS.todo}`} />
      <div className="flex-1 min-w-0">
        <p className="text-xs font-medium text-gray-800">{title}</p>
        {detail && <p className="text-[11px] text-gray-500 mt-0.5">{detail}</p>}
        <div className="mt-1 flex items-center gap-2">
          {isTour ? (
            <button type="button" onClick={onTour} className="text-[11px] font-medium text-indigo-600 hover:text-indigo-800">
              {t('setupGuide.takeTour')}
            </button>
          ) : step.status === 'todo' ? (
            <>
              <button type="button" disabled={busy} onClick={() => onAsk(step)}
                className="text-[11px] font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50">
                {t('setupGuide.doIt')}
              </button>
              {!step.required && (
                <button type="button" disabled={busy} onClick={onSkip}
                  className="text-[11px] font-medium text-gray-400 hover:text-gray-600 disabled:opacity-50">
                  {t('setupGuide.skip')}
                </button>
              )}
            </>
          ) : step.status === 'skipped' ? (
            <button type="button" disabled={busy} onClick={onUnskip}
              className="text-[11px] font-medium text-gray-400 hover:text-gray-600 disabled:opacity-50">
              {t('setupGuide.unskip')}
            </button>
          ) : null}
        </div>
      </div>
    </li>
  );
}

export default function SetupGuidePanel({ guide, act, onAsk }) {
  const { t } = useI18n();
  const tour = useWelcomeTour();
  // One step's skip/unskip in flight at a time; disables just its own row.
  const [busyStep, setBusyStep] = useState('');

  if (!guide) return null;

  const run = async (action, stepId) => {
    setBusyStep(stepId);
    try { await act(action, { step: stepId }); } finally { setBusyStep(''); }
  };

  const steps = guide.steps || [];
  const groups = [
    { id: 'setup', items: steps.filter((s) => s.group === 'setup') },
    { id: 'start', items: steps.filter((s) => s.group === 'start') },
  ].filter((g) => g.items.length);
  const pct = guide.total ? Math.round((guide.done / guide.total) * 100) : 0;

  return (
    <div className="flex flex-col h-full" data-testid="setup-guide-panel">
      <div className="px-3 py-2.5 border-b border-gray-100 shrink-0">
        <span className="text-xs font-medium text-gray-600">{t('setupGuide.progress', { done: guide.done, total: guide.total })}</span>
        <div className="mt-1.5 w-full h-1.5 bg-gray-100 rounded-full overflow-hidden">
          <div className="h-full bg-indigo-500 transition-all" style={{ width: `${pct}%` }} />
        </div>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {groups.map((g) => (
          <div key={g.id}>
            <p className="px-3 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-gray-400">
              {t(`setupGuide.groups.${g.id}`)}
            </p>
            <ul>
              {g.items.map((step) => (
                <StepRow
                  key={step.id}
                  step={step}
                  t={t}
                  busy={busyStep === step.id}
                  onAsk={onAsk}
                  onSkip={() => run('skip', step.id)}
                  onUnskip={() => run('unskip', step.id)}
                  onTour={() => tour.start()}
                />
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="px-3 py-2 border-t border-gray-100 shrink-0 flex items-center gap-2">
        <button type="button" onClick={() => act('restart')} className="text-xs font-medium text-gray-500 hover:text-gray-800">
          {t('setupGuide.restart')}
        </button>
        {guide.complete && (
          <button type="button" onClick={() => act('finish')}
            className="ml-auto text-xs font-medium px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700">
            {t('setupGuide.finish')}
          </button>
        )}
      </div>
    </div>
  );
}
