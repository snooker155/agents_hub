import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { KeyRound, Loader, Plus, X } from 'lucide-react';
import { getAgentSecrets, getWorkspaceSecrets, updateAgentSecrets } from '../../api';
import { useWorkspace } from '../workspace';
import { useI18n } from '../../i18n';

const NAME_RE = /^[A-Z][A-Z0-9_]{0,63}$/;

/**
 * The secrets this agent may receive: an allowlist of names, saved on the
 * agent definition (`AgentSpec.secrets`, see docs/secrets.md).
 *
 * A run of the agent, in a subprocess, a container or the chat, gets only
 * these names from the workspace's secrets. The list is what the capability
 * guard counts as reading private data, so saving it can be refused with the
 * same 409 the tools list gets; the message is shown as it comes back.
 *
 * The names the current workspace actually holds are offered as one-click
 * suggestions, but any valid name may be declared: a workspace that does not
 * hold it yet simply hands out nothing for it.
 */
export default function AgentSecretsCard({ agentId, readOnly = false }) {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const [names, setNames] = useState([]);
  const [saved, setSaved] = useState([]);
  const [available, setAvailable] = useState([]);
  const [draft, setDraft] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getAgentSecrets(agentId)
      .then(({ data }) => {
        if (cancelled) return;
        const list = Array.isArray(data?.secrets) ? data.secrets : [];
        setNames(list);
        setSaved(list);
      })
      .catch(() => { if (!cancelled) { setNames([]); setSaved([]); } })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [agentId]);

  useEffect(() => {
    const workspace = selectedWorkspace || 'default';
    getWorkspaceSecrets(workspace)
      .then(({ data }) => {
        const items = Array.isArray(data) ? data : (data?.secrets || []);
        setAvailable([...new Set(items.map((s) => s.name).filter(Boolean))]);
      })
      .catch(() => setAvailable([]));
  }, [selectedWorkspace]);

  const dirty = useMemo(
    () => names.length !== saved.length || names.some((n, i) => n !== saved[i]),
    [names, saved],
  );

  const add = useCallback((raw) => {
    const name = String(raw || '').trim().toUpperCase();
    if (!name) return;
    if (!NAME_RE.test(name)) {
      setError(t('agentDetails.secretsBadName'));
      return;
    }
    setError('');
    setNames((prev) => (prev.includes(name) ? prev : [...prev, name]));
    setDraft('');
  }, [t]);

  const remove = (name) => setNames((prev) => prev.filter((n) => n !== name));

  const save = async () => {
    setSaving(true);
    setMessage('');
    setError('');
    try {
      const { data } = await updateAgentSecrets(agentId, names);
      const list = Array.isArray(data?.secrets) ? data.secrets : names;
      setNames(list);
      setSaved(list);
      setMessage(t('agentDetails.secretsSaved'));
    } catch (err) {
      setError(err?.response?.data?.detail || t('agentDetails.secretsSaveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const suggestions = available.filter((n) => !names.includes(n));

  return (
    <div className="bg-white p-6 shadow-md rounded-lg" data-testid="agent-secrets">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <KeyRound className="w-5 h-5 text-indigo-600" />
          <h3 className="text-lg font-bold text-gray-900">{t('agentDetails.secrets')}</h3>
        </div>
        {!readOnly && (
          <button
            type="button"
            onClick={save}
            disabled={saving || !dirty}
            className={`px-4 py-2 rounded-lg text-sm font-semibold ${
              saving || !dirty
                ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                : 'bg-indigo-600 text-white hover:bg-indigo-700'
            }`}
          >
            {saving ? t('common.saving') : t('agentDetails.saveSecrets')}
          </button>
        )}
      </div>
      <p className="text-sm text-gray-600 mb-4">{t('agentDetails.secretsIntro')}</p>

      {loading ? (
        <Loader className="w-4 h-4 animate-spin text-gray-400" />
      ) : (
        <>
          <div className="flex flex-wrap gap-2 mb-3">
            {names.length === 0 && (
              <span className="text-sm text-gray-500 italic">{t('agentDetails.secretsNone')}</span>
            )}
            {names.map((name) => (
              <span key={name}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-mono font-semibold bg-amber-50 text-amber-800 border border-amber-200">
                {name}
                {!readOnly && (
                  <button type="button" onClick={() => remove(name)}
                    title={t('common.remove')} className="text-amber-700 hover:text-amber-900">
                    <X className="w-3 h-3" />
                  </button>
                )}
              </span>
            ))}
          </div>

          {!readOnly && (
            <form
              className="flex items-center gap-2 mb-3"
              onSubmit={(e) => { e.preventDefault(); add(draft); }}
            >
              <input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder={t('agentDetails.secretsPlaceholder')}
                aria-label={t('agentDetails.secretsPlaceholder')}
                className="border border-gray-200 rounded-lg px-3 py-1.5 text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none w-72"
              />
              <button type="submit"
                className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-200 text-sm font-medium text-gray-700 hover:bg-gray-50">
                <Plus className="w-3.5 h-3.5" /> {t('common.add')}
              </button>
            </form>
          )}

          {!readOnly && suggestions.length > 0 && (
            <div className="text-xs text-gray-500">
              <span className="mr-2">{t('agentDetails.secretsInWorkspace')}</span>
              {suggestions.map((name) => (
                <button key={name} type="button" onClick={() => add(name)}
                  className="font-mono mr-2 underline decoration-dotted hover:text-gray-800">
                  {name}
                </button>
              ))}
            </div>
          )}

          {error && (
            <p className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2 mt-3">{error}</p>
          )}
          {message && <p className="text-xs text-gray-600 mt-3">{message}</p>}
        </>
      )}
    </div>
  );
}
