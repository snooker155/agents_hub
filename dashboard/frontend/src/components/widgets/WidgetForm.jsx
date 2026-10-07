/**
 * The widget's settings: used by the create modal and by the Settings tab.
 * The backend validates everything again (widgets/models.py); this form only
 * keeps the obvious mistakes from making a round trip.
 */
import { useEffect, useState } from 'react';
import { Loader } from 'lucide-react';
import { getAgents, getAgentVersions } from '../../api';
import { createWidget, updateWidget } from '../../api/widgets';
import { useI18n } from '../../i18n';
import { errorDetail } from '../toast';
import {
  ACCENT_SWATCHES, FALLBACK_OPTIONS, bytesToKb, clampLimit, kbToBytes, originsFromText, originsToText,
} from './widgetUtils';

const inputCls = 'w-full border border-gray-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none';
const labelCls = 'block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider';
const hintCls = 'text-xs text-gray-400 mt-1';

function initialState(widget, options) {
  const limits = { ...Object.fromEntries(Object.entries(options.limits || {}).map(([k, v]) => [k, v.default])),
    ...(widget?.limits || {}) };
  return {
    name: widget?.name || '',
    agentId: widget?.agent_id || '',
    agentVersion: widget?.agent_version ?? '',
    origins: originsToText(widget?.allowed_origins),
    title: widget?.title || '',
    greeting: widget?.greeting || '',
    placeholder: widget?.placeholder || '',
    accent: widget?.accent || 'navy',
    language: widget?.language || 'auto',
    enabled: widget?.enabled ?? true,
    messagesPerMinute: limits.messages_per_minute ?? 6,
    attachmentKb: bytesToKb(limits.attachment_max_bytes ?? 0),
    maxAttachments: limits.max_attachments ?? 0,
    tokensPerDay: limits.tokens_per_day ?? 0,
  };
}

