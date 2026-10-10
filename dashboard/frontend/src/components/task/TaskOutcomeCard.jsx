import React, { useEffect, useMemo, useState } from 'react';
import {
  Target, Pencil, Trash2, Loader, Play, CheckCircle2, XCircle, AlertTriangle,
  ChevronDown, ChevronRight, Plus,
} from 'lucide-react';
import { getModelsCatalog } from '../../api';
import { setTaskOutcome, deleteTaskOutcome, gradeTaskOutcome } from '../../api/outcomes';
import { useI18n } from '../../i18n';

/**
 * TaskOutcomeCard: the task's definition of done (tasks/outcome.py).
 *
 * An outcome is a markdown rubric an independent model grades every finished
 * agent run against, criterion by criterion. Unmet criteria go back to the
 * same agent for another attempt, up to the outcome's limit, after which the
 * task is blocked for a person. The card sets and edits the rubric, its
 * attempt limit, grader model and pass threshold, lists every grading newest
 * first with its per-criterion feedback, and grades the latest run on request.
 * Without an outcome it stays a one-line affordance.
 */

const DEFAULT_MAX_ITERATIONS = 3;

const graderRef = (g) => {
  if (!g) return '';
  if (typeof g === 'string') return g;
  return g.model ? `${g.provider ? `${g.provider}/` : ''}${g.model}` : '';
};

const formFrom = (outcome) => ({
  rubric: outcome?.rubric || '',
  max_iterations: outcome?.max_iterations ?? DEFAULT_MAX_ITERATIONS,
  grader: graderRef(outcome?.grader),
  threshold: outcome?.threshold ?? '',
});

const isAttempt = (ev) => (ev?.trigger || 'run') === 'run';

function StatusBadge({ evaluation, t }) {
  if (!evaluation) return null;
  if (evaluation.error) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-amber-100 text-amber-800">
        <AlertTriangle className="w-3 h-3" /> {t('outcomes.graderError')}
      </span>
    );
  }
  return evaluation.passed ? (
    <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-green-100 text-green-700">
      <CheckCircle2 className="w-3 h-3" /> {t('outcomes.met')}
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full bg-red-100 text-red-700">
      <XCircle className="w-3 h-3" /> {t('outcomes.notMet')}
    </span>
  );
}

