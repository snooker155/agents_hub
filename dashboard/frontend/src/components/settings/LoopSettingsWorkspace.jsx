import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { CheckCircle, Save } from 'lucide-react';
import { getWorkspaceLoopSettings, updateWorkspaceLoopSettings } from '../../api/loopSettings';
import { useI18n } from '../../i18n';

const fieldCls = 'border border-gray-200 rounded-lg px-2 py-1.5 text-sm focus:outline-none';

// Loop settings key -> its i18n sub-namespace (dot path under
// `loopSettings.fields`) and, for a number, its input step and range. The
// order here is the order the card renders them in.
const FIELDS = [
  { key: 'compaction', i18n: 'compaction', kind: 'bool' },
  { key: 'compaction_fraction', i18n: 'compactionFraction', kind: 'number', step: 0.01, min: 0.05, max: 0.95 },
  { key: 'compaction_keep', i18n: 'compactionKeep', kind: 'number', step: 1, min: 1 },
  { key: 'tool_search_threshold', i18n: 'toolSearchThreshold', kind: 'number', step: 1, min: 1 },
  { key: 'native', i18n: 'native', kind: 'bool' },
  { key: 'strict_tools', i18n: 'strictTools', kind: 'bool' },
  { key: 'view_focus', i18n: 'viewFocus', kind: 'bool' },
  { key: 'tool_output_spill_chars', i18n: 'toolOutputSpillChars', kind: 'number', step: 1000, min: 0, max: 1000000 },
  { key: 'advisor_max_calls', i18n: 'advisorMaxCalls', kind: 'number', step: 1, min: 0, max: 50 },
  { key: 'advisor_max_answer_chars', i18n: 'advisorMaxAnswerChars', kind: 'number', step: 100, min: 200, max: 50000 },
];

// The value shown for *key* once the stored block, the environment and the
// built-in default are merged the way `loop_setting` resolves it for a built
// agent: the stored value when the workspace overrides it, else whatever the
// backend already resolved into `effective`.
function mergedValues(data) {
  const settings = data?.settings || {};
  const effective = data?.effective || {};
  const values = {};
  FIELDS.forEach(({ key }) => {
    values[key] = Object.prototype.hasOwnProperty.call(settings, key) ? settings[key] : effective[key];
  });
  return values;
}

// Where a key's effective value came from: the workspace block itself when
// it is set there; otherwise the environment when that alone explains why
// the effective value differs from the built-in default; the default
// otherwise. The backend does not say which of the two applied, but with
// only three possible sources this is enough to tell them apart.
function sourceOf(data, key) {
  const settings = data?.settings || {};
  if (Object.prototype.hasOwnProperty.call(settings, key)) return 'workspace';
  const effective = data?.effective || {};
  const defaults = data?.defaults || {};
  return effective[key] !== defaults[key] ? 'environment' : 'default';
}

/**
 * The workspace's agent loop settings (agents/loop_ext/settings.py,
 * `settings.loop`): compaction, its fraction and how many recent tool
 * results always stay verbatim, the tool-search threshold, and the two
 * on/off native features (Anthropic context editing and deferred tool
 * loading, strict OpenAI tool schemas). Every key is optional: unset falls
 * back to its `AGENTS_HUB_LOOP_<KEY>` environment variable, then a built-in
 * default.
 *
 * Saved through PUT /api/workspaces/{name}/loop-settings, which merges a
 * partial object into the stored block; a key sent as null resets it.
 */
