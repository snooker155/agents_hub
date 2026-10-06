import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AlertTriangle, AudioLines, CheckCircle2, Film, Image as ImageIcon, Loader, Mic, Play, Plug, Plus, RefreshCw, Save,
  Sparkles, Square, Trash2, XCircle,
} from 'lucide-react';
import {
  checkWorkspaceSpecialModel, discoverWorkspaceSpecialModels, getWorkspaceSpecialModelVoices, getWorkspaceSpecialModels,
  sampleWorkspaceSpecialModel, updateWorkspaceSpecialModels,
} from '../../api';
import { SectionCard, inputCls } from '../settingsUi';
import { useToast, errorDetail } from '../toast';
import { useI18n } from '../../i18n';
import useVoiceSample from '../useVoiceSample';

const ICONS = { image: ImageIcon, video: Film, speech: AudioLines, transcription: Mic };

// "Name: value" per line, the way the headers are typed, and back.
const headersToText = (headers) => Object.entries(headers || {}).map(([k, v]) => `${k}: ${v}`).join('\n');
const textToHeaders = (text) => Object.fromEntries(
  String(text || '').split('\n').map((line) => {
    const at = line.indexOf(':');
    return at > 0 ? [line.slice(0, at).trim(), line.slice(at + 1).trim()] : null;
  }).filter((pair) => pair && pair[0]),
);

// How many found models show as buttons; the rest stay in the field's list.
const FOUND_SHOWN = 24;

const CHECK_LOOK = {
  ok: { Icon: CheckCircle2, cls: 'text-emerald-600' },
  warn: { Icon: AlertTriangle, cls: 'text-amber-600' },
  error: { Icon: XCircle, cls: 'text-red-600' },
};

/**
 * The connection check of one model: a button and what the last check said.
 * ``state`` is { sig, loading, status, message }; a result made for other
 * values than the form holds now (``sig``) is not shown.
 */
function ConnectionCheck({ id, sig, state, onCheck }) {
  const { t } = useI18n();
  const shown = state?.sig === sig ? state : null;
  const look = shown && !shown.loading ? CHECK_LOOK[shown.status] || CHECK_LOOK.error : null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <button
        type="button"
        onClick={onCheck}
        disabled={shown?.loading}
        data-testid={`special-${id}-check`}
        title={t('workspaceDetails.specialModels.checkHint')}
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-gray-200 text-xs font-medium text-gray-700 hover:border-indigo-300 hover:text-indigo-700 disabled:opacity-50"
      >
        {shown?.loading ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Plug className="w-3.5 h-3.5" />}
        {shown?.loading ? t('workspaceDetails.specialModels.checking') : t('workspaceDetails.specialModels.check')}
      </button>
      {look && (
        <span className={`inline-flex items-start gap-1 text-xs ${look.cls}`} data-testid={`special-${id}-check-result`}>
          <look.Icon className="w-3.5 h-3.5 mt-px shrink-0" /> {shown.message}
        </span>
      )}
    </div>
  );
}

/**
 * The voice of a speech model: a list of the voices the model has (each with
 * its language when it speaks one), or a free field when none are known, and
 * a button that plays a short line with the chosen voice. The line is in the
 * voice's own language, else in the page's; ``sample`` is what the backend
 * reads it with, null while there is no model to read it.
 */
