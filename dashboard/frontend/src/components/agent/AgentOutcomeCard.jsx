import { useEffect, useState } from 'react';
import { Target, Pencil, Trash2, Loader, Plus } from 'lucide-react';
import { getAgentDefaultOutcome, updateAgentDefaultOutcome } from '../../api/agentOutcome';
import { getModelsCatalog } from '../../api';
import { useI18n } from '../../i18n';

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

/**
 * AgentOutcomeCard: the agent's own default outcome (agents/registry.py
 * `default_outcome`, routes/agent_outcome.py), the same rubric shape a task
 * carries (tasks/outcome.py). A task picks this up the first time it is
 * assigned the agent and has no outcome of its own yet
 * (`tasks.service.assign_executor`); editing it here never touches a task
 * that already has one. See docs/outcomes.md, "An agent's default outcome".
 */
export default function AgentOutcomeCard({ agentId, readOnly = false }) {
  const { t } = useI18n();
  const [outcome, setOutcome] = useState(null);
  // The agent id the outcome was last loaded for; loading is derived from it.
  const [loadedFor, setLoadedFor] = useState(null);
  const loading = loadedFor !== agentId;
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState(formFrom(null));
  const [models, setModels] = useState([]);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    getAgentDefaultOutcome(agentId)
      .then(({ data }) => { if (!cancelled) setOutcome(data?.default_outcome || null); })
      .catch(() => { if (!cancelled) setError(t('agentOutcome.loadFailed')); })
      .finally(() => { if (!cancelled) setLoadedFor(agentId); });
    return () => { cancelled = true; };
  }, [agentId, t]);

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
      const { data } = await updateAgentDefaultOutcome(agentId, {
        rubric: form.rubric,
        max_iterations: Number(form.max_iterations) || DEFAULT_MAX_ITERATIONS,
        grader: form.grader || null,
        threshold: form.threshold === '' || form.threshold === null ? null : Number(form.threshold),
      });
      setOutcome(data?.default_outcome || null);
      setEditing(false);
    } catch (e) {
      setError(e?.response?.data?.detail || t('outcomes.saveFailed'));
    } finally {
      setBusy('');
    }
  };

  const remove = async () => {
    if (!window.confirm(t('agentOutcome.removeConfirm'))) return;
    setBusy('remove'); setError('');
    try {
      const { data } = await updateAgentDefaultOutcome(agentId, {});
      setOutcome(data?.default_outcome || null);
      setEditing(false);
    } catch (e) {
      setError(e?.response?.data?.detail || t('outcomes.removeFailed'));
    } finally {
      setBusy('');
    }
  };

  const graderOptions = form.grader && !models.includes(form.grader) ? [form.grader, ...models] : models;

  if (loading) {
    return (
      <div className="bg-white p-6 shadow-md rounded-lg" data-testid="agent-outcome">
        <Loader className="w-4 h-4 animate-spin text-gray-400" />
      </div>
    );
  }

  if (!outcome && !editing) {
    return (
      <div className="bg-white shadow-sm border border-gray-200 rounded-xl px-4 py-3 flex items-center gap-3 flex-wrap"
        data-testid="agent-outcome">
        <Target className="w-4 h-4 text-gray-400 shrink-0" />
        <span className="text-sm font-semibold text-gray-800">{t('agentOutcome.title')}</span>
        <span className="text-xs text-gray-500">{t('agentOutcome.noneHint')}</span>
        {!readOnly && (
          <button type="button" onClick={startEdit}
            className="ml-auto inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50">
            <Plus className="w-3.5 h-3.5" /> {t('outcomes.add')}
          </button>
        )}
        {error && <p className="w-full text-sm text-red-600">{error}</p>}
      </div>
    );
  }

  return (
    <div className="bg-white shadow-sm border border-gray-200 rounded-xl p-5" data-testid="agent-outcome">
      <div className="flex items-center gap-3 flex-wrap">
        <Target className="w-4 h-4 text-indigo-600 shrink-0" />
        <h3 className="text-sm font-bold text-gray-900">{t('agentOutcome.title')}</h3>
        {outcome && !editing && (
          <span className="text-xs text-gray-500">
            {[
              t('agentOutcome.upToAttempts', { max: outcome.max_iterations }),
              outcome.threshold !== null && outcome.threshold !== undefined
                ? t('outcomes.thresholdLabel', { value: outcome.threshold })
                : t('outcomes.thresholdAll'),
            ].filter(Boolean).join(' · ')}
          </span>
        )}
        {outcome && !editing && !readOnly && (
          <button type="button" onClick={startEdit}
            className="ml-auto inline-flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50">
            <Pencil className="w-3.5 h-3.5" /> {t('outcomes.edit')}
          </button>
        )}
      </div>
      <p className="text-xs text-gray-500 mt-1">{t('agentOutcome.hint')}</p>

      {editing ? (
        <div className="mt-4 space-y-3">
          <div>
            <label htmlFor={`agent-outcome-rubric-${agentId}`} className="block text-xs font-semibold text-gray-600 mb-1">
              {t('outcomes.rubric')}
            </label>
            <textarea
              id={`agent-outcome-rubric-${agentId}`}
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
              <label htmlFor={`agent-outcome-max-${agentId}`} className="block text-xs font-semibold text-gray-600 mb-1">
                {t('outcomes.maxIterations')}
              </label>
              <input
                id={`agent-outcome-max-${agentId}`}
                type="number" min={1} max={10}
                value={form.max_iterations}
                onChange={(e) => setForm({ ...form, max_iterations: e.target.value })}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
              />
            </div>
            <div>
              <label htmlFor={`agent-outcome-grader-${agentId}`} className="block text-xs font-semibold text-gray-600 mb-1">
                {t('outcomes.grader')}
              </label>
              <select
                id={`agent-outcome-grader-${agentId}`}
                value={form.grader}
                onChange={(e) => setForm({ ...form, grader: e.target.value })}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
              >
                <option value="">{t('outcomes.graderDefault')}</option>
                {graderOptions.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor={`agent-outcome-threshold-${agentId}`} className="block text-xs font-semibold text-gray-600 mb-1">
                {t('outcomes.threshold')}
              </label>
              <input
                id={`agent-outcome-threshold-${agentId}`}
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
            <button type="button" onClick={save} disabled={!!busy}
              className="inline-flex items-center gap-1 px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50">
              {busy === 'save' && <Loader className="w-3.5 h-3.5 animate-spin" />}
              {busy === 'save' ? t('outcomes.saving') : t('outcomes.save')}
            </button>
            <button type="button" onClick={() => { setEditing(false); setError(''); }}
              className="px-4 py-2 rounded-lg border border-gray-300 text-sm text-gray-700 hover:bg-gray-50">
              {t('outcomes.cancel')}
            </button>
            {outcome && (
              <button type="button" onClick={remove} disabled={!!busy}
                className="ml-auto inline-flex items-center gap-1 px-3 py-2 rounded-lg text-sm text-red-700 hover:bg-red-50 disabled:opacity-50">
                <Trash2 className="w-3.5 h-3.5" /> {t('outcomes.remove')}
              </button>
            )}
          </div>
        </div>
      ) : (
        outcome?.rubric && (
          <pre className="mt-3 text-xs text-gray-700 whitespace-pre-wrap bg-gray-50 border border-gray-200 rounded-lg p-3 max-h-40 overflow-auto">
            {outcome.rubric}
          </pre>
        )
      )}
      {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
    </div>
  );
}
