import { useState } from 'react';
import { Loader, RotateCcw, Save } from 'lucide-react';
import { humanBytes } from './jobs';
import { useI18n } from '../../i18n';

const inputCls = 'border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:outline-none w-28 tabular-nums';
const MIB = 1024 * 1024;
const RAM_PRESETS_GB = [0, 2, 4, 8, 16];

function Switch({ checked, onChange, disabled, label, testId }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      data-testid={testId}
      className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${checked ? 'bg-indigo-600' : 'bg-gray-300'}`}
    >
      <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${checked ? 'translate-x-5' : 'translate-x-1'}`} />
    </button>
  );
}

function Row({ title, hint, children }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start gap-1.5 sm:gap-4 py-2.5 border-t border-gray-100 first:border-t-0">
      <div className="sm:w-1/2 min-w-0">
        <p className="text-sm font-medium text-gray-800">{title}</p>
        {hint && <p className="text-xs text-gray-500 mt-0.5">{hint}</p>}
      </div>
      <div className="sm:w-1/2 flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}

const gb = (mib) => Math.round((mib / 1024) * 10) / 10;

/**
 * The prompt cache's settings (deploy/models/app.py, CACHE_DEFAULTS): one set
 * for every local chat model. Saved as a whole draft; a running model takes
 * them when it is loaded again, which the card above offers right after.
 * Read-only for anyone who is not an administrator.
 */
export default function CacheSettings({ data, canEdit, saving, onSave }) {
  const { t } = useI18n();
  const settings = data?.settings || {};
  const defaults = data?.defaults || {};
  const limits = data?.limits || {};
  const [draft, setDraft] = useState(settings);
  const [prev, setPrev] = useState(settings);
  // A fresh answer from the runtime replaces an unchanged draft.
  if (settings !== prev) {
    setPrev(settings);
    if (JSON.stringify(draft) === JSON.stringify(prev)) setDraft(settings);
  }
  const set = (key, value) => setDraft((d) => ({ ...d, [key]: value }));
  const changed = Object.keys(draft).filter((k) => draft[k] !== settings[k]);
  const lim = (k) => limits[k] || [0, Number.MAX_SAFE_INTEGER];
  const intField = (key, { step = 1, testId } = {}) => (
    <input
      type="number" className={inputCls} min={lim(key)[0]} max={lim(key)[1]} step={step}
      value={draft[key] ?? ''} disabled={!canEdit} data-testid={testId}
      onChange={(e) => set(key, e.target.value === '' ? 0 : Math.max(lim(key)[0], Math.min(lim(key)[1], Math.round(Number(e.target.value)))))}
    />
  );
  const gbField = (key, testId) => (
    <span className="inline-flex items-center gap-1.5">
      <input
        type="number" className={inputCls} min={0} step={0.5} value={gb(draft[key] ?? 0)} disabled={!canEdit}
        data-testid={testId}
        onChange={(e) => set(key, Math.max(lim(key)[0], Math.min(lim(key)[1], Math.round(Number(e.target.value || 0) * 1024))))}
      />
      <span className="text-sm text-gray-500">{t('localModels.runtimeCache.gb')}</span>
    </span>
  );
  const mem = data?.memory || {};
  const llamaModels = (data?.models || []).filter((m) => m.engine === 'llama').length;
  const ramNeed = (draft.ram_mib || 0) * MIB * Math.max(1, llamaModels);
  const ramOf = mem.limit_bytes || mem.ram_total_bytes;
  const tooMuch = draft.enabled && ramOf && ramNeed > ramOf * 0.5;
  const off = !draft.enabled;

  return (
    <div data-testid="cache-settings">
      <Row title={t('localModels.runtimeCache.settings.enabled')} hint={t('localModels.runtimeCache.settings.enabledHint')}>
        <Switch checked={!!draft.enabled} onChange={(v) => set('enabled', v)} disabled={!canEdit}
          label={t('localModels.runtimeCache.settings.enabled')} testId="cache-enabled" />
      </Row>
      <Row title={t('localModels.runtimeCache.settings.ram')} hint={t('localModels.runtimeCache.settings.ramHint')}>
        {gbField('ram_mib', 'cache-ram')}
        <span className="flex flex-wrap gap-1">
          {RAM_PRESETS_GB.map((g) => (
            <button
              key={g} type="button" disabled={!canEdit || off}
              onClick={() => set('ram_mib', g * 1024)}
              className={`px-2 py-0.5 rounded-full border text-xs transition-colors disabled:opacity-50 ${draft.ram_mib === g * 1024 ? 'border-indigo-300 bg-indigo-50 text-indigo-700' : 'border-gray-200 text-gray-600 hover:bg-gray-50'}`}
            >
              {g === 0 ? t('localModels.runtimeCache.settings.ramOff') : `${g} ${t('localModels.runtimeCache.gb')}`}
            </button>
          ))}
        </span>
        {ramOf ? (
          <p className="w-full text-xs text-gray-500">
            {t('localModels.runtimeCache.settings.ramOf', { total: humanBytes(ramOf), models: Math.max(1, llamaModels), need: humanBytes(ramNeed) })}
          </p>
        ) : null}
        {tooMuch && <p className="w-full text-xs text-amber-700" data-testid="cache-ram-warning">{t('localModels.runtimeCache.settings.ramTooMuch')}</p>}
      </Row>
      <Row title={t('localModels.runtimeCache.settings.kvType')} hint={t('localModels.runtimeCache.settings.kvTypeHint')}>
        <select
          className={`${inputCls} w-auto`} value={draft.kv_type || 'f16'} disabled={!canEdit}
          onChange={(e) => set('kv_type', e.target.value)} data-testid="cache-kv-type"
        >
          {(data?.kv_types || ['f16', 'q8_0', 'q4_0']).map((k) => (
            <option key={k} value={k}>{t(`localModels.runtimeCache.settings.kvTypes.${k}`, { defaultValue: k })}</option>
          ))}
        </select>
      </Row>
      <Row title={t('localModels.runtimeCache.settings.slots')} hint={t('localModels.runtimeCache.settings.slotsHint')}>
        {intField('slots', { testId: 'cache-slots' })}
        <span className="text-xs text-gray-500">{t('localModels.runtimeCache.settings.zeroAuto')}</span>
      </Row>
      <Row title={t('localModels.runtimeCache.settings.reuse')} hint={t('localModels.runtimeCache.settings.reuseHint')}>
        {intField('reuse_tokens', { step: 64 })}
        <span className="text-xs text-gray-500">{t('localModels.runtimeCache.settings.zeroOff')}</span>
      </Row>
      <Row title={t('localModels.runtimeCache.settings.disk')} hint={t('localModels.runtimeCache.settings.diskHint')}>
        <Switch checked={!!draft.disk} onChange={(v) => set('disk', v)} disabled={!canEdit || off}
          label={t('localModels.runtimeCache.settings.disk')} testId="cache-disk" />
        {gbField('disk_mib', 'cache-disk-limit')}
      </Row>
      <Row title={t('localModels.runtimeCache.settings.warmup')} hint={t('localModels.runtimeCache.settings.warmupHint')}>
        <Switch checked={!!draft.warmup} onChange={(v) => set('warmup', v)} disabled={!canEdit || off}
          label={t('localModels.runtimeCache.settings.warmup')} testId="cache-warmup" />
        {intField('warmup_prompts')}
        <span className="text-xs text-gray-500">{t('localModels.runtimeCache.settings.warmupPrompts')}</span>
      </Row>

      {canEdit ? (
        <div className="flex flex-wrap items-center gap-2 pt-3">
          <button
            type="button" onClick={() => onSave(Object.fromEntries(changed.map((k) => [k, draft[k]])))}
            disabled={!changed.length || saving}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 text-white text-xs font-medium hover:bg-indigo-700 transition-colors disabled:opacity-50"
            data-testid="cache-save"
          >
            {saving ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
            {t('localModels.runtimeCache.settings.save')}
          </button>
          <button
            type="button" onClick={() => setDraft({ ...settings, ...defaults })}
            disabled={saving || Object.keys(defaults).every((k) => draft[k] === defaults[k])}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-xs font-medium transition-colors disabled:opacity-50"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            {t('localModels.runtimeCache.settings.defaults')}
          </button>
          {changed.length > 0 && <span className="text-xs text-gray-500">{t('localModels.runtimeCache.settings.unsaved', { count: changed.length })}</span>}
        </div>
      ) : (
        <p className="pt-3 text-xs text-gray-500">{t('localModels.runtimeCache.settings.adminOnly')}</p>
      )}
    </div>
  );
}
