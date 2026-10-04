import { useCallback, useEffect, useMemo, useState } from 'react';
import { AudioLines, Film, Image as ImageIcon, Loader, Mic, Plus, Save, Sparkles, Trash2 } from 'lucide-react';
import { getWorkspaceSpecialModels, updateWorkspaceSpecialModels } from '../../api';
import { SectionCard, inputCls } from '../settingsUi';
import { useToast, errorDetail } from '../toast';
import { useI18n } from '../../i18n';

const ICONS = { image: ImageIcon, video: Film, speech: AudioLines, transcription: Mic };

// "Name: value" per line, the way the headers are typed, and back.
const headersToText = (headers) => Object.entries(headers || {}).map(([k, v]) => `${k}: ${v}`).join('\n');
const textToHeaders = (text) => Object.fromEntries(
  String(text || '').split('\n').map((line) => {
    const at = line.indexOf(':');
    return at > 0 ? [line.slice(0, at).trim(), line.slice(at + 1).trim()] : null;
  }).filter((pair) => pair && pair[0]),
);

const priceOrNull = (value) => (value === '' || value === null || value === undefined ? undefined : Number(value));

/**
 * The models this workspace uses for work a chat model does not do
 * (providers/special.py): pictures, video, speech, transcripts, and models
 * of its own. Agents call a tool per purpose (generate_image and so on) and
 * the workspace decides which model answers it; a purpose left empty has no
 * model, and its tool answers that the model is not added. Nothing comes
 * from another workspace, except in a personal workspace, which uses
 * default's model for a purpose it left empty. Saved with its own button.
 */
