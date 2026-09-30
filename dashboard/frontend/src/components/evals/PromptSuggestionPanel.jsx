/**
 * "Suggest prompt fix" on a finished eval run with failed cases on an agent
 * target (evals/prompt_suggest.py): builds a revised instructions.md from the
 * failed cells, shows it as a diff against the live prompt with the model's
 * rationale, and lets the user Apply (through the definition editor's own
 * path, so it is snapshotted, optionally re-running the set to compare) or
 * Dismiss. Renders nothing for a container run, a run with nothing failed,
 * or a run still in progress.
 */
import { useEffect, useState } from 'react';
import { Check, Lightbulb, Loader, X } from 'lucide-react';
import { getPromptSuggestions, suggestPromptFix, applyPromptSuggestion, dismissPromptSuggestion } from '../../api';
import { lineDiff } from '../../lib/lineDiff';
import { useI18n } from '../../i18n';

function hasFailures(summary) {
  return Object.values(summary || {}).some((s) => (s.total || 0) > (s.passed || 0));
}

export default function PromptSuggestionPanel({ evalRun, evalSet, onApplied }) {
  const { t } = useI18n();
  const [suggestions, setSuggestions] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [building, setBuilding] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [rerun, setRerun] = useState(false);
  const [error, setError] = useState('');
  const [appliedNote, setAppliedNote] = useState('');

  const runId = evalRun?.eval_run_id;
  const applicable = evalSet?.target_kind === 'agent' && evalRun?.status === 'completed';

  useEffect(() => {
    setSuggestions([]);
    setLoaded(false);
    setError('');
    setAppliedNote('');
    if (!runId || !applicable) return;
    (async () => {
      try {
        const { data } = await getPromptSuggestions(runId);
        setSuggestions(data.suggestions || []);
      } catch {
        setSuggestions([]);
      } finally {
        setLoaded(true);
      }
    })();
  }, [runId, applicable]);

  if (!applicable || !loaded) return null;

  const pending = suggestions.find((s) => s.status === 'pending');
  const decided = !pending && suggestions[0];

  const handleSuggest = async () => {
    setBuilding(true);
    setError('');
    try {
      const { data } = await suggestPromptFix(runId);
      setSuggestions((prev) => [data, ...prev]);
    } catch (err) {
      setError(err.response?.data?.detail || t('evals.suggest.buildFailed'));
    } finally {
      setBuilding(false);
    }
  };

  const handleApply = async () => {
    setDeciding(true);
    setError('');
    try {
      const { data } = await applyPromptSuggestion(pending.suggestion_id, { rerun });
      setSuggestions((prev) => prev.map((s) => (s.suggestion_id === data.suggestion.suggestion_id ? data.suggestion : s)));
      if (data.eval_run) {
        setAppliedNote(t('evals.suggest.appliedAndRerun'));
        onApplied?.(data.eval_run);
      } else {
        setAppliedNote(t('evals.suggest.applied'));
      }
    } catch (err) {
      setError(err.response?.data?.detail || t('evals.suggest.applyFailed'));
    } finally {
      setDeciding(false);
    }
  };

  const handleDismiss = async () => {
    setDeciding(true);
    setError('');
    try {
      const { data } = await dismissPromptSuggestion(pending.suggestion_id);
      setSuggestions((prev) => prev.map((s) => (s.suggestion_id === data.suggestion_id ? data : s)));
    } catch (err) {
      setError(err.response?.data?.detail || t('evals.suggest.dismissFailed'));
    } finally {
      setDeciding(false);
    }
  };

  if (!pending && !decided) {
    if (!hasFailures(evalRun.summary)) return null;
    return (
      <div className="mb-4 rounded-lg border border-indigo-100 bg-indigo-50/50 p-3" data-testid="prompt-suggestion-panel">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-gray-600 flex items-center gap-1.5">
            <Lightbulb className="w-4 h-4 text-indigo-600" /> {t('evals.suggest.hint')}
          </span>
          <button
            onClick={handleSuggest}
            disabled={building}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {building ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Lightbulb className="w-3.5 h-3.5" />}
            {t('evals.suggest.button')}
          </button>
        </div>
        {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      </div>
    );
  }

  const active = pending || decided;
  const diff = lineDiff(active.old_instructions, active.new_instructions);

  return (
    <div className="mb-4 rounded-lg border border-indigo-100 bg-indigo-50/50 p-3" data-testid="prompt-suggestion-panel">
      <div className="flex items-center justify-between gap-2 mb-2">
        <span className="text-xs font-semibold text-gray-700 flex items-center gap-1.5">
          <Lightbulb className="w-4 h-4 text-indigo-600" /> {t('evals.suggest.title')}
        </span>
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
          active.status === 'pending' ? 'bg-blue-100 text-blue-700'
          : active.status === 'applied' ? 'bg-green-100 text-green-700'
          : 'bg-gray-100 text-gray-600'
        }`}>
          {t(`evals.suggest.statuses.${active.status}`)}
        </span>
      </div>
      {active.rationale && (
        <p className="text-xs text-gray-600 mb-2 whitespace-pre-wrap">{active.rationale}</p>
      )}
      <pre className="text-xs font-mono bg-gray-900 text-gray-100 rounded p-2 overflow-x-auto max-h-56 mb-2">
        {diff.map((d, idx) => (
          <div
            key={idx}
            className={
              d.type === 'added' ? 'bg-emerald-900/40 text-emerald-300'
                : d.type === 'removed' ? 'bg-red-900/40 text-red-300 line-through'
                  : ''
            }
          >
            {(d.type === 'added' ? '+ ' : d.type === 'removed' ? '- ' : '  ') + d.text}
          </div>
        ))}
      </pre>
      {error && <p className="text-xs text-red-600 mb-2">{error}</p>}
      {appliedNote && <p className="text-xs text-green-700 mb-2">{appliedNote}</p>}
      {pending && (
        <div className="flex items-center gap-2 flex-wrap">
          <label className="flex items-center gap-1.5 text-xs text-gray-600">
            <input type="checkbox" checked={rerun} onChange={(e) => setRerun(e.target.checked)} />
            {t('evals.suggest.rerunLabel')}
          </label>
          <button
            onClick={handleApply}
            disabled={deciding}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {deciding ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
            {t('evals.suggest.apply')}
          </button>
          <button
            onClick={handleDismiss}
            disabled={deciding}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
          >
            <X className="w-3.5 h-3.5" /> {t('evals.suggest.dismiss')}
          </button>
        </div>
      )}
    </div>
  );
}
