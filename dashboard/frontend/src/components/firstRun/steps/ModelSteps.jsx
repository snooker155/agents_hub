/**
 * The one step nobody passes without: a model to think with. Three ways in,
 * each its own screen inside the step: a cloud key (checked with the
 * provider before it is saved), a model server already running on this
 * machine (Ollama, LM Studio), or the hub's ready local set downloaded in the
 * background. Then which of the provider's models is the default.
 */
import { useCallback, useEffect, useState } from 'react';
import { Brain, Cloud, Cpu, Download, ExternalLink, Gauge, Server, Sparkles, Zap } from 'lucide-react';
import { useI18n } from '../../../i18n';
import { connectFirstModel } from '../../../api/setupGuide';
import { getReadySet, startReadySet } from '../../../api/localModels';
import { firstRunOp } from '../../../api/firstRun';
import {
  ChoiceCard, DoneNote, ErrorLine, LaterButton, Pills, PrimaryButton, StepFrame,
} from '../ui';
import {
  ACTIVE_JOB as ACTIVE, CLOUD, PROVIDER_LABELS, SERVERS, TIERED, errorText, fieldCls, localSetStarted,
} from '../firstRunModel';

const KEY_PAGES = {
  openai: 'https://platform.openai.com/api-keys',
  anthropic: 'https://console.anthropic.com/settings/keys',
  google: 'https://aistudio.google.com/apikey',
};
const SERVER_URLS = { ollama: 'http://localhost:11434', lmstudio: 'http://localhost:1234' };
const POLL_MS = 2000;

function gb(bytes) {
  return bytes ? `${(bytes / 1e9).toFixed(1)} GB` : '';
}

/** The ready local set: what it would download here, then how far it got. */
function useReadySet(enabled) {
  const [data, setData] = useState(null);
  const load = useCallback(async () => {
    try { const { data: d } = await getReadySet(); setData(d); } catch { setData({ available: false }); }
  }, []);
  useEffect(() => {
    if (!enabled) return undefined;
    let live = true;
    getReadySet()
      .then(({ data: d }) => { if (live) setData(d); })
      .catch(() => { if (live) setData({ available: false }); });
    return () => { live = false; };
  }, [enabled]);
  const running = ACTIVE.has(data?.job?.status);
  useEffect(() => {
    if (!running) return undefined;
    const id = setTimeout(load, POLL_MS);
    return () => clearTimeout(id);
  }, [running, data, load]);
  return { data, reload: load, running };
}