export default function WorkspaceSpecialModels({ workspace }) {
  const { t } = useI18n();
  const toast = useToast();
  const [payload, setPayload] = useState(null);
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);

  const fromServer = useCallback((data) => {
    setPayload(data);
    const own = data.own || {};
    const next = { custom: (own.custom || []).map((c) => ({ ...c, headersText: headersToText(c.headers) })) };
    for (const p of data.options.purposes) {
      const entry = own[p.id] || {};
      next[p.id] = {
        provider: entry.provider || '', model: entry.model || '',
        price_usd: entry.price_usd ?? '', options: { ...(entry.options || {}) },
      };
    }
    setDraft(next);
  }, []);

  const load = useCallback(() => {
    getWorkspaceSpecialModels(workspace)
      .then(({ data }) => fromServer(data))
      .catch((e) => toast.error(t('workspaceDetails.specialModels.loadFailed'), errorDetail(e)));
  }, [workspace, fromServer, t, toast]);

  useEffect(() => { load(); }, [load]);

  const providers = useMemo(() => payload?.options?.providers || [], [payload]);

  if (!payload || !draft) {
    return (
      <p className="text-sm text-gray-500 flex items-center gap-2">
        <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
      </p>
    );
  }

  // A personal workspace takes default's model for a purpose it left empty.
  const inherited = (id) => {
    const entry = payload.effective?.[id];
    return entry?.inherited_from ? entry : null;
  };
  const setPurpose = (id, patch) => setDraft((d) => ({ ...d, [id]: { ...d[id], ...patch } }));
  const setCustom = (index, patch) => setDraft((d) => ({
    ...d, custom: d.custom.map((c, i) => (i === index ? { ...c, ...patch } : c)),
  }));

  const save = async () => {
    const body = {};
    for (const p of payload.options.purposes) {
      const entry = draft[p.id];
      if (!entry.provider && !entry.model) continue;
      body[p.id] = {
        provider: entry.provider, model: entry.model.trim(),
        price_usd: priceOrNull(entry.price_usd),
        options: Object.fromEntries(Object.entries(entry.options || {}).filter(([, v]) => String(v).trim())),
      };
    }
    body.custom = draft.custom.map((c) => ({
      id: (c.id || '').trim(), name: c.name, description: c.description, kind: c.kind || 'chat',
      ...(c.kind === 'http'
        ? { url: c.url, headers: textToHeaders(c.headersText) }
        : { provider: c.provider, model: c.model }),
      price_usd: priceOrNull(c.price_usd),
    }));
    setSaving(true);
    try {
      const { data } = await updateWorkspaceSpecialModels(workspace, body);
      fromServer(data);
      toast.success(t('workspaceDetails.specialModels.saved'));
    } catch (e) {
      toast.error(t('workspaceDetails.specialModels.saveFailed'), errorDetail(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-gray-500 max-w-xl">
          {t('workspaceDetails.specialModels.intro')}
        </p>
        <button
          type="button"
          onClick={save}
          disabled={saving}
          className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-50"
        >
          {saving ? <Loader className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? t('common.saving') : t('workspaceDetails.specialModels.save')}
        </button>
      </div>

      {payload.options.purposes.map((p) => {
        const Icon = ICONS[p.id] || Sparkles;
        const entry = draft[p.id];
        const allowed = providers.filter((pr) => p.kinds.includes(pr.kind));
        const kind = providers.find((pr) => pr.id === entry.provider)?.kind;
        const suggestions = kind ? (p.suggestions[kind] || []) : [];
        return (
          <SectionCard
            key={p.id}
            title={(
              <span className="inline-flex items-center gap-2">
                <Icon className="w-4 h-4 text-indigo-500" /> {t(`workspaceDetails.specialModels.purposes.${p.id}`)}
              </span>
            )}
            actions={<code className="text-[11px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">{p.tool}</code>}
          >
            <p className="text-xs text-gray-500 -mt-2">{t(`workspaceDetails.specialModels.purposeHints.${p.id}`)}</p>
            {!entry.provider && inherited(p.id) && (
              <p className="text-xs text-indigo-600" data-testid={`special-${p.id}-inherited`}>
                {t('workspaceDetails.specialModels.inherited', {
                  workspace: inherited(p.id).inherited_from,
                  model: `${inherited(p.id).provider}/${inherited(p.id).model}`,
                })}
              </p>
            )}
            {!entry.provider && !inherited(p.id) && (
              <p className="text-xs text-gray-500" data-testid={`special-${p.id}-missing`}>
                {t('workspaceDetails.specialModels.notAdded', { tool: p.tool })}
              </p>
            )}
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <label className="text-xs font-medium text-gray-600">
                {t('workspaceDetails.specialModels.provider')}
                <select
                  value={entry.provider}
                  onChange={(e) => setPurpose(p.id, { provider: e.target.value })}
                  className={`${inputCls} mt-1 bg-white`}
                >
                  <option value="">{t('workspaceDetails.specialModels.none')}</option>
                  {allowed.map((pr) => <option key={pr.id} value={pr.id}>{pr.label}</option>)}
                </select>
              </label>
              <label className="text-xs font-medium text-gray-600">
                {t('workspaceDetails.specialModels.model')}
                <input
                  list={`special-${p.id}-models`}
                  value={entry.model}
                  disabled={!entry.provider}
                  placeholder={suggestions[0] || ''}
                  onChange={(e) => setPurpose(p.id, { model: e.target.value })}
                  className={`${inputCls} mt-1`}
                />
                <datalist id={`special-${p.id}-models`}>
                  {suggestions.map((m) => <option key={m} value={m} />)}
                </datalist>
              </label>
              <label className="text-xs font-medium text-gray-600">
                {t(`workspaceDetails.specialModels.units.${p.unit}`)}
                <input
                  type="number" min="0" step="0.001"
                  value={entry.price_usd}
                  disabled={!entry.provider}
                  placeholder="0.00"
                  onChange={(e) => setPurpose(p.id, { price_usd: e.target.value })}
                  className={`${inputCls} mt-1`}
                />
              </label>
            </div>
            {entry.provider && Object.keys(p.options).length > 0 && (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                {Object.entries(p.options).map(([key, hint]) => (
                  <label key={key} className="text-xs font-medium text-gray-600">
                    {t(`workspaceDetails.specialModels.options.${key}`)}
                    <input
                      value={entry.options[key] || ''}
                      placeholder={hint}
                      onChange={(e) => setPurpose(p.id, { options: { ...entry.options, [key]: e.target.value } })}
                      className={`${inputCls} mt-1`}
                    />
                  </label>
                ))}
              </div>
            )}
          </SectionCard>
        );
      })}

      <SectionCard
        title={(
          <span className="inline-flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-indigo-500" /> {t('workspaceDetails.specialModels.customTitle')}
          </span>
        )}
        actions={<code className="text-[11px] bg-gray-100 text-gray-600 px-1.5 py-0.5 rounded">{payload.options.custom.tool}</code>}
      >
        <p className="text-xs text-gray-500 -mt-2">{t('workspaceDetails.specialModels.customHint')}</p>
        {draft.custom.map((c, index) => (
          <div key={index} className="rounded-lg border border-gray-200 p-3 space-y-3" data-testid="special-model-custom">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <label className="text-xs font-medium text-gray-600">
                {t('workspaceDetails.specialModels.customId')}
                <input value={c.id || ''} placeholder="jev"
                       onChange={(e) => setCustom(index, { id: e.target.value.toLowerCase() })}
                       className={`${inputCls} mt-1 font-mono`} />
              </label>
              <label className="text-xs font-medium text-gray-600">
                {t('workspaceDetails.specialModels.customName')}
                <input value={c.name || ''} onChange={(e) => setCustom(index, { name: e.target.value })}
                       className={`${inputCls} mt-1`} />
              </label>
              <label className="text-xs font-medium text-gray-600">
                {t('workspaceDetails.specialModels.customKind')}
                <select value={c.kind || 'chat'} onChange={(e) => setCustom(index, { kind: e.target.value })}
                        className={`${inputCls} mt-1 bg-white`}>
                  <option value="chat">{t('workspaceDetails.specialModels.kinds.chat')}</option>
                  <option value="http">{t('workspaceDetails.specialModels.kinds.http')}</option>
                </select>
              </label>
            </div>
            <label className="block text-xs font-medium text-gray-600">
              {t('workspaceDetails.specialModels.customDescription')}
              <textarea rows={2} value={c.description || ''}
                        placeholder={t('workspaceDetails.specialModels.customDescriptionHint')}
                        onChange={(e) => setCustom(index, { description: e.target.value })}
                        className={`${inputCls} mt-1`} />
            </label>
            {(c.kind || 'chat') === 'chat' ? (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <label className="text-xs font-medium text-gray-600">
                  {t('workspaceDetails.specialModels.provider')}
                  <input value={c.provider || ''} placeholder="openai"
                         onChange={(e) => setCustom(index, { provider: e.target.value })}
                         className={`${inputCls} mt-1`} />
                </label>
                <label className="text-xs font-medium text-gray-600">
                  {t('workspaceDetails.specialModels.model')}
                  <input value={c.model || ''} onChange={(e) => setCustom(index, { model: e.target.value })}
                         className={`${inputCls} mt-1`} />
                </label>
                <label className="text-xs font-medium text-gray-600">
                  {t('workspaceDetails.specialModels.units.call')}
                  <input type="number" min="0" step="0.001" value={c.price_usd ?? ''}
                         onChange={(e) => setCustom(index, { price_usd: e.target.value })}
                         className={`${inputCls} mt-1`} />
                </label>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <label className="text-xs font-medium text-gray-600 md:col-span-2">
                  URL
                  <input value={c.url || ''} placeholder="https://gpu.internal/predict"
                         onChange={(e) => setCustom(index, { url: e.target.value })}
                         className={`${inputCls} mt-1 font-mono`} />
                </label>
                <label className="text-xs font-medium text-gray-600">
                  {t('workspaceDetails.specialModels.units.call')}
                  <input type="number" min="0" step="0.001" value={c.price_usd ?? ''}
                         onChange={(e) => setCustom(index, { price_usd: e.target.value })}
                         className={`${inputCls} mt-1`} />
                </label>
                <label className="text-xs font-medium text-gray-600 md:col-span-3">
                  {t('workspaceDetails.specialModels.headers')}
                  <textarea rows={2} value={c.headersText || ''} placeholder="Authorization: Bearer ${MY_TOKEN}"
                            onChange={(e) => setCustom(index, { headersText: e.target.value })}
                            className={`${inputCls} mt-1 font-mono`} />
                  <span className="mt-1 block font-normal text-gray-400">{t('workspaceDetails.specialModels.headersHint')}</span>
                </label>
              </div>
            )}
            <div className="flex justify-end">
              <button type="button"
                      onClick={() => setDraft((d) => ({ ...d, custom: d.custom.filter((_, i) => i !== index) }))}
                      className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-800">
                <Trash2 className="w-3.5 h-3.5" /> {t('workspaceDetails.specialModels.remove')}
              </button>
            </div>
          </div>
        ))}
        <button type="button"
                onClick={() => setDraft((d) => ({ ...d, custom: [...d.custom, { kind: 'chat' }] }))}
                className="inline-flex items-center gap-1.5 text-sm font-medium text-indigo-600 hover:text-indigo-800">
          <Plus className="w-4 h-4" /> {t('workspaceDetails.specialModels.addCustom')}
        </button>
      </SectionCard>
    </div>
  );
}
