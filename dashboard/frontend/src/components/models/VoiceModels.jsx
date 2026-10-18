import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, AudioLines, CheckCircle2, Loader, Mic, Play, Plug, RefreshCw, Save, Square, XCircle,
} from 'lucide-react';
import {
  checkVoiceModel, discoverVoiceModels, getVoiceModelVoices, getVoiceModels, sampleVoiceModel, updateVoiceModels,
} from '../../api';
import { SectionCard, inputCls } from '../settingsUi';
import SpeechLanguagesField from './SpeechLanguagesField';
import { useToast, errorDetail } from '../toast';
import { useI18n } from '../../i18n';
import useVoiceSample from '../useVoiceSample';

const ICONS = { speech: AudioLines, transcription: Mic };

// How many found models show as buttons; the rest stay in the field's list.
const FOUND_SHOWN = 24;

// Options the form shows; the voice has a picker of its own.
const SHOWN_OPTIONS = ['voice', 'format', 'language'];

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
        data-testid={`voice-${id}-check`}
        title={t('models.voice.checkHint')}
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-gray-200 text-xs font-medium text-gray-700 hover:border-indigo-300 hover:text-indigo-700 disabled:opacity-50"
      >
        {shown?.loading ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Plug className="w-3.5 h-3.5" />}
        {shown?.loading ? t('models.voice.checking') : t('models.voice.check')}
      </button>
      {look && (
        <span className={`inline-flex items-start gap-1 text-xs ${look.cls}`} data-testid={`voice-${id}-check-result`}>
          <look.Icon className="w-3.5 h-3.5 mt-px shrink-0" /> {shown.message}
        </span>
      )}
    </div>
  );
}

/**
 * The voice of a speech model: a list of the voices known for it, or a free
 * field when none are known, and a button that plays a short line with the
 * chosen voice in the page's language. ``sample`` is what the backend reads
 * it with, null while there is no model to read it.
 */