export function ModelStep({ ctx, refreshCtx, next }) {
  const { t } = useI18n();
  const connected = (ctx?.providers || []).length > 0;
  const [way, setWay] = useState(connected || localSetStarted(ctx) ? 'connected' : '');
  const [provider, setProvider] = useState('openai');
  const [server, setServer] = useState('ollama');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const ready = useReadySet(Boolean(ctx?.runtime));
  const setRunning = ready.running || localSetStarted(ctx);

  const connect = async () => {
    setBusy(true);
    setError('');
    try {
      const body = way === 'cloud'
        ? { provider, api_key: apiKey.trim(), base_url: '' }
        : { provider: server, api_key: '', base_url: baseUrl.trim() };
      await connectFirstModel(body);
      setApiKey('');
      await refreshCtx();
      next();
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(false);
    }
  };

  const download = async () => {
    setBusy(true);
    setError('');
    try {
      await startReadySet('default');
      await ready.reload();
      await refreshCtx();
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(false);
    }
  };

  const pick = (w) => { setWay(w); setError(''); };

  // Already connected (`ah setup`, or back from a later step): say so.
  if (way === 'connected') {
    const dm = ctx?.default_model || {};
    const names = (ctx?.providers || []).map((p) => PROVIDER_LABELS[p] || p).join(', ');
    return (
      <StepFrame
        icon={Brain}
        title={t('firstRun.model.title')}
        subtitle={t('firstRun.model.subtitle')}
        testId="first-run-model"
        footer={(
          <>
            <PrimaryButton onClick={() => next()} testId="first-run-continue">{t('firstRun.continue')}</PrimaryButton>
            <LaterButton onClick={() => pick('')} testId="first-run-model-other">{t('firstRun.model.another')}</LaterButton>
          </>
        )}
      >
        {connected ? (
          <DoneNote
            testId="first-run-model-connected"
            title={t('firstRun.model.connected', { providers: names })}
            detail={dm.model ? t('firstRun.model.defaultIs', { model: `${PROVIDER_LABELS[dm.provider] || dm.provider} · ${dm.model}` }) : ''}
          />
        ) : (
          <DownloadProgress ready={ready} t={t} />
        )}
      </StepFrame>
    );
  }

  const footer = way === 'cloud' || way === 'server' ? (
    <>
      <PrimaryButton
        onClick={connect}
        busy={busy}
        disabled={way === 'cloud' && !apiKey.trim()}
        testId="first-run-connect"
      >
        {busy ? t('firstRun.model.checking') : t('firstRun.model.connect')}
      </PrimaryButton>
      <LaterButton onClick={() => pick('')}>{t('firstRun.model.otherWay')}</LaterButton>
    </>
  ) : way === 'download' ? (
    <>
      {setRunning ? (
        <PrimaryButton onClick={() => next()} testId="first-run-continue">{t('firstRun.continue')}</PrimaryButton>
      ) : (
        <PrimaryButton onClick={download} busy={busy} disabled={!ready.data?.chat} testId="first-run-download">
          {t('firstRun.model.download')}
        </PrimaryButton>
      )}
      {!setRunning && <LaterButton onClick={() => pick('')}>{t('firstRun.model.otherWay')}</LaterButton>}
    </>
  ) : (
    <p className="text-sm text-gray-400 text-center max-w-sm">{t('firstRun.model.required')}</p>
  );

  return (
    <StepFrame
      icon={Brain}
      title={t('firstRun.model.title')}
      subtitle={t('firstRun.model.subtitle')}
      testId="first-run-model"
      footer={footer}
    >
      {way === '' && (
        <div className="space-y-3">
          <ChoiceCard icon={Cloud} title={t('firstRun.model.cloud')} detail={t('firstRun.model.cloudDetail')}
            onClick={() => pick('cloud')} testId="first-run-way-cloud" />
          <ChoiceCard icon={Server} title={t('firstRun.model.server')} detail={t('firstRun.model.serverDetail')}
            onClick={() => pick('server')} testId="first-run-way-server" />
          {ctx?.runtime && (
            <ChoiceCard icon={Cpu} title={t('firstRun.model.local')}
              detail={ready.data?.chat
                ? t('firstRun.model.localDetailSized', { model: ready.data.chat.label, size: gb(ready.data.chat.size_bytes) })
                : t('firstRun.model.localDetail')}
              onClick={() => pick('download')} testId="first-run-way-download" />
          )}
        </div>
      )}

      {way === 'cloud' && (
        <div className="space-y-4">
          <Pills items={CLOUD.map((id) => ({ id, label: PROVIDER_LABELS[id] }))} value={provider}
            onChange={(p) => { setProvider(p); setError(''); }} testId="first-run-providers" />
          <input
            type="password"
            autoComplete="new-password"
            aria-label={t('firstRun.model.key')}
            placeholder={t('firstRun.model.keyPlaceholder', { provider: PROVIDER_LABELS[provider] })}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && apiKey.trim()) connect(); }}
            className={fieldCls}
            data-testid="first-run-key"
          />
          <a href={KEY_PAGES[provider]} target="_blank" rel="noreferrer"
            className="flex items-center justify-center gap-1 text-sm text-indigo-600 hover:text-indigo-800">
            {t('firstRun.model.getKey', { provider: PROVIDER_LABELS[provider] })}
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
          <p className="text-xs text-center text-gray-400">{t('firstRun.model.keyPrivacy')}</p>
        </div>
      )}

      {way === 'server' && (
        <div className="space-y-4">
          <Pills items={SERVERS.map((id) => ({ id, label: PROVIDER_LABELS[id] }))} value={server}
            onChange={(s) => { setServer(s); setError(''); }} testId="first-run-servers" />
          <input
            type="text"
            aria-label={t('firstRun.model.address')}
            placeholder={SERVER_URLS[server]}
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            className={fieldCls}
            data-testid="first-run-server-url"
          />
          <p className="text-sm text-center text-gray-500">{t('firstRun.model.serverHint', { server: PROVIDER_LABELS[server] })}</p>
        </div>
      )}

      {way === 'download' && <DownloadProgress ready={ready} t={t} />}

      <ErrorLine>{error}</ErrorLine>
    </StepFrame>
  );
}