export default function WidgetForm({ widget, workspace, options, onSaved, onCancel }) {
  const { t } = useI18n();
  const opts = options || FALLBACK_OPTIONS;
  const isEdit = !!widget;
  const [form, setForm] = useState(() => initialState(widget, opts));
  const [agents, setAgents] = useState([]);
  // The stored versions of the chosen agent, for the version pin
  // (agents/versions.py): a widget answers with the live definition, or
  // with the version picked here.
  const [versions, setVersions] = useState([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let alive = true;
    getAgents(workspace)
      .then(({ data }) => {
        if (!alive) return;
        const list = Array.isArray(data) ? data : (data?.items || []);
        setAgents(list);
        setForm((f) => (f.agentId || !list.length ? f : { ...f, agentId: list[0].id }));
      })
      .catch(() => { if (alive) setAgents([]); });
    return () => { alive = false; };
  }, [workspace]);

  useEffect(() => {
    if (!form.agentId) { setVersions([]); return undefined; }
    let alive = true;
    getAgentVersions(form.agentId)
      .then(({ data }) => { if (alive) setVersions(data?.versions || []); })
      .catch(() => { if (alive) setVersions([]); });
    return () => { alive = false; };
  }, [form.agentId]);

  const set = (field) => (e) => {
    const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [field]: value }));
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!form.name.trim()) { setError(t('widgets.form.nameRequired')); return; }
    if (!form.agentId) { setError(t('widgets.form.agentRequired')); return; }
    const bounds = opts.limits || {};
    const payload = {
      name: form.name.trim(),
      agent_id: form.agentId,
      // A version of this agent; another agent's pin never carries over.
      agent_version: form.agentVersion === '' ? null : Number(form.agentVersion),
      allowed_origins: originsFromText(form.origins),
      title: form.title.trim(),
      greeting: form.greeting.trim(),
      placeholder: form.placeholder.trim(),
      accent: form.accent,
      language: form.language,
      enabled: !!form.enabled,
      limits: {
        messages_per_minute: clampLimit(form.messagesPerMinute, bounds.messages_per_minute),
        attachment_max_bytes: clampLimit(kbToBytes(form.attachmentKb), bounds.attachment_max_bytes),
        max_attachments: clampLimit(form.maxAttachments, bounds.max_attachments),
        tokens_per_day: clampLimit(form.tokensPerDay, bounds.tokens_per_day),
      },
    };
    setSaving(true);
    setError('');
    try {
      const { data } = isEdit
        ? await updateWidget(widget.widget_id, payload)
        : await createWidget({ ...payload, workspace: workspace || 'default' });
      onSaved(data);
    } catch (err) {
      setError(errorDetail(err) || t('widgets.form.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const agentChoices = agents.some((a) => a.id === form.agentId) || !form.agentId
    ? agents
    : [{ id: form.agentId, name: form.agentId }, ...agents];

  return (
    <form onSubmit={submit} className="space-y-4">
      <div>
        <label className={labelCls} htmlFor="widget-name">{t('widgets.form.name')}</label>
        <input id="widget-name" className={inputCls} value={form.name} onChange={set('name')}
          placeholder={t('widgets.form.namePlaceholder')} maxLength={120} />
      </div>

      <div>
        <label className={labelCls} htmlFor="widget-agent">{t('widgets.form.agent')}</label>
        <select id="widget-agent" className={inputCls} value={form.agentId}
          onChange={(e) => setForm((f) => ({ ...f, agentId: e.target.value, agentVersion: '' }))}>
          {!agentChoices.length && <option value="">{t('widgets.form.noAgents')}</option>}
          {agentChoices.map((a) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
        </select>
        <p className={hintCls}>{t('widgets.form.agentHint')}</p>
      </div>

      {versions.length > 0 && (
        <div>
          <label className={labelCls} htmlFor="widget-agent-version">{t('agentVersionPin.jobFieldLabel')}</label>
          <select id="widget-agent-version" className={inputCls} value={form.agentVersion}
            onChange={set('agentVersion')}>
            <option value="">{t('agentVersionPin.jobFieldLive')}</option>
            {versions.slice().reverse().map((v) => (
              <option key={v.version} value={v.version}>{t('agentVersionPin.versionOption', { version: v.version })}</option>
            ))}
          </select>
          <p className={hintCls}>{t('agentVersionPin.widgetHint')}</p>
        </div>
      )}

      <div>
        <label className={labelCls} htmlFor="widget-origins">{t('widgets.form.origins')}</label>
        <textarea id="widget-origins" rows={3} className={`${inputCls} font-mono`} value={form.origins}
          onChange={set('origins')} placeholder="https://shop.example" spellCheck={false} />
        <p className={hintCls}>
          {t('widgets.form.originsHint')} {opts.wildcard_allowed ? t('widgets.form.originsWildcard') : ''}
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className={labelCls} htmlFor="widget-title">{t('widgets.form.title')}</label>
          <input id="widget-title" className={inputCls} value={form.title} onChange={set('title')}
            placeholder={t('widgets.form.titlePlaceholder')} maxLength={80} />
        </div>
        <div>
          <label className={labelCls} htmlFor="widget-placeholder">{t('widgets.form.placeholder')}</label>
          <input id="widget-placeholder" className={inputCls} value={form.placeholder} onChange={set('placeholder')}
            placeholder={t('widgets.form.placeholderPlaceholder')} maxLength={120} />
        </div>
      </div>

      <div>
        <label className={labelCls} htmlFor="widget-greeting">{t('widgets.form.greeting')}</label>
        <textarea id="widget-greeting" rows={2} className={inputCls} value={form.greeting} onChange={set('greeting')}
          placeholder={t('widgets.form.greetingPlaceholder')} maxLength={1000} />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <fieldset>
          <legend className={labelCls}>{t('widgets.form.accent')}</legend>
          <div className="flex flex-wrap gap-2" role="radiogroup">
            {(opts.accents || []).map((accent) => (
              <button key={accent} type="button" role="radio" aria-checked={form.accent === accent}
                title={t(`widgets.form.accents.${accent}`)} aria-label={t(`widgets.form.accents.${accent}`)}
                onClick={() => setForm((f) => ({ ...f, accent }))}
                className={`w-7 h-7 rounded-full ${ACCENT_SWATCHES[accent] || 'bg-gray-400'} ${
                  form.accent === accent ? 'ring-2 ring-offset-2 ring-indigo-500' : ''}`} />
            ))}
          </div>
        </fieldset>
        <div>
          <label className={labelCls} htmlFor="widget-language">{t('widgets.form.language')}</label>
          <select id="widget-language" className={inputCls} value={form.language} onChange={set('language')}>
            {(opts.languages || []).map((lang) => (
              <option key={lang} value={lang}>{t(`widgets.form.languages.${lang}`)}</option>
            ))}
          </select>
        </div>
      </div>

      <fieldset className="border border-gray-100 rounded-lg p-3">
        <legend className="px-1 text-xs font-bold text-gray-500 uppercase tracking-wider">{t('widgets.form.limits')}</legend>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {[
            ['messagesPerMinute', 'widget-mpm', t('widgets.form.messagesPerMinute')],
            ['attachmentKb', 'widget-akb', t('widgets.form.attachmentKb')],
            ['maxAttachments', 'widget-max', t('widgets.form.maxAttachments')],
            ['tokensPerDay', 'widget-tpd', t('widgets.form.tokensPerDay')],
          ].map(([field, id, label]) => (
            <div key={field}>
              <label className="block text-xs text-gray-500 mb-1" htmlFor={id}>{label}</label>
              <input id={id} type="number" min={0} className={inputCls} value={form[field]} onChange={set(field)} />
            </div>
          ))}
        </div>
      </fieldset>

      <label className="flex items-center gap-2 text-sm text-gray-700">
        <input type="checkbox" checked={!!form.enabled} onChange={set('enabled')}
          className="h-4 w-4 rounded border-gray-300 text-indigo-600" />
        {t('widgets.form.enabled')}
      </label>

      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}

      <div className="flex justify-end gap-2">
        {onCancel && (
          <button type="button" onClick={onCancel}
            className="px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">
            {t('widgets.form.cancel')}
          </button>
        )}
        <button type="submit" disabled={saving}
          className="flex items-center gap-2 px-3 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-60">
          {saving && <Loader className="w-4 h-4 animate-spin" />}
          {isEdit ? t('widgets.form.save') : t('widgets.form.create')}
        </button>
      </div>
    </form>
  );
}
