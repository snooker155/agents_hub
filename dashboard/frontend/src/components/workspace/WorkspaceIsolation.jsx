import { useState } from 'react';
import { Link } from 'react-router-dom';
import { CheckCircle, XCircle, Loader } from 'lucide-react';
import { updateWorkspaceIsolation } from '../../api/isolation';
import useWorkspaceIsolation, { setWorkspaceIsolationCache } from './useWorkspaceIsolation';
import { SectionCard, inputCls } from '../settingsUi';
import { Toggle } from '../settings/WorkspaceSettingsSections';
import { errorDetail } from '../toast';
import { useI18n } from '../../i18n';

const linesOf = (list) => (list || []).join('\n');
const listOf = (text) => text.split('\n').map((x) => x.trim()).filter(Boolean);

/**
 * The perimeter around a workspace (common/isolation.py, docs/isolation.md):
 * the switch, the hub's readiness checks, the sites an isolated run may
 * read from, and the agents this workspace owns that hold a tool from
 * outside the allowlist. Saves itself, like the other per workspace cards.
 */
export default function WorkspaceIsolation({ workspace }) {
  const { t } = useI18n();
  const iso = useWorkspaceIsolation(workspace);
  const data = iso.data;
  const [domainsEdit, setDomainsEdit] = useState(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [domainsSaved, setDomainsSaved] = useState(false);

  const savedDomains = linesOf(data?.allow_domains);
  const domains = domainsEdit ?? savedDomains;
  const domainsDirty = domains !== savedDomains;

  const applyUpdate = async (payload) => {
    setSaving(true);
    setError('');
    try {
      const { data: body } = await updateWorkspaceIsolation(workspace, payload);
      setWorkspaceIsolationCache(workspace, body);
      return true;
    } catch (e) {
      setError(errorDetail(e) || t('isolation.errors.save'));
      return false;
    } finally {
      setSaving(false);
    }
  };

  const toggleIsolated = async (next) => {
    if (!next) {
      if (!window.confirm(t('isolation.confirmOff'))) return;
    }
    await applyUpdate({ isolated: next });
  };

  const saveDomains = async () => {
    setDomainsSaved(false);
    const ok = await applyUpdate({ allow_domains: listOf(domains) });
    if (ok) {
      setDomainsEdit(null);
      setDomainsSaved(true);
      setTimeout(() => setDomainsSaved(false), 3000);
    }
  };

  if (!data) {
    return (
      <SectionCard title={t('isolation.title')}>
        {iso.loading ? (
          <p className="text-sm text-gray-500 flex items-center gap-2">
            <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
          </p>
        ) : (
          <p className="text-sm text-red-600">{iso.error || t('isolation.errors.load')}</p>
        )}
      </SectionCard>
    );
  }

  return (
    <SectionCard title={t('isolation.title')}>
      <p className="text-sm text-gray-600">{t('isolation.explainer')}</p>

      <div className="flex items-center justify-between gap-3 pt-2 border-t border-gray-100">
        <div>
          <label className="text-sm font-medium text-gray-700">{t('isolation.switchLabel')}</label>
          <p className="text-xs text-gray-500 mt-1">
            {data.isolated ? t('isolation.statusOn') : t('isolation.statusOff')}
          </p>
        </div>
        <Toggle checked={!!data.isolated} disabled={saving} onChange={toggleIsolated} />
      </div>

      {error && (
        <p className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2" data-testid="isolation-error">
          {error}
        </p>
      )}

      <div>
        <p className="text-sm font-medium text-gray-700 mb-2">{t('isolation.readiness')}</p>
        <ul className="divide-y divide-gray-100 rounded-lg border border-gray-200" data-testid="isolation-readiness">
          {(data.readiness || []).map((check) => (
            <li key={check.id} className="flex items-start gap-2 px-3 py-2 text-sm" data-testid={`isolation-check-${check.id}`}>
              {check.ok
                ? <CheckCircle className="w-4 h-4 text-green-600 mt-0.5 shrink-0" />
                : <XCircle className="w-4 h-4 text-red-600 mt-0.5 shrink-0" />}
              <div className="min-w-0">
                <div className={check.ok ? 'text-gray-800 font-medium' : 'text-red-700 font-medium'}>
                  {t(`isolation.checks.${check.id}`, { defaultValue: check.id })}
                </div>
                <div className="text-xs text-gray-500">{check.detail}</div>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div>
        <label className="block text-sm font-medium text-gray-700 mb-1">{t('isolation.domains')}</label>
        <p className="text-xs text-gray-500 mb-1">{t('isolation.domainsHint')}</p>
        <textarea
          value={domains}
          onChange={(e) => setDomainsEdit(e.target.value)}
          placeholder={'wikipedia.org\napi.example.com'}
          rows={6}
          spellCheck={false}
          className={`${inputCls} font-mono text-xs`}
          aria-label={t('isolation.domains')}
        />
        <div className="mt-2 flex items-center gap-3">
          <button
            type="button"
            onClick={saveDomains}
            disabled={saving || !domainsDirty}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
          >
            {saving ? t('common.saving') : t('isolation.saveDomains')}
          </button>
          {domainsSaved && <span className="text-xs text-green-700">{t('isolation.domainsSaved')}</span>}
        </div>
      </div>

      {data.offending_agents?.length > 0 && (
        <div>
          <p className="text-sm font-medium text-gray-700 mb-1">{t('isolation.offendingAgents')}</p>
          <p className="text-xs text-gray-500 mb-2">{t('isolation.offendingAgentsHint')}</p>
          <ul className="divide-y divide-gray-100 rounded-lg border border-amber-200 bg-amber-50" data-testid="isolation-offending">
            {data.offending_agents.map((a) => (
              <li key={a.agent_id} className="px-3 py-2 text-sm flex flex-wrap items-center gap-2">
                <Link to={`/agents/${encodeURIComponent(a.agent_id)}`} className="font-medium text-indigo-600 hover:text-indigo-800">
                  {a.agent_id}
                </Link>
                <span className="text-xs text-gray-600 font-mono">{(a.tools || []).join(', ')}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </SectionCard>
  );
}