export default function LoopSettingsWorkspace({ workspace }) {
  const { t } = useI18n();
  const [data, setData] = useState(null);
  const [values, setValues] = useState({});
  const [baseline, setBaseline] = useState({});
  const [saving, setSaving] = useState(false);
  const [resetting, setResetting] = useState('');
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  // Read through a ref so a new `t` (a language switch) does not reload and
  // throw away unsaved edits.
  const tRef = useRef(t);
  useLayoutEffect(() => { tRef.current = t; });

  // A promise chain rather than an async body, so the effect below may call it
  // without a synchronous setState.
  const load = useCallback(() => {
    if (!workspace) return Promise.resolve();
    return getWorkspaceLoopSettings(workspace)
      .then(({ data: loaded }) => {
        setError('');
        setData(loaded);
        const merged = mergedValues(loaded);
        setValues(merged);
        setBaseline(merged);
      })
      .catch((e) => {
        setError(e?.response?.data?.detail || tRef.current('loopSettings.loadFailed'));
      });
  }, [workspace]);

  useEffect(() => { load(); }, [load]);

  const dirtyKeys = useMemo(
    () => FIELDS.map((f) => f.key).filter((key) => values[key] !== baseline[key]),
    [values, baseline],
  );
  const dirty = dirtyKeys.length > 0;

  const setValue = (key, value) => {
    setDone(false);
    setValues((prev) => ({ ...prev, [key]: value }));
  };

  const save = async () => {
    if (!dirty) return;
    setSaving(true);
    setError('');
    setDone(false);
    const patch = {};
    dirtyKeys.forEach((key) => { patch[key] = values[key]; });
    try {
      const { data: saved } = await updateWorkspaceLoopSettings(workspace, patch);
      setData(saved);
      const merged = mergedValues(saved);
      setValues(merged);
      setBaseline(merged);
      setDone(true);
    } catch (e) {
      setError(e?.response?.data?.detail || t('loopSettings.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const resetField = async (key) => {
    setResetting(key);
    setError('');
    setDone(false);
    try {
      const { data: saved } = await updateWorkspaceLoopSettings(workspace, { [key]: null });
      setData(saved);
      const merged = mergedValues(saved);
      setValues(merged);
      setBaseline(merged);
    } catch (e) {
      setError(e?.response?.data?.detail || t('loopSettings.saveFailed'));
    } finally {
      setResetting('');
    }
  };

  return (
    <div className="pt-4 border-t border-gray-100 space-y-4" data-testid="loop-settings-workspace">
      <div>
        <label className="text-sm font-medium text-gray-700">{t('loopSettings.title')}</label>
        <p className="text-xs text-gray-500 mt-1">{t('loopSettings.intro')}</p>
      </div>

      {error && <p className="text-xs text-red-600">{error}</p>}

      <div className="space-y-3">
        {FIELDS.map((field) => {
          const label = t(`loopSettings.fields.${field.i18n}.label`);
          const hint = t(`loopSettings.fields.${field.i18n}.hint`);
          const source = data ? sourceOf(data, field.key) : 'default';
          return (
            <div key={field.key} className="flex items-center justify-between gap-3">
              <div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-gray-700">{label}</span>
                  <span className="text-xs text-gray-400 bg-gray-50 border border-gray-200 rounded-full px-2 py-0.5">
                    {t(`loopSettings.source.${source}`)}
                  </span>
                </div>
                <p className="text-xs text-gray-500 mt-1">{hint}</p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {field.kind === 'bool' ? (
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      className="sr-only peer"
                      checked={!!values[field.key]}
                      onChange={(e) => setValue(field.key, e.target.checked)}
                      aria-label={label}
                    />
                    <span className="w-11 h-6 bg-gray-200 rounded-full peer peer-checked:bg-indigo-600 relative transition-colors">
                      <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full transition-transform ${values[field.key] ? 'translate-x-5' : ''}`} />
                    </span>
                  </label>
                ) : (
                  <input
                    type="number"
                    step={field.step}
                    min={field.min}
                    max={field.max}
                    value={values[field.key] ?? ''}
                    onChange={(e) => {
                      const next = Number(e.target.value);
                      if (!Number.isNaN(next)) setValue(field.key, next);
                    }}
                    aria-label={label}
                    className={`${fieldCls} w-24`}
                  />
                )}
                <button
                  type="button"
                  onClick={() => resetField(field.key)}
                  disabled={resetting === field.key || !Object.prototype.hasOwnProperty.call(data?.settings || {}, field.key)}
                  aria-label={t('loopSettings.resetFor', { field: label })}
                  className="text-xs text-gray-400 hover:text-gray-700 disabled:opacity-40"
                >
                  {t('loopSettings.reset')}
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={save}
          disabled={saving || !dirty || !workspace}
          className="flex items-center gap-2 bg-indigo-600 text-white px-3 py-1.5 rounded-lg text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
        >
          <Save className="w-3.5 h-3.5" />
          {saving ? t('common.saving') : t('loopSettings.save')}
        </button>
        {done && (
          <span className="text-xs text-green-600 inline-flex items-center gap-1">
            <CheckCircle className="w-3.5 h-3.5" /> {t('loopSettings.saved')}
          </span>
        )}
      </div>
    </div>
  );
}