function EvaluationRow({ evaluation, defaultOpen, t }) {
  const [open, setOpen] = useState(defaultOpen);
  const Chevron = open ? ChevronDown : ChevronRight;
  const score = Math.round((Number(evaluation.score) || 0) * 100);
  const cost = Number(evaluation.cost_usd) || 0;
  const model = graderRef(evaluation.grader);
  return (
    <li className="border border-gray-200 rounded-lg overflow-hidden" data-testid="outcome-evaluation">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-3 py-2 text-left hover:bg-gray-50"
      >
        <Chevron className="w-4 h-4 text-gray-400 shrink-0" />
        <span className="text-sm font-semibold text-gray-900 shrink-0">
          {t('outcomes.iteration', { n: evaluation.iteration })}
        </span>
        <StatusBadge evaluation={evaluation} t={t} />
        {!evaluation.error && (
          <span className="text-xs font-semibold text-gray-700">{t('outcomes.score', { score })}</span>
        )}
        {!isAttempt(evaluation) && (
          <span className="text-xs text-gray-500">{t('outcomes.manual')}</span>
        )}
        <span className="ml-auto text-xs text-gray-400 shrink-0">${cost.toFixed(4)}</span>
      </button>
      {open && (
        <div className="px-4 py-3 border-t border-gray-100 bg-gray-50 space-y-2">
          {evaluation.error && (
            <p className="text-sm text-amber-800">{evaluation.error}</p>
          )}
          {(evaluation.criteria || []).length > 0 && (
            <ul className="space-y-1.5">
              {evaluation.criteria.map((c) => (
                <li key={c.name} className="flex items-start gap-2 text-sm" data-testid="outcome-criterion">
                  {c.passed
                    ? <CheckCircle2 className="w-4 h-4 text-green-600 shrink-0 mt-0.5" />
                    : <XCircle className="w-4 h-4 text-red-600 shrink-0 mt-0.5" />}
                  <div className="min-w-0">
                    <span className="font-medium text-gray-900">{c.name}</span>
                    <span className="ml-2 text-xs text-gray-500">
                      {t('outcomes.score', { score: Math.round((Number(c.score) || 0) * 100) })}
                    </span>
                    {c.feedback && (
                      <p className="text-gray-700 whitespace-pre-wrap">{c.feedback}</p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {evaluation.feedback && !evaluation.error && (
            <p className="text-sm text-gray-700">
              <span className="font-semibold">{t('outcomes.overall')}:</span> {evaluation.feedback}
            </p>
          )}
          <p className="text-xs text-gray-500">
            {[
              model && t('outcomes.gradedBy', { model }),
              evaluation.graded_at && new Date(evaluation.graded_at).toLocaleString(),
            ].filter(Boolean).join(' · ')}
          </p>
        </div>
      )}
    </li>
  );
}

export default function TaskOutcomeCard({ task, onChanged }) {
  const { t } = useI18n();
  const taskId = task?.id;
  const [outcome, setOutcome] = useState(task?.outcome || null);
  const [evaluations, setEvaluations] = useState(task?.outcome_evaluations || []);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(formFrom(task?.outcome));
  const [models, setModels] = useState([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  // The page refetches the task after every change and on live updates; follow
  // it. The form keeps its own copy, so a refresh never wipes what is being typed.
  const taskOutcomeKey = JSON.stringify(task?.outcome || null);
  const taskEvalsKey = JSON.stringify(task?.outcome_evaluations || []);
  // Adjusted during render: the local copies restart from the task whenever
  // its outcome or evaluations change.
  const taskKey = `${taskOutcomeKey}|${taskEvalsKey}`;
  const [syncedKey, setSyncedKey] = useState(taskKey);
  if (syncedKey !== taskKey) {
    setSyncedKey(taskKey);
    setOutcome(task?.outcome || null);
    setEvaluations(task?.outcome_evaluations || []);
  }

  useEffect(() => {
    if (!editing || models.length) return;
    getModelsCatalog()
      .then(({ data }) => {
        const providers = data?.providers || {};
        setModels(Object.entries(providers).flatMap(([provider, entry]) => (
          (entry?.models || []).filter((m) => m.enabled).map((m) => `${provider}/${m.id}`)
        )));
      })
      .catch(() => { /* the default grader stays available without a catalogue */ });
  }, [editing, models.length]);

  const newestFirst = useMemo(() => [...evaluations].reverse(), [evaluations]);
  const attempts = evaluations.filter(isAttempt).length;
  const latest = evaluations.length ? evaluations[evaluations.length - 1] : null;

  const apply = (data) => {
    setOutcome(data?.outcome || null);
    setEvaluations(data?.evaluations || []);
    if (onChanged) onChanged();
  };

  const startEdit = () => {
    setForm(formFrom(outcome));
    setError('');
    setEditing(true);
  };

  const save = async () => {
    if (!form.rubric.trim()) {
      setError(t('outcomes.rubricRequired'));
      return;
    }
    setBusy('save'); setError('');
    try {
      const { data } = await setTaskOutcome(taskId, {
        rubric: form.rubric,
        max_iterations: Number(form.max_iterations) || DEFAULT_MAX_ITERATIONS,
        grader: form.grader || null,
        threshold: form.threshold === '' || form.threshold === null ? null : Number(form.threshold),
      });
      setEditing(false);
      apply(data);
    } catch (e) {
      setError(e?.response?.data?.detail || t('outcomes.saveFailed'));
    } finally {
      setBusy('');
    }
  };

  const remove = async () => {
    if (!window.confirm(t('outcomes.removeConfirm'))) return;
    setBusy('remove'); setError('');
    try {
      const { data } = await deleteTaskOutcome(taskId);
      setEditing(false);
      apply(data);
    } catch (e) {
      setError(e?.response?.data?.detail || t('outcomes.removeFailed'));
    } finally {
      setBusy('');
    }
  };

  const gradeNow = async () => {
    setBusy('grade'); setError('');
    try {
      const { data } = await gradeTaskOutcome(taskId);
      apply(data);
    } catch (e) {
      setError(e?.response?.data?.detail || t('outcomes.gradeFailed'));
    } finally {
      setBusy('');
    }
  };

  if (!task) return null;

  if (!outcome && !editing) {
    return (
      <div className="bg-white shadow-sm border border-gray-200 rounded-xl px-4 py-3 mb-6 flex items-center gap-3 flex-wrap">
        <Target className="w-4 h-4 text-gray-400 shrink-0" />
        <span className="text-sm font-semibold text-gray-800">{t('outcomes.title')}</span>
        <span className="text-xs text-gray-500">{t('outcomes.noneHint')}</span>
        <button
          type="button"
          onClick={startEdit}
          className="ml-auto inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50"
        >
          <Plus className="w-3.5 h-3.5" /> {t('outcomes.add')}
        </button>
        {error && <p className="w-full text-sm text-red-600">{error}</p>}
      </div>
    );
  }

  const graderOptions = form.grader && !models.includes(form.grader) ? [form.grader, ...models] : models;

  return (
    <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-5 mb-6" data-testid="task-outcome-card">
      <div className="flex items-center gap-3 flex-wrap">
        <Target className="w-4 h-4 text-indigo-600 shrink-0" />
        <h3 className="text-sm font-bold text-gray-900">{t('outcomes.title')}</h3>
        {outcome && (
          <span className="text-xs text-gray-500">
            {[
              latest?.criteria?.length > 0 && t('outcomes.criteria', { count: latest.criteria.length }),
              t('outcomes.attempts', { used: attempts, max: outcome.max_iterations }),
              outcome.threshold !== null && outcome.threshold !== undefined
                ? t('outcomes.thresholdLabel', { value: outcome.threshold })
                : t('outcomes.thresholdAll'),
            ].filter(Boolean).join(' · ')}
          </span>
        )}
        <StatusBadge evaluation={latest} t={t} />
        {outcome && !editing && (
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={gradeNow}
              disabled={!!busy}
              title={t('outcomes.gradeNowHint')}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50"
            >
              {busy === 'grade' ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
              {busy === 'grade' ? t('outcomes.grading') : t('outcomes.gradeNow')}
            </button>
            <button
              type="button"
              onClick={startEdit}
              className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50"
            >
              <Pencil className="w-3.5 h-3.5" /> {t('outcomes.edit')}
            </button>
          </div>
        )}
      </div>

      {editing ? (
        <div className="mt-4 space-y-3">
          <div>
            <label htmlFor="outcome-rubric" className="block text-xs font-semibold text-gray-600 mb-1">
              {t('outcomes.rubric')}
            </label>
            <textarea
              id="outcome-rubric"
              value={form.rubric}
              onChange={(e) => setForm({ ...form, rubric: e.target.value })}
              rows={6}
              placeholder={t('outcomes.rubricPlaceholder')}
              className="w-full text-sm font-mono border border-gray-300 rounded-lg px-3 py-2"
            />
            <p className="text-xs text-gray-500 mt-1">{t('outcomes.rubricHint')}</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label htmlFor="outcome-max" className="block text-xs font-semibold text-gray-600 mb-1">
                {t('outcomes.maxIterations')}
              </label>
              <input
                id="outcome-max"
                type="number" min={1} max={10}
                value={form.max_iterations}
                onChange={(e) => setForm({ ...form, max_iterations: e.target.value })}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
              />
            </div>
            <div>
              <label htmlFor="outcome-grader" className="block text-xs font-semibold text-gray-600 mb-1">
                {t('outcomes.grader')}
              </label>
              <select
                id="outcome-grader"
                value={form.grader}
                onChange={(e) => setForm({ ...form, grader: e.target.value })}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
              >
                <option value="">{t('outcomes.graderDefault')}</option>
                {graderOptions.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="outcome-threshold" className="block text-xs font-semibold text-gray-600 mb-1">
                {t('outcomes.threshold')}
              </label>
              <input
                id="outcome-threshold"
                type="number" min={0} max={1} step="0.05"
                value={form.threshold}
                placeholder={t('outcomes.thresholdAll')}
                onChange={(e) => setForm({ ...form, threshold: e.target.value })}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
              />
              <p className="text-[11px] text-gray-500 mt-1">{t('outcomes.thresholdHint')}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={save}
              disabled={!!busy}
              className="inline-flex items-center gap-1 px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50"
            >
              {busy === 'save' && <Loader className="w-3.5 h-3.5 animate-spin" />}
              {busy === 'save' ? t('outcomes.saving') : t('outcomes.save')}
            </button>
            <button
              type="button"
              onClick={() => { setEditing(false); setError(''); }}
              className="px-4 py-2 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50"
            >
              {t('outcomes.cancel')}
            </button>
            {outcome && (
              <button
                type="button"
                onClick={remove}
                disabled={!!busy}
                className="ml-auto inline-flex items-center gap-1 px-3 py-2 rounded-lg text-sm text-red-700 hover:bg-red-50 disabled:opacity-50"
              >
                <Trash2 className="w-3.5 h-3.5" /> {t('outcomes.remove')}
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="mt-3">
          {outcome?.rubric && (
            <pre className="text-xs text-gray-700 whitespace-pre-wrap bg-gray-50 border border-gray-200 rounded-lg p-3 max-h-40 overflow-auto">
              {outcome.rubric}
            </pre>
          )}
          {newestFirst.length === 0 ? (
            <p className="text-sm text-gray-500 mt-3">{t('outcomes.noEvaluations')}</p>
          ) : (
            <>
              <h4 className="text-xs font-bold uppercase tracking-wide text-gray-500 mt-4 mb-2">
                {t('outcomes.evaluations')}
              </h4>
              <ul className="space-y-2">
                {newestFirst.map((ev, i) => (
                  <EvaluationRow
                    key={`${ev.iteration}-${ev.graded_at || i}`}
                    evaluation={ev}
                    defaultOpen={i === 0}
                    t={t}
                  />
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
    </div>
  );
}