function DownloadProgress({ ready, t }) {
  const data = ready.data;
  if (!data) return <p className="text-center text-sm text-gray-400">{t('firstRun.loading')}</p>;
  const job = data.job;
  const started = job && (ACTIVE.has(job.status) || job.status === 'done');
  const pct = Math.round(job?.percent || 0);
  return (
    <div className="rounded-2xl border border-gray-200 bg-white px-5 py-5 space-y-3" data-testid="first-run-download-card">
      <div className="flex items-center gap-3">
        <span className="w-10 h-10 rounded-xl bg-gray-100 text-gray-600 flex items-center justify-center"><Download className="w-5 h-5" /></span>
        <div className="min-w-0">
          <p className="text-base font-semibold text-gray-900">{data.chat?.label || t('firstRun.model.local')}</p>
          <p className="text-sm text-gray-500">
            {[gb(data.chat?.size_bytes), data.hardware?.name].filter(Boolean).join(' · ')}
          </p>
        </div>
      </div>
      <p className="text-sm text-gray-600">{t('firstRun.model.localIncludes')}</p>
      {started && (
        <>
          <div className="w-full h-2 bg-gray-100 rounded-full overflow-hidden">
            <div className="h-full bg-indigo-500 transition-all" style={{ width: `${job.status === 'done' ? 100 : pct}%` }} />
          </div>
          <p className="text-sm text-gray-500">
            {job.status === 'done' ? t('firstRun.model.localDone') : `${pct}% · ${job.message || ''}`}
          </p>
          {job.status !== 'done' && <p className="text-sm text-gray-500">{t('firstRun.model.localGoOn')}</p>}
        </>
      )}
      {job?.status === 'error' && <p className="text-sm text-red-600">{job.error || job.message}</p>}
    </div>
  );
}

const TIERS = [
  { id: 'fast', icon: Zap },
  { id: 'balanced', icon: Gauge },
  { id: 'strong', icon: Sparkles },
];

export function ChooseModelStep({ ctx, options, optionsError, refreshCtx, next }) {
  const { t } = useI18n();
  const offered = (ctx?.providers || []).filter((p) => TIERED.includes(p));
  const dm = ctx?.default_model || {};
  const [provider, setProvider] = useState(offered.includes(dm.provider) ? dm.provider : offered[0]);
  const listing = options?.models?.[provider] || {};
  // A local server has no tiers: each model it serves is a choice of its own.
  const served = SERVERS.includes(provider) ? (listing.served || []) : [];
  const tiers = served.length
    ? Object.fromEntries(served.map((m) => [m, { model: m }]))
    : (listing.tiers || {});
  const tierIds = served.length
    ? served.map((m) => ({ id: m, icon: Cpu, served: true }))
    : TIERS.filter((x) => tiers[x.id]?.model);
  const current = tierIds.find((x) => dm.provider === provider && tiers[x.id].model === dm.model)?.id;
  const [chosen, setChosen] = useState('');
  const pickedTier = chosen || current || (tiers.balanced ? 'balanced' : tierIds[0]?.id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const save = async () => {
    const model = tiers[pickedTier]?.model;
    if (!model || (provider === dm.provider && model === dm.model)) { next(); return; }
    setBusy(true);
    setError('');
    try {
      await firstRunOp('choose_model', { provider, model });
      await refreshCtx();
      next();
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <StepFrame
      icon={Gauge}
      title={t('firstRun.choose.title')}
      subtitle={t('firstRun.choose.subtitle')}
      testId="first-run-choose"
      footer={(
        <PrimaryButton onClick={save} busy={busy} disabled={!options && !optionsError} testId="first-run-continue">
          {t('firstRun.continue')}
        </PrimaryButton>
      )}
    >
      {offered.length > 1 && (
        <div className="mb-4">
          <Pills items={offered.map((id) => ({ id, label: PROVIDER_LABELS[id] || id }))} value={provider}
            onChange={(p) => { setProvider(p); setChosen(''); }} />
        </div>
      )}
      {!options && !optionsError && (
        <p className="text-center text-sm text-gray-400" data-testid="first-run-asking">
          {t('firstRun.choose.asking', { provider: PROVIDER_LABELS[provider] || provider })}
        </p>
      )}
      {(options || optionsError) && tierIds.length === 0 && (
        <DoneNote title={t('firstRun.choose.keep')} detail={dm.model ? `${PROVIDER_LABELS[dm.provider] || dm.provider} · ${dm.model}` : ''} />
      )}
      <div className="space-y-3">
        {tierIds.map(({ id, icon, served: own }) => (
          <ChoiceCard
            key={id}
            icon={icon}
            title={own ? id : t(`firstRun.choose.${id}`)}
            badge={!own && id === 'balanced' ? t('firstRun.choose.recommended') : ''}
            detail={own ? t('firstRun.choose.servedDetail', { server: PROVIDER_LABELS[provider] })
              : `${tiers[id].model} · ${t(`firstRun.choose.${id}Detail`)}`}
            selected={pickedTier === id}
            onClick={() => setChosen(id)}
            testId={`first-run-tier-${id}`}
          />
        ))}
      </div>
      <p className="mt-4 text-sm text-center text-gray-400">{t('firstRun.choose.later')}</p>
      <ErrorLine>{error}</ErrorLine>
    </StepFrame>
  );
}
