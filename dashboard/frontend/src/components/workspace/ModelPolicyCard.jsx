import { useEffect, useMemo, useState } from 'react';
import { Loader, Save, Trash2 } from 'lucide-react';
import { getModelsCatalog, getWorkspaceModelPolicy, updateWorkspaceModelPolicy } from '../../api';
import { SectionCard } from '../settingsUi';
import { useAuth, canAdminister } from '../auth';
import { effortLabel } from '../effort';
import { errorDetail } from '../toast';
import { useI18n } from '../../i18n';
import {
  buildPolicyBody, formFromPolicy, groupByProvider, modelKey, policyFormDirty,
} from './modelPolicy';

const EFFORTS = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];
const fieldCls = 'border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:outline-none';

/** The enabled models of the catalog (GET /api/models) as [{provider, model}]. */
function enabledModels(providers) {
  const out = [];
  for (const [provider, entry] of Object.entries(providers || {})) {
    for (const m of entry?.models || []) {
      if (m.enabled) out.push({ provider, model: m.id });
    }
  }
  return out;
}

/**
 * The workspace's model policy (workspace/model_policy.py): which enabled
 * models members may choose here, the default among them and the settings it
 * fixes. An administrator edits it; anyone else reads a summary.
 */
export default function ModelPolicyCard({ workspace }) {
  const { t } = useI18n();
  const auth = useAuth();
  const admin = canAdminister(auth);
  const [policy, setPolicy] = useState(undefined);   // undefined = loading
  const [catalog, setCatalog] = useState([]);
  const [form, setForm] = useState(formFromPolicy(null));
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    getWorkspaceModelPolicy(workspace)
      .then(({ data }) => {
        if (!live) return;
        setPolicy(data.policy || null);
        setForm(formFromPolicy(data.policy));
      })
      .catch((e) => { if (live) { setPolicy(null); setError(errorDetail(e) || t('modelPolicy.errors.load')); } });
    return () => { live = false; };
  // The page keys this card by workspace, so a new workspace remounts it.
  // `t` changes with the language only.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace]);

  useEffect(() => {
    if (!admin) return undefined;
    let live = true;
    getModelsCatalog()
      .then(({ data }) => { if (live) setCatalog(enabledModels(data.providers)); })
      .catch(() => { if (live) setCatalog([]); });
    return () => { live = false; };
  }, [admin]);

  // The enabled models, plus any the policy still names that were disabled
  // since, so they stay visible (and can be unticked) instead of vanishing.
  const options = useMemo(() => {
    const known = new Set(catalog.map((m) => modelKey(m.provider, m.model)));
    const extra = form.selected
      .filter((key) => !known.has(key))
      .map((key) => ({ provider: key.slice(0, key.indexOf('/')), model: key.slice(key.indexOf('/') + 1), disabled: true }));
    return [...catalog, ...extra];
  }, [catalog, form.selected]);

  const toggle = (key) => setForm((f) => {
    const selected = f.selected.includes(key) ? f.selected.filter((k) => k !== key) : [...f.selected, key];
    return { ...f, selected, defaultKey: selected.includes(f.defaultKey) ? f.defaultKey : (selected[0] || '') };
  });

  const submit = async (body, message) => {
    setSaving(true);
    setError('');
    setSaved('');
    try {
      const { data } = await updateWorkspaceModelPolicy(workspace, body);
      setPolicy(data.policy || null);
      setForm(formFromPolicy(data.policy));
      setSaved(message);
      setTimeout(() => setSaved(''), 3000);
    } catch (e) {
      setError(errorDetail(e) || t('modelPolicy.errors.save'));
    } finally {
      setSaving(false);
    }
  };

  const save = () => {
    if (!form.selected.length) { setError(t('modelPolicy.errors.noneSelected')); return; }
    submit(buildPolicyBody(form), t('modelPolicy.saved'));
  };

  if (policy === undefined) {
    return (
      <SectionCard title={t('modelPolicy.title')}>
        <p className="text-sm text-gray-500 flex items-center gap-2"><Loader className="w-4 h-4 animate-spin" /> {t('modelPolicy.loading')}</p>
      </SectionCard>
    );
  }

  if (!admin) {
    const fixed = [
      policy?.temperature != null && [t('modelPolicy.temperature'), String(policy.temperature)],
      policy?.effort && [t('modelPolicy.effort'), effortLabel(t, policy.effort)],
      policy?.max_tokens != null && [t('modelPolicy.maxTokens'), String(policy.max_tokens)],
    ].filter(Boolean);
    return (
      <SectionCard title={t('modelPolicy.title')}>
        {error && <p className="text-xs text-red-600">{error}</p>}
        {!policy ? (
          <p className="text-sm text-gray-500" data-testid="model-policy-none">{t('modelPolicy.noPolicy')}</p>
        ) : (
          <div className="space-y-3 text-sm" data-testid="model-policy-summary">
            <p className="text-gray-600">{t('modelPolicy.explainer')}</p>
            {policy.inherited_from && (
              <p className="text-xs text-gray-500" data-testid="model-policy-inherited">{t('modelPolicy.inherited')}</p>
            )}
            {policy.lock_special_models && (
              <p className="text-xs text-gray-500">{t('modelPolicy.specialLocked')}</p>
            )}
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">{t('modelPolicy.allowedModels')}</p>
              <ul className="space-y-1">
                {policy.models.map((m) => {
                  const isDefault = m.provider === policy.default?.provider && m.model === policy.default?.model;
                  return (
                    <li key={modelKey(m.provider, m.model)} className="flex items-center gap-2">
                      <span className="font-mono text-gray-700">{m.provider} · {m.model}</span>
                      {isDefault && policy.models.length > 1 && (
                        <span className="px-1.5 py-0.5 rounded border text-[9px] font-medium bg-indigo-50 border-indigo-200 text-indigo-600 uppercase tracking-wider">{t('modelPolicy.defaultModel')}</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
            {fixed.length > 0 && (
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-1">{t('modelPolicy.fixedSettings')}</p>
                <ul className="space-y-0.5 text-gray-700">
                  {fixed.map(([label, value]) => <li key={label}>{label}{': '}<span className="font-medium">{value}</span></li>)}
                </ul>
              </div>
            )}
          </div>
        )}
      </SectionCard>
    );
  }

  const groups = groupByProvider(options);
  const dirty = policyFormDirty(form, policy);
  return (
    <SectionCard title={t('modelPolicy.title')}>
      <p className="text-sm text-gray-600">{t('modelPolicy.explainer')}</p>
      {!policy && <p className="text-xs text-gray-500">{t('modelPolicy.noPolicyAdmin')}</p>}
      {policy?.inherited_from && (
        <p className="text-xs text-gray-500" data-testid="model-policy-inherited">{t('modelPolicy.inheritedAdmin')}</p>
      )}

      <div>
        <p className="block text-sm font-medium text-gray-700 mb-2">{t('modelPolicy.allowedModels')}</p>
        {groups.length === 0 && <p className="text-xs text-gray-400">{t('modelPolicy.noEnabledModels')}</p>}
        <div className="space-y-3">
          {groups.map((group) => (
            <div key={group.provider}>
              <p className="text-[10px] font-semibold uppercase tracking-wider text-gray-400 mb-1">{group.provider}</p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1">
                {group.models.map((model) => {
                  const key = modelKey(group.provider, model);
                  const gone = !catalog.some((m) => m.provider === group.provider && m.model === model);
                  return (
                    <label key={key} className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer min-w-0">
                      <input type="checkbox" checked={form.selected.includes(key)} onChange={() => toggle(key)} />
                      <span className="font-mono truncate">{model}</span>
                      {gone && <span className="text-[10px] text-amber-600 shrink-0">{t('modelPolicy.notEnabled')}</span>}
                    </label>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>

      {form.selected.length > 1 && (
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1" htmlFor="policy-default">{t('modelPolicy.defaultModel')}</label>
          <select
            id="policy-default"
            value={form.defaultKey}
            onChange={(e) => setForm((f) => ({ ...f, defaultKey: e.target.value }))}
            className={fieldCls}
          >
            {form.selected.map((key) => <option key={key} value={key}>{key}</option>)}
          </select>
        </div>
      )}
      {form.selected.length === 1 && <p className="text-xs text-gray-500">{t('modelPolicy.defaultOnly')}</p>}

      <div>
        <p className="text-sm font-medium text-gray-700">{t('modelPolicy.fixedSettings')}</p>
        <p className="text-xs text-gray-500 mb-2">{t('modelPolicy.fixedSettingsHint')}</p>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs text-gray-600 mb-1" htmlFor="policy-temperature">{t('modelPolicy.temperature')}</label>
            <input
              id="policy-temperature" type="number" min="0" max="2" step="0.05"
              value={form.temperature} placeholder={t('modelPolicy.anyValue')}
              onChange={(e) => setForm((f) => ({ ...f, temperature: e.target.value }))}
              className={`${fieldCls} w-full`}
            />
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1" htmlFor="policy-effort">{t('modelPolicy.effort')}</label>
            <select
              id="policy-effort" value={form.effort}
              onChange={(e) => setForm((f) => ({ ...f, effort: e.target.value }))}
              className={`${fieldCls} w-full`}
            >
              <option value="">{t('modelPolicy.effortFree')}</option>
              {EFFORTS.map((e) => <option key={e} value={e}>{effortLabel(t, e)}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-600 mb-1" htmlFor="policy-max-tokens">{t('modelPolicy.maxTokens')}</label>
            <input
              id="policy-max-tokens" type="number" min="1" step="256"
              value={form.maxTokens} placeholder={t('modelPolicy.anyValue')}
              onChange={(e) => setForm((f) => ({ ...f, maxTokens: e.target.value }))}
              className={`${fieldCls} w-full`}
            />
          </div>
        </div>
      </div>

      <label className="flex items-start gap-2 text-sm text-gray-700 cursor-pointer">
        <input
          type="checkbox" className="mt-0.5" checked={form.lockSpecialModels !== false}
          onChange={(e) => setForm((f) => ({ ...f, lockSpecialModels: e.target.checked }))}
        />
        <span>
          {t('modelPolicy.lockSpecial')}
          <span className="block text-xs text-gray-500">{t('modelPolicy.lockSpecialHint')}</span>
        </span>
      </label>

      {error && (
        <p className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2" data-testid="model-policy-error">{error}</p>
      )}
      <div className="flex items-center gap-3">
        <button
          type="button" onClick={save} disabled={saving || !dirty}
          className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium ${
            dirty ? 'bg-indigo-600 text-white hover:bg-indigo-700' : 'bg-gray-100 text-gray-400 cursor-not-allowed'
          }`}
        >
          {saving ? <Loader className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {t('modelPolicy.save')}
        </button>
        {policy && !policy.inherited_from && (
          <button
            type="button" onClick={() => submit({ models: [] }, t('modelPolicy.removed'))} disabled={saving}
            className="flex items-center gap-1.5 text-sm text-red-500 hover:text-red-700 disabled:opacity-50"
          >
            <Trash2 className="w-4 h-4" /> {t('modelPolicy.remove')}
          </button>
        )}
        {saved && <span className="text-xs text-green-700">{saved}</span>}
      </div>
    </SectionCard>
  );
}
