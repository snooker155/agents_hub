import React, { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { ExternalLink, Loader, Maximize2, X } from 'lucide-react';
import { useI18n } from '../../i18n';
import ChatMarkdown from '../chat/ChatMarkdown';
import { StatusBadge } from './loopUi';
import { hasScore, rubricGrading, scoreColor, scoreText } from './loopFormat';

const H4 = 'text-[10px] font-bold uppercase tracking-wider text-gray-400 mb-0.5';

/**
 * A pass's whole output, over the page. Rendered into <body> so the chat's
 * overflow never clips it; Escape and a click outside close it.
 */
export function OutputModal({ title, text, onClose }) {
  const { t } = useI18n();
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => { e.stopPropagation(); onClose(); }}
      role="dialog" aria-modal="true" aria-label={title}
      data-testid="loop-output-modal"
    >
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 border-b border-gray-100 px-5 py-3">
          <h3 className="truncate text-sm font-semibold text-gray-900">{title}</h3>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label={t('common.close')}>
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="overflow-y-auto break-words px-5 py-4 text-sm text-gray-800"><ChatMarkdown content={text} /></div>
      </div>
    </div>,
    document.body,
  );
}

/**
 * One pass as a reply in the run's chat (LoopRunChat.jsx): its score, what
 * it produced and what the judge said about it. What the flow did on the
 * way, its work log, is in the column beside the chat (LoopPassLogs.jsx).
 * The output shows a few lines; the whole of it opens over the page.
 */
export default function PassEvaluation({ iteration, flowId, evaluating = false, className = '' }) {
  const { t } = useI18n();
  const [outputOpen, setOutputOpen] = useState(false);
  const grading = useMemo(() => rubricGrading(iteration.evaluator_raw), [iteration.evaluator_raw]);
  const running = iteration.status === 'running';
  const stopped = iteration.status === 'stopped';
  const accepted = iteration.verdict === 'stop';
  const output = iteration.output || '';

  return (
    <div className={className} data-testid={`loop-evaluation-${iteration.iteration}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-gray-100 px-4 py-2">
        <span className="text-sm font-semibold text-gray-900">{t('loops.iterationN', { n: iteration.iteration })}</span>
        {hasScore(iteration.score) ? (
          <span className={`inline-flex items-center gap-1 text-base font-bold ${scoreText(iteration.score)}`}>
            <span className={`h-2 w-2 rounded-full ${scoreColor(iteration.score)}`} />
            {Math.round(iteration.score)}/100
          </span>
        ) : <StatusBadge status={iteration.status} />}
        {iteration.verdict && (
          <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
            accepted ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'}`}>
            {accepted ? t('loops.accepted') : t('loops.anotherPass')}
          </span>
        )}
        <span className="ml-auto flex items-center gap-3 text-xs text-gray-500">
          {!running && <span>{((iteration.duration_ms || 0) / 1000).toFixed(1)}s</span>}
          {flowId && iteration.flow_run_id && (
            <Link
              to={`/flows/${encodeURIComponent(flowId)}/runs/${encodeURIComponent(iteration.flow_run_id)}`}
              className="inline-flex items-center gap-1 text-indigo-600 hover:text-indigo-800"
            >
              <ExternalLink className="h-3 w-3" /> {t('loops.openTheFlowRun')}
            </Link>
          )}
        </span>
      </div>

      <div className="space-y-2.5 px-4 py-3">
        {running && (
          <p className="flex items-center gap-1.5 text-sm text-gray-500">
            <Loader className="h-4 w-4 animate-spin" />
            {evaluating ? t('loops.evaluating') : t('loops.runningTheFlow')}
          </p>
        )}
        {stopped && <p className="text-xs italic text-gray-500">{t('loops.notJudgedStopped')}</p>}

        {!running && (
          <div>
            <h4 className={H4}>{t('loops.output')}</h4>
            {/* The start of it, formatted; the whole of it opens in a modal. */}
            {output ? (
              <div className="max-h-28 overflow-hidden break-words text-sm text-gray-700"><ChatMarkdown content={output} /></div>
            ) : (
              <p className="text-sm text-gray-700">{t('loops.noOutput')}</p>
            )}
            {output && (
              <button
                type="button" onClick={() => setOutputOpen(true)}
                className="mt-1 inline-flex items-center gap-1 text-[11px] font-medium text-indigo-600 hover:text-indigo-800"
                data-testid={`loop-output-open-${iteration.iteration}`}
              >
                <Maximize2 className="h-3 w-3" /> {t('loops.showAll')}
              </button>
            )}
          </div>
        )}

        {iteration.reason && (
          <div>
            <h4 className={H4}>{t('loops.evaluationComments')}</h4>
            <p className="whitespace-pre-wrap break-words text-sm text-gray-800">{iteration.reason}</p>
          </div>
        )}

        {(grading?.criteria || []).length > 0 && (
          <div>
            <h4 className={H4}>{t('outcomes.criteriaHeading')}</h4>
            <ul className="space-y-1">
              {grading.criteria.map((c) => (
                <li key={c.name} className="flex items-start gap-1.5 text-xs">
                  <span className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${c.passed ? 'bg-green-500' : 'bg-red-500'}`} />
                  <span className="min-w-0">
                    <span className="font-medium text-gray-900">{c.name}</span>
                    <span className="ml-1.5 text-gray-500">{Math.round((Number(c.score) || 0) * 100)}/100</span>
                    {c.feedback && <span className="block whitespace-pre-wrap text-gray-700">{c.feedback}</span>}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {iteration.feedback && (
          <div>
            <h4 className={H4}>{t('loops.fedIntoTheNextIteration')}</h4>
            <p className="whitespace-pre-wrap break-words text-sm text-gray-800">{iteration.feedback}</p>
          </div>
        )}

        {iteration.evaluator_agent && (
          <p className="text-[11px] text-gray-400">{t('loops.judgedBy', { agent: iteration.evaluator_agent })}</p>
        )}
      </div>

      {outputOpen && (
        <OutputModal
          title={`${t('loops.iterationN', { n: iteration.iteration })} · ${t('loops.output')}`}
          text={output}
          onClose={() => setOutputOpen(false)}
        />
      )}
    </div>
  );
}