function VoicePicker({ id, value, onChange, hint, info, fallbackNames, sample }) {
  const { t, language } = useI18n();
  const names = info?.voices || fallbackNames;
  const languages = info?.languages || {};
  const single = Boolean(info?.own) && names.length === 0;
  // A sample of other values than the form holds now is not shown.
  const { state, toggle, busy } = useVoiceSample(JSON.stringify({ ...sample, voice: value }));
  const play = () => toggle(
    (signal) => sampleVoiceModel({ ...sample, voice: value, language }, { signal }),
    t('models.voice.sampleFailed'),
  );

  const fieldId = `voice-${id}-voice`;
  return (
    <div className="text-xs font-medium text-gray-600 md:col-span-2">
      <span className="flex items-center justify-between gap-2">
        <label htmlFor={fieldId}>{t('models.voice.options.voice')}</label>
        {info?.loading && <Loader className="w-3 h-3 animate-spin text-gray-400" />}
      </span>
      <div className="mt-1 flex items-stretch gap-1.5">
        {names.length > 0 ? (
          <select id={fieldId} value={value} onChange={(e) => onChange(e.target.value)}
                  data-testid={fieldId} className={`${inputCls} bg-white min-w-0`}>
            <option value="">{t('models.voice.voiceDefault')}</option>
            {value && !names.includes(value) && <option value={value}>{value}</option>}
            {names.map((v) => (
              <option key={v} value={v}>{languages[v] ? `${v} (${languages[v]})` : v}</option>
            ))}
          </select>
        ) : (
          <input id={fieldId} value={value} onChange={(e) => onChange(e.target.value)}
                 data-testid={fieldId} disabled={single && !value}
                 placeholder={single ? t('models.voice.singleVoice') : hint}
                 className={`${inputCls} min-w-0`} />
        )}
        <button
          type="button"
          onClick={play}
          disabled={!sample}
          data-testid={`voice-${id}-sample`}
          title={t('models.voice.sampleHint')}
          aria-label={busy ? t('models.voice.sampleStop') : t('models.voice.sample')}
          className="inline-flex shrink-0 items-center gap-1 px-2.5 rounded-md border border-gray-200 text-xs font-medium text-gray-700 hover:border-indigo-300 hover:text-indigo-700 disabled:opacity-50"
        >
          {state.loading ? <Loader className="w-3.5 h-3.5 animate-spin" />
            : state.playing ? <Square className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
          {/* Both labels share one cell, so the button keeps the wider one's
              width and the field beside it does not jump while it plays. */}
          <span className="grid">
            <span className={`col-start-1 row-start-1 ${busy ? 'invisible' : ''}`} aria-hidden={busy}>
              {t('models.voice.sample')}
            </span>
            <span className={`col-start-1 row-start-1 ${busy ? '' : 'invisible'}`} aria-hidden={!busy}>
              {t('models.voice.sampleStop')}
            </span>
          </span>
        </button>
      </div>
      {/* One line is kept for the sample's text or error whether it is shown
          or not, so playing a sample does not push down what lies below. */}
      <div className="mt-1 h-4 font-normal">
        {state.error && (
          <span className="block truncate text-red-600" title={state.error} data-testid={`voice-${id}-sample-error`}>
            {state.error}
          </span>
        )}
        {!state.error && state.text && (
          <span className="block truncate italic text-gray-400" data-testid={`voice-${id}-sample-text`}
                title={`${state.language ? `${state.language}: ` : ''}«${state.text}»`}>
            {state.language ? `${state.language}: ` : ''}«{state.text}»
          </span>
        )}
      </div>
    </div>
  );
}

const priceOrNull = (value) => (value === '' || value === null || value === undefined ? undefined : Number(value));

/**
 * The models the assistant's voice uses (providers/voice_models.py): a
 * transcription model that turns what the person says into text and a
 * speech model that reads the answers aloud. One pair for the hub; a purpose
 * left empty falls back to the browser's own. Saved with its own button.
 */
export default function VoiceModels() {
  const { t } = useI18n();
  const toast = useToast();
  const [payload, setPayload] = useState(null);
  const [draft, setDraft] = useState(null);
  const [saving, setSaving] = useState(false);
  // Per purpose, what the provider listed that fits it: { provider, models, total, loading, error }.
  const [found, setFound] = useState({});
  // Per purpose id, the last connection check.
  const [checks, setChecks] = useState({});
  // Per "provider|model" of a speech model, the voices known for it.
  const [voices, setVoices] = useState({});
  const voicesAsked = useRef(new Set());

  const fromServer = useCallback((data) => {
    setPayload(data);
    const own = data.own || {};
    const next = {};
    for (const p of data.options.purposes) {
      const entry = own[p.id] || {};
      next[p.id] = {
        provider: entry.provider || '', model: entry.model || '',
        price_usd: entry.price_usd ?? '', options: { ...(entry.options || {}) },
        // A voice per language (speech only, providers/speech_languages.py).
        languages: { ...(entry.languages || {}) },
      };
    }
    setDraft(next);
  }, []);

  useEffect(() => {
    getVoiceModels()
      .then(({ data }) => fromServer(data))
      .catch((e) => toast.error(t('models.voice.loadFailed'), errorDetail(e)));
  }, [fromServer, t, toast]);

  // The speech model the form holds now.
  const voiceKey = draft?.speech?.provider && draft.speech.model.trim()
    ? `${draft.speech.provider}|${draft.speech.model.trim()}` : '';

  useEffect(() => {
    if (!voiceKey || voicesAsked.current.has(voiceKey)) return undefined;
    // A model being typed is asked about once the typing stops.
    const timer = setTimeout(() => {
      voicesAsked.current.add(voiceKey);
      const [provider, ...rest] = voiceKey.split('|');
      setVoices((v) => ({ ...v, [voiceKey]: { ...v[voiceKey], loading: true } }));
      getVoiceModelVoices(provider, rest.join('|'))
        .then(({ data }) => setVoices((v) => ({ ...v, [voiceKey]: { ...data, loading: false } })))
        .catch(() => {
          voicesAsked.current.delete(voiceKey);
          setVoices((v) => ({ ...v, [voiceKey]: undefined }));
        });
    }, 350);
    return () => clearTimeout(timer);
  }, [voiceKey]);

  const providers = useMemo(() => payload?.options?.providers || [], [payload]);

  // Ask the provider for its models and keep those that fit the purpose.
  const discover = (purposeId, provider) => {
    setFound((f) => ({ ...f, [purposeId]: { provider, loading: true } }));
    discoverVoiceModels(purposeId, provider)
      .then(({ data }) => setFound((f) => ({
        ...f, [purposeId]: { provider, models: data.models || [], total: data.total || 0 },
      })))
      .catch((e) => setFound((f) => ({
        ...f, [purposeId]: { provider, error: errorDetail(e) || t('models.voice.discoverFailed') },
      })));
  };

  // Check what the form holds now, saved or not; nothing is run or stored.
  const runCheck = (key, sig, body) => {
    setChecks((c) => ({ ...c, [key]: { sig, loading: true } }));
    checkVoiceModel(body)
      .then(({ data }) => setChecks((c) => ({ ...c, [key]: { sig, ...data } })))
      .catch((e) => setChecks((c) => ({
        ...c, [key]: { sig, status: 'error', message: errorDetail(e) || t('models.voice.checkFailed') },
      })));
  };

  if (!payload || !draft) {
    return (
      <p className="text-sm text-gray-500 flex items-center gap-2">
        <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
      </p>
    );
  }

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

  const save = async () => {
    const body = {};
    for (const p of payload.options.purposes) {
      const entry = draft[p.id];
      if (!entry.provider && !entry.model) continue;
      body[p.id] = {
        provider: entry.provider, model: entry.model.trim(),
        price_usd: priceOrNull(entry.price_usd),
        options: Object.fromEntries(Object.entries(entry.options || {}).filter(([, v]) => String(v).trim())),
        // Kept only with the provider they were set for: another provider's models are not these.
        ...(p.id === 'speech' && entry.provider === (payload.own?.speech?.provider || entry.provider)
          && Object.keys(entry.languages || {}).length ? { languages: entry.languages } : {}),
      };
    }
    setSaving(true);
    try {
      const { data } = await updateVoiceModels(body);
      fromServer(data);
      toast.success(t('models.voice.saved'));
    } catch (e) {
      toast.error(t('models.voice.saveFailed'), errorDetail(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* Floats right and stays at the top while the form scrolls; the intro wraps around it. */}
      <button
        type="button"
        onClick={save}
        disabled={saving}
        className="sticky top-2 z-20 float-right ml-3 inline-flex items-center gap-1.5 px-3 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium shadow-sm hover:bg-indigo-700 disabled:opacity-50"
      >
        {saving ? <Loader className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
        {saving ? t('common.saving') : t('models.voice.save')}
      </button>
      <p className="!mt-0 text-sm text-gray-500 max-w-xl">
        {t('models.voice.intro')}
      </p>
      <div className="clear-both !mt-0" />

      {payload.options.purposes.map((p) => {
        const Icon = ICONS[p.id] || AudioLines;
        const entry = draft[p.id];
        const allowed = providers.filter((pr) => p.kinds.includes(pr.kind));
        const chosen = providers.find((pr) => pr.id === entry.provider);
        const kind = chosen?.kind;
        const suggestions = kind ? (p.suggestions[kind] || []) : [];
        // A search made for another provider says nothing about this one.
        const search = found[p.id]?.provider === entry.provider ? found[p.id] : null;
        const listed = search?.models || suggestions;
        // Until the model's own answer comes: the voices known for the
        // provider's API shape.
        const voiceNames = kind ? (p.voices?.[kind] || []) : [];
        const voiceInfo = entry.model.trim() ? voices[`${entry.provider}|${entry.model.trim()}`] : null;
        const shownOptions = Object.entries(p.options).filter(([key]) => SHOWN_OPTIONS.includes(key));
        return (
          <SectionCard
            key={p.id}
            title={(
              <span className="inline-flex items-center gap-2">
                <Icon className="w-4 h-4 text-indigo-500" /> {t(`models.voice.purposes.${p.id}`)}
              </span>
            )}
          >
            <p className="text-xs text-gray-500 -mt-2">{t(`models.voice.purposeHints.${p.id}`)}</p>
            {!entry.provider && (
              <p className="text-xs text-gray-500" data-testid={`voice-${p.id}-missing`}>
                {t('models.voice.notAdded', { what: t(`models.voice.browser.${p.id}`) })}
              </p>
            )}
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <label className="text-xs font-medium text-gray-600">
                {t('models.voice.provider')}
                <select
                  value={entry.provider}
                  onChange={(e) => setPurpose(p.id, { provider: e.target.value })}
                  data-testid={`voice-${p.id}-provider`}
                  className={`${inputCls} mt-1 bg-white`}
                >
                  <option value="">{t('models.voice.none')}</option>
                  {allowed.map((pr) => <option key={pr.id} value={pr.id}>{pr.label}</option>)}
                </select>
              </label>
              <div className="text-xs font-medium text-gray-600">
                <span className="flex items-center justify-between gap-2">
                  <label htmlFor={`voice-${p.id}-model`}>{t('models.voice.model')}</label>
                  {entry.provider && (
                    <button
                      type="button"
                      onClick={() => discover(p.id, entry.provider)}
                      disabled={search?.loading}
                      data-testid={`voice-${p.id}-discover`}
                      title={t('models.voice.discoverHint')}
                      className="inline-flex items-center gap-1 font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-50"
                    >
                      {search?.loading ? <Loader className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
                      {t('models.voice.discover')}
                    </button>
                  )}
                </span>
                <input
                  id={`voice-${p.id}-model`}
                  list={`voice-${p.id}-models`}
                  value={entry.model}
                  disabled={!entry.provider}
                  placeholder={suggestions[0] || ''}
                  onChange={(e) => setPurpose(p.id, { model: e.target.value })}
                  className={`${inputCls} mt-1`}
                />
                <datalist id={`voice-${p.id}-models`}>
                  {listed.map((m) => <option key={m} value={m} />)}
                </datalist>
              </div>
              <label className="text-xs font-medium text-gray-600">
                {t(`models.voice.units.${p.unit}`)}
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
            {search && !search.loading && (
              <div className="space-y-1.5" data-testid={`voice-${p.id}-found`}>
                {search.error ? (
                  <p className="text-xs text-red-600">{search.error}</p>
                ) : (
                  <p className="text-xs text-gray-500">
                    {search.models.length
                      ? t('models.voice.discoverFound', { count: search.models.length, total: search.total })
                      : t('models.voice.discoverNone', { total: search.total })}
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
                        {t('models.voice.discoverMore', { count: search.models.length - FOUND_SHOWN })}
                      </span>
                    )}
                  </div>
                )}
              </div>
            )}
            {entry.provider && entry.model.trim() && (
              <ConnectionCheck
                id={p.id}
                sig={`${entry.provider}|${entry.model.trim()}`}
                state={checks[p.id]}
                onCheck={() => runCheck(p.id, `${entry.provider}|${entry.model.trim()}`,
                  { purpose: p.id, provider: entry.provider, model: entry.model.trim() })}
              />
            )}
            {entry.provider && shownOptions.length > 0 && (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                {shownOptions.map(([key, hint]) => (key === 'voice' ? (
                  <VoicePicker
                    key={key}
                    id={p.id}
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
                    {t(`models.voice.options.${key}`)}
                    <input
                      value={entry.options[key] || ''}
                      placeholder={hint}
                      data-testid={`voice-${p.id}-${key}`}
                      onChange={(e) => setPurpose(p.id, { options: { ...entry.options, [key]: e.target.value } })}
                      className={`${inputCls} mt-1`}
                    />
                  </label>
                )))}
              </div>
            )}
            {p.id === 'speech' && entry.provider && (
              <SpeechLanguagesField
                value={entry.languages}
                onChange={(languages) => setPurpose(p.id, { languages })}
                model={entry.model.trim()}
                voice={entry.options.voice || ''}
              />
            )}
          </SectionCard>
        );
      })}
    </div>
  );
}
