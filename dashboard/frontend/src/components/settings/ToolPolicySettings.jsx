import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle, Plus, Save, X } from 'lucide-react';
import { getModelsCatalog, getWorkspacePolicy, updateWorkspacePolicy } from '../../api/toolPolicy';
import { useI18n } from '../../i18n';

const MODES = ['always_allow', 'always_ask', 'auto'];
const NONE = '';

const fieldCls = 'border border-gray-200 rounded-lg px-2 py-1.5 text-sm focus:outline-none';

/**
 * The workspace's per-tool permission policy, inside the Settings tool policy
 * block (tools/permission_policy.py): a default mode for every tool ("*"),
 * per-tool overrides, and the model that decides "auto" calls.
 *
 * Saved through the same /workspaces/{name}/policy endpoint as the approval
 * gate and the hooks, but only the two keys this block owns, so it never
 * writes over the gate flag or the hook config edited next to it. An agent's
 * own tool policy (its Tools tab) wins over what is set here.
 */
export default function ToolPolicySettings({ workspace }) {
  const { t } = useI18n();
  const [policy, setPolicy] = useState({});
  const [model, setModel] = useState('');
  const [saved, setSaved] = useState({ policy: {}, model: '' });
  const [models, setModels] = useState([]);
  const [newTool, setNewTool] = useState('');
  const [newMode, setNewMode] = useState('always_ask');
  const [saving, setSaving] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  // Read through a ref so a new `t` (a language switch) does not reload and
  // throw away unsaved edits.
  const tRef = useRef(t);
  useLayoutEffect(() => { tRef.current = t; });

  // Promise chain rather than try/await: the lint rule cannot tell that no
  // state is set before the first await of an async function with a catch.
  const load = useCallback(() => {
    if (!workspace) return;
    getWorkspacePolicy(workspace)
      .then(({ data }) => {
        const loaded = data?.tool_policy || {};
        const loadedModel = data?.tool_policy_model || '';
        setError('');
        setPolicy(loaded);
        setModel(loadedModel);
        setSaved({ policy: loaded, model: loadedModel });
      })
      .catch((e) => {
        setError(e?.response?.data?.detail || tRef.current('toolPolicy.loadFailed'));
      });
  }, [workspace]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    // Only models the Models page has enabled are offered, the same list the
    // header picker is built from.
    getModelsCatalog()
      .then(({ data }) => {
        const providers = data?.providers || {};
        setModels(Object.entries(providers).flatMap(([provider, entry]) => (
          (entry?.models || []).filter((m) => m.enabled).map((m) => `${provider}/${m.id}`)
        )));
      })
      .catch(() => setModels([]));
  }, []);

  const overrides = useMemo(
    () => Object.entries(policy).filter(([tool]) => tool !== '*'),
    [policy],
  );
  const dirty = useMemo(() => {
    const a = policy;
    const b = saved.policy || {};
    const ka = Object.keys(a);
    return model !== saved.model || ka.length !== Object.keys(b).length || ka.some((k) => a[k] !== b[k]);
  }, [policy, model, saved]);
  const modelOptions = useMemo(
    () => (model && !models.includes(model) ? [model, ...models] : models),
    [model, models],
  );

  const setMode = (tool, mode) => {
    setDone(false);
    setPolicy((prev) => {
      const next = { ...prev };
      if (mode === NONE) delete next[tool];
      else next[tool] = mode;
      return next;
    });
  };

  const addOverride = (e) => {
    e.preventDefault();
    const tool = newTool.trim();
    if (!tool || tool === '*') {
      setError(t('toolPolicy.badToolId'));
      return;
    }
    setError('');
    setMode(tool, newMode);
    setNewTool('');
  };

  const save = async () => {
    setSaving(true);
    setError('');
    setDone(false);
    try {
      const { data } = await updateWorkspacePolicy(workspace, {
        tool_policy: policy,
        tool_policy_model: model || null,
      });
      const loaded = data?.tool_policy || {};
      const loadedModel = data?.tool_policy_model || '';
      setPolicy(loaded);
      setModel(loadedModel);
      setSaved({ policy: loaded, model: loadedModel });
      setDone(true);
    } catch (e) {
      setError(e?.response?.data?.detail || t('toolPolicy.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const modeLabel = (mode) => t(`toolPolicy.modes.${mode}`);

  return (
    <div className="pt-4 border-t border-gray-100 space-y-3" data-testid="tool-policy-settings">
      <div>
        <label className="text-sm font-medium text-gray-700">{t('toolPolicy.workspaceTitle')}</label>
        <p className="text-xs text-gray-500 mt-1">{t('toolPolicy.workspaceIntro')}</p>
      </div>

      <label className="flex flex-wrap items-center gap-2 text-sm text-gray-700">
        <span>{t('toolPolicy.workspaceDefault')}</span>
        <select
          value={policy['*'] || NONE}
          onChange={(e) => setMode('*', e.target.value)}
          aria-label={t('toolPolicy.workspaceDefault')}
          className={fieldCls}
        >
          <option value={NONE}>{t('toolPolicy.workspaceDefaultNone')}</option>
          {MODES.map((m) => <option key={m} value={m}>{modeLabel(m)}</option>)}
        </select>
      </label>

      <div>
        <div className="text-sm text-gray-700 mb-1">{t('toolPolicy.overrides')}</div>
        {overrides.length === 0 ? (
          <p className="text-xs text-gray-500 italic mb-2">{t('toolPolicy.noOverrides')}</p>
        ) : (
          <ul className="space-y-1.5 mb-2">
            {overrides.map(([tool, mode]) => (
              <li key={tool} className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-xs text-gray-800 min-w-[10rem]">{tool}</span>
                <select
                  value={mode}
                  onChange={(e) => setMode(tool, e.target.value)}
                  aria-label={t('toolPolicy.modeFor', { tool })}
                  className={fieldCls}
                >
                  {MODES.map((m) => <option key={m} value={m}>{modeLabel(m)}</option>)}
                </select>
                <button
                  type="button"
                  onClick={() => setMode(tool, NONE)}
                  title={t('toolPolicy.remove')}
                  aria-label={t('toolPolicy.removeFor', { tool })}
                  className="text-gray-400 hover:text-gray-700"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </li>
            ))}
          </ul>
        )}
        <form className="flex flex-wrap items-center gap-2" onSubmit={addOverride}>
          <input
            value={newTool}
            onChange={(e) => setNewTool(e.target.value)}
            placeholder={t('toolPolicy.toolIdPlaceholder')}
            aria-label={t('toolPolicy.toolIdPlaceholder')}
            className={`${fieldCls} font-mono w-60`}
          />
          <select
            value={newMode}
            onChange={(e) => setNewMode(e.target.value)}
            aria-label={t('toolPolicy.newMode')}
            className={fieldCls}
          >
            {MODES.map((m) => <option key={m} value={m}>{modeLabel(m)}</option>)}
          </select>
          <button
            type="submit"
            className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-gray-200 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            <Plus className="w-3.5 h-3.5" /> {t('toolPolicy.addOverride')}
          </button>
        </form>
      </div>

      <label className="flex flex-wrap items-center gap-2 text-sm text-gray-700">
        <span>{t('toolPolicy.model')}</span>
        <select
          value={model}
          onChange={(e) => { setDone(false); setModel(e.target.value); }}
          aria-label={t('toolPolicy.model')}
          className={fieldCls}
        >
          <option value="">{t('toolPolicy.modelDefault')}</option>
          {modelOptions.map((id) => <option key={id} value={id}>{id}</option>)}
        </select>
      </label>
      <p className="text-xs text-gray-500">{t('toolPolicy.modelHint')}</p>

      {error && <p className="text-xs text-red-600">{error}</p>}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={save}
          disabled={saving || !dirty || !workspace}
          className="flex items-center gap-2 bg-indigo-600 text-white px-3 py-1.5 rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
        >
          <Save className="w-3.5 h-3.5" />
          {saving ? t('common.saving') : t('toolPolicy.saveWorkspace')}
        </button>
        {done && (
          <span className="text-xs text-green-600 inline-flex items-center gap-1">
            <CheckCircle className="w-3.5 h-3.5" /> {t('toolPolicy.saved')}
          </span>
        )}
      </div>
    </div>
  );
}
