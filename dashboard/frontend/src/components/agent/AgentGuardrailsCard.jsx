/**
 * The guardrails checking this agent's runs: the ones already applying to
 * every agent that can see them, plus a picker over the ones scoped to
 * selected agents (AgentSpec.guardrails, saved through
 * PUT /api/agents/{id}/guardrails). Config tab, see docs/guardrails.md.
 */
import { useEffect, useMemo, useState } from 'react';
import { ShieldCheck, Loader } from 'lucide-react';
import { getGuardrails, updateAgentGuardrails } from '../../api/guardrails';
import { useWorkspace } from '../workspace';
import { useI18n } from '../../i18n';
import { StageBadge, KindBadge, ActionBadge } from '../guardrails/badges';

export default function AgentGuardrailsCard({ agentId, agent, onSaved }) {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const [guardrails, setGuardrails] = useState([]);
  const [selected, setSelected] = useState(agent?.guardrails || []);
  const [saved, setSaved] = useState(agent?.guardrails || []);
  // The workspace the list was fetched for: loading is derived from it.
  const [loadedFor, setLoadedFor] = useState(undefined);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const workspace = agent?.owner_workspace || selectedWorkspace || null;

  const loading = loadedFor !== workspace;

  useEffect(() => {
    let cancelled = false;
    getGuardrails(workspace, false)
      .then(({ data }) => { if (!cancelled) setGuardrails(data || []); })
      .catch(() => { if (!cancelled) setGuardrails([]); })
      .finally(() => { if (!cancelled) setLoadedFor(workspace); });
    return () => { cancelled = true; };
  }, [workspace]);

  // The record changed under us (saved elsewhere, another agent): start the
  // picker from it again. Adjusted while rendering, not in an effect.
  const [seenGuardrails, setSeenGuardrails] = useState(agent?.guardrails);
  if (agent?.guardrails !== seenGuardrails) {
    const ids = agent?.guardrails || [];
    setSeenGuardrails(agent?.guardrails);
    setSelected(ids);
    setSaved(ids);
  }

  const already = useMemo(() => guardrails.filter((g) => g.applies_to === 'all'), [guardrails]);
  const selectable = useMemo(() => guardrails.filter((g) => g.applies_to === 'selected'), [guardrails]);
  const dirty = selected.length !== saved.length || selected.some((id, i) => id !== saved[i]);

  const toggle = (id) => {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const save = async () => {
    setSaving(true);
    setMessage('');
    setError('');
    try {
      const { data } = await updateAgentGuardrails(agentId, selected);
      const ids = data?.guardrails || selected;
      setSelected(ids);
      setSaved(ids);
      setMessage(t('guardrails.agentCard.saved'));
      onSaved?.();
    } catch (err) {
      setError(err?.response?.data?.detail || t('guardrails.agentCard.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="bg-white p-6 shadow-md rounded-lg" data-testid="agent-guardrails">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-5 h-5 text-indigo-600" />
          <h3 className="text-lg font-bold text-gray-900">{t('guardrails.agentCard.title')}</h3>
        </div>
        <button type="button" onClick={save} disabled={saving || !dirty}
          className={`px-4 py-2 rounded-lg text-sm font-semibold ${
            saving || !dirty
              ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
              : 'bg-indigo-600 text-white hover:bg-indigo-700'
          }`}>
          {saving ? t('common.saving') : t('guardrails.agentCard.save')}
        </button>
      </div>
      <p className="text-sm text-gray-600 mb-4">{t('guardrails.agentCard.description')}</p>

      {loading ? (
        <Loader className="w-4 h-4 animate-spin text-gray-400" />
      ) : (
        <div className="space-y-4">
          {already.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">
                {t('guardrails.agentCard.alreadyApplying')}
              </h4>
              <div className="flex flex-wrap gap-2">
                {already.map((g) => (
                  <span key={g.id} className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs bg-gray-50 border border-gray-200">
                    {g.name}
                    <StageBadge stage={g.stage} t={t} />
                    <ActionBadge action={g.action} t={t} />
                  </span>
                ))}
              </div>
            </div>
          )}

          <div>
            <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">
              {t('guardrails.agentCard.selectable')}
            </h4>
            {selectable.length === 0 ? (
              <p className="text-sm text-gray-400 italic">{t('guardrails.agentCard.none')}</p>
            ) : (
              <div className="space-y-1.5">
                {selectable.map((g) => (
                  <label key={g.id} className="flex items-center gap-2.5 text-sm text-gray-700 cursor-pointer">
                    <input type="checkbox" checked={selected.includes(g.id)} onChange={() => toggle(g.id)}
                      className="h-4 w-4 rounded border-gray-300 text-indigo-600" />
                    <span className="font-medium">{g.name}</span>
                    <KindBadge kind={g.kind} t={t} />
                    <StageBadge stage={g.stage} t={t} />
                    <ActionBadge action={g.action} t={t} />
                  </label>
                ))}
              </div>
            )}
          </div>

          {error && (
            <p className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>
          )}
          {message && <p className="text-xs text-gray-600">{message}</p>}
        </div>
      )}
    </div>
  );
}