function VoicePicker({ id, workspace, value, onChange, hint, info, fallbackNames, sample }) {
  const { t, language } = useI18n();
  const names = info?.voices || fallbackNames;
  const languages = info?.languages || {};
  const single = Boolean(info?.own) && names.length === 0;
  // The model's own list is the whole truth: a voice outside it is one the
  // model does not have, so it is neither offered nor kept. The names known
  // for a provider's API shape are only some, so a voice outside them stays.
  const stale = Boolean(info?.own) && !info.loading && Boolean(value) && !names.includes(value);
  useEffect(() => { if (stale) onChange(''); }, [stale, onChange]);
  // A sample of other values than the form holds now is not shown.
  const { state, toggle, busy } = useVoiceSample(JSON.stringify({ ...sample, voice: value }));
  const play = () => toggle(
    (signal) => sampleWorkspaceSpecialModel(workspace, { ...sample, voice: value, language }, { signal }),
    t('workspaceDetails.specialModels.sampleFailed'),
  );

  const fieldId = `special-${id}-voice`;
  return (
    <div className="text-xs font-medium text-gray-600 md:col-span-2">
      <span className="flex items-center justify-between gap-2">
        <label htmlFor={fieldId}>{t('workspaceDetails.specialModels.options.voice')}</label>
        {info?.loading && <Loader className="w-3 h-3 animate-spin text-gray-400" />}
      </span>
      <div className="mt-1 flex items-stretch gap-1.5">
        {names.length > 0 ? (
          <select id={fieldId} value={value} onChange={(e) => onChange(e.target.value)}
                  data-testid={fieldId} className={`${inputCls} bg-white min-w-0`}>
            <option value="">{t('workspaceDetails.specialModels.voiceDefault')}</option>
            {value && !stale && !names.includes(value) && <option value={value}>{value}</option>}
            {names.map((v) => (
              <option key={v} value={v}>{languages[v] ? `${v} (${languages[v]})` : v}</option>
            ))}
          </select>
        ) : (
          <input id={fieldId} value={value} onChange={(e) => onChange(e.target.value)}
                 data-testid={fieldId} disabled={single && !value}
                 placeholder={single ? t('workspaceDetails.specialModels.singleVoice') : hint}
                 className={`${inputCls} min-w-0`} />
        )}
        <button
          type="button"
          onClick={play}
          disabled={!sample}
          data-testid={`special-${id}-sample`}
          title={t('workspaceDetails.specialModels.sampleHint')}
          aria-label={busy ? t('workspaceDetails.specialModels.sampleStop') : t('workspaceDetails.specialModels.sample')}
          className="inline-flex shrink-0 items-center gap-1 px-2.5 rounded-md border border-gray-200 text-xs font-medium text-gray-700 hover:border-indigo-300 hover:text-indigo-700 disabled:opacity-50"
        >
          {state.loading ? <Loader className="w-3.5 h-3.5 animate-spin" />
            : state.playing ? <Square className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
          {/* Both labels share one cell, so the button keeps the wider one's
              width and the field beside it does not jump while it plays. */}
          <span className="grid">
            <span className={`col-start-1 row-start-1 ${busy ? 'invisible' : ''}`} aria-hidden={busy}>
              {t('workspaceDetails.specialModels.sample')}
            </span>
            <span className={`col-start-1 row-start-1 ${busy ? '' : 'invisible'}`} aria-hidden={!busy}>
              {t('workspaceDetails.specialModels.sampleStop')}
            </span>
          </span>
        </button>
      </div>
      {/* One line is kept for the sample's text or error whether it is shown
          or not, so playing a sample does not push down what lies below. */}
      <div className="mt-1 h-4 font-normal">
        {state.error && (
          <span className="block truncate text-red-600" title={state.error} data-testid={`special-${id}-sample-error`}>
            {state.error}
          </span>
        )}
        {!state.error && state.text && (
          <span className="block truncate italic text-gray-400" data-testid={`special-${id}-sample-text`}
                title={`${state.language ? `${state.language}: ` : ''}«${state.text}»`}>
            {state.language ? `${state.language}: ` : ''}«{state.text}»
          </span>
        )}
      </div>
    </div>
  );
}

// What a custom model's check sends, and when it has enough to be checked.
const customBody = (c) => (c.kind === 'http'
  ? { id: (c.id || '').trim(), kind: 'http', url: (c.url || '').trim(), headers: textToHeaders(c.headersText) }
  : { kind: 'chat', provider: (c.provider || '').trim(), model: (c.model || '').trim() });
const customSig = (c) => JSON.stringify(customBody(c));
const customCheckable = (c) => (c.kind === 'http' ? Boolean((c.url || '').trim())
  : Boolean((c.provider || '').trim() && (c.model || '').trim()));

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
  // Per purpose, what the provider listed that fits it: { provider, models, total, loading, error }.
  const [found, setFound] = useState({});
  // Per purpose id or custom-<index>, the last connection check.
  const [checks, setChecks] = useState({});
  // Per "provider|model" of a model with voices, what it has:
  // { loading, voices, own, language, languages }. Asked when a model is
  // picked or typed, and again after a search at the provider.
  const [voices, setVoices] = useState({});
  const [voicesEpoch, setVoicesEpoch] = useState(0);
  const voicesAsked = useRef(new Set());

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

  // The models the form holds now for a purpose with a voice.
  const voiceKeys = useMemo(() => (payload && draft ? payload.options.purposes
    .filter((p) => 'voice' in (p.options || {}) && draft[p.id]?.provider && draft[p.id].model.trim())
    .map((p) => `${draft[p.id].provider}|${draft[p.id].model.trim()}`) : []).join('\n'), [payload, draft]);

  useEffect(() => {
    const wanted = voiceKeys ? voiceKeys.split('\n').filter((k) => !voicesAsked.current.has(k)) : [];
    if (!wanted.length) return undefined;
    // A model being typed is asked about once the typing stops.
    const timer = setTimeout(() => {
      for (const key of wanted) {
        voicesAsked.current.add(key);
        const [provider, ...rest] = key.split('|');
        setVoices((v) => ({ ...v, [key]: { ...v[key], loading: true } }));
        getWorkspaceSpecialModelVoices(workspace, provider, rest.join('|'))
          .then(({ data }) => setVoices((v) => ({ ...v, [key]: { ...data, loading: false } })))
          .catch(() => {
            voicesAsked.current.delete(key);
            setVoices((v) => ({ ...v, [key]: undefined }));
          });
      }
    }, 350);
    return () => clearTimeout(timer);
  }, [voiceKeys, voicesEpoch, workspace]);

  const providers = useMemo(() => payload?.options?.providers || [], [payload]);

  // Ask the provider for its models and keep those that fit the purpose.
  const discover = (purposeId, provider) => {
    setFound((f) => ({ ...f, [purposeId]: { provider, loading: true } }));
    discoverWorkspaceSpecialModels(workspace, purposeId, provider)
      .then(({ data }) => {
        setFound((f) => ({
          ...f, [purposeId]: { provider, models: data.models || [], total: data.total || 0, voices: data.voices },
        }));
        // What was downloaded since may have voices the last answer lacked.
        voicesAsked.current = new Set([...voicesAsked.current].filter((k) => !k.startsWith(`${provider}|`)));
        setVoicesEpoch((n) => n + 1);
      })
      .catch((e) => setFound((f) => ({
        ...f, [purposeId]: { provider, error: errorDetail(e) || t('workspaceDetails.specialModels.discoverFailed') },
      })));
  };

  // Check what the form holds now, saved or not; nothing is run or stored.
  const runCheck = (key, sig, body) => {
    setChecks((c) => ({ ...c, [key]: { sig, loading: true } }));
    checkWorkspaceSpecialModel(workspace, body)
      .then(({ data }) => setChecks((c) => ({ ...c, [key]: { sig, ...data } })))
      .catch((e) => setChecks((c) => ({
        ...c, [key]: { sig, status: 'error', message: errorDetail(e) || t('workspaceDetails.specialModels.checkFailed') },
      })));
  };

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
  // A voice belongs to the model it was picked for: another provider or model
  // drops it, so the picker never offers a voice the new model lacks.
  const setPurpose = (id, patch) => setDraft((d) => {
    const next = { ...d[id], ...patch };
    const moved = ('provider' in patch && patch.provider !== d[id].provider)
      || ('model' in patch && patch.model.trim() !== d[id].model.trim());
    if (moved && next.options?.voice) {
      const { voice: _dropped, ...options } = next.options;
      next.options = options;
    }
    return { ...d, [id]: next };
  });
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
        const chosen = providers.find((pr) => pr.id === entry.provider);
        const kind = chosen?.kind;
        // The hub's own runtime has what was downloaded into it, nothing a
        // cloud provider's suggestions or voice names would match.
        const localRuntime = !!chosen?.local_runtime;
        const suggestions = kind && !localRuntime ? (p.suggestions[kind] || []) : [];
        // A search made for another provider says nothing about this one.
        const search = found[p.id]?.provider === entry.provider ? found[p.id] : null;
        const listed = search?.models || suggestions;
        // Until the model's own answer comes: what a search at the provider
        // said about it, or the voices known for the provider's API shape.
        const voiceNames = localRuntime
          ? (search?.voices?.[entry.model.trim()] || [])
          : (kind ? (p.voices?.[kind] || []) : []);
        const voiceInfo = entry.model.trim() ? voices[`${entry.provider}|${entry.model.trim()}`] : null;
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
              <div className="text-xs font-medium text-gray-600">
                <span className="flex items-center justify-between gap-2">
                  <label htmlFor={`special-${workspace}-${p.id}-model`}>{t('workspaceDetails.specialModels.model')}</label>
                  {entry.provider && (
                    <button
                      type="button"
                      onClick={() => discover(p.id, entry.provider)}
                      disabled={search?.loading}
                      data-testid={`special-${p.id}-discover`}
                      title={t('workspaceDetails.specialModels.discoverHint')}
                      className="inline-flex items-center gap-1 font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
                    >
                      {search?.loading ? <Loader className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                      {t('workspaceDetails.specialModels.discover')}
                    </button>
                  )}
                </span>
                <input
                  id={`special-${workspace}-${p.id}-model`}
                  list={`special-${p.id}-models`}
                  value={entry.model}
                  disabled={!entry.provider}
                  placeholder={suggestions[0] || ''}
                  onChange={(e) => setPurpose(p.id, { model: e.target.value })}
                  className={`${inputCls} mt-1`}
                />
                <datalist id={`special-${p.id}-models`}>
                  {listed.map((m) => <option key={m} value={m} />)}
                </datalist>
              </div>
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
            {localRuntime && (!search || (!search.loading && !search.error && search.models.length === 0)) && (
              <p className="text-xs text-gray-500" data-testid={`special-${p.id}-local-hint`}>
                {t('workspaceDetails.specialModels.localRuntimeHint')}{' '}
                <Link to="/models?tab=local" className="text-indigo-600 hover:text-indigo-800">
                  {t('workspaceDetails.specialModels.localRuntimeLink')}
                </Link>
              </p>
            )}
            {search && !search.loading && (
              <div className="space-y-1.5" data-testid={`special-${p.id}-found`}>
                {search.error ? (
                  <p className="text-xs text-red-600">{search.error}</p>
                ) : (
                  <p className="text-xs text-gray-500">
                    {search.models.length
                      ? t('workspaceDetails.specialModels.discoverFound', { count: search.models.length, total: search.total })
                      : t('workspaceDetails.specialModels.discoverNone', { total: search.total })}
                  </p>
                )}
                {search.models?.length > 0 && (
                  <div className="flex flex-wrap gap-1.5">
                    {search.models.slice(0, FOUND_SHOWN).map((m) => (
                      <button
                        key={m}
                        type="button"
                        onClick={() => setPurpose(p.id, { model: m })}
                        className={`px-2 py-0.5 rounded-md border text-xs font-mono ${
                          entry.model === m
                            ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                            : 'border-gray-200 text-gray-700 hover:border-indigo-300 hover:text-indigo-700'}`}
                      >
                        {m}
                      </button>
                    ))}
                    {search.models.length > FOUND_SHOWN && (
                      <span className="px-1 py-0.5 text-xs text-gray-400">
                        {t('workspaceDetails.specialModels.discoverMore', { count: search.models.length - FOUND_SHOWN })}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )}
            {((entry.provider && entry.model.trim()) || (!entry.provider && inherited(p.id))) && (
              <ConnectionCheck
                id={p.id}
                sig={`${entry.provider}|${entry.model.trim()}`}
                state={checks[p.id]}
                onCheck={() => runCheck(p.id, `${entry.provider}|${entry.model.trim()}`,
                  { purpose: p.id, provider: entry.provider, model: entry.model.trim() })}
              />
            )}
            {entry.provider && Object.keys(p.options).length > 0 && (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                {Object.entries(p.options).map(([key, hint]) => (key === 'voice' ? (
                  <VoicePicker
                    key={key}
                    id={p.id}
                    workspace={workspace}
                    value={entry.options.voice || ''}
                    onChange={(v) => setPurpose(p.id, { options: { ...entry.options, voice: v } })}
                    hint={hint}
                    info={voiceInfo}
                    fallbackNames={voiceNames}
                    sample={entry.model.trim() ? {
                      provider: entry.provider, model: entry.model.trim(),
                      options: Object.fromEntries(Object.entries(entry.options || {})
                        .filter(([k, v]) => k !== 'voice' && String(v).trim())),
                    } : null}
                  />
                ) : (
                  <label key={key} className="text-xs font-medium text-gray-600">
                    {t(`workspaceDetails.specialModels.options.${key}`)}
                    <input
                      value={entry.options[key] || ''}
                      placeholder={hint}
                      onChange={(e) => setPurpose(p.id, { options: { ...entry.options, [key]: e.target.value } })}
                      className={`${inputCls} mt-1`}
                    />
                  </label>
                )))}
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
            <div className="flex flex-wrap items-center justify-between gap-2">
              {customCheckable(c) ? (
                <ConnectionCheck
                  id={`custom-${index}`}
                  sig={customSig(c)}
                  state={checks[`custom-${index}`]}
                  onCheck={() => runCheck(`custom-${index}`, customSig(c), { custom: customBody(c) })}
                />
              ) : <span />}
              <button type="button"
                      onClick={() => {
                        setDraft((d) => ({ ...d, custom: d.custom.filter((_, i) => i !== index) }));
                        setChecks({});
                      }}
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
