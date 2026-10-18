/**
 * The one step nobody passes without: a model to think with. Two ways in,
 * each its own screen inside the step: a cloud key (checked with the
 * provider before it is saved) or a model server already running on this
 * machine (Ollama, LM Studio). Then which of the provider's models is the
 * default.
 */
import { useState } from 'react';
import { Brain, Cloud, Cpu, ExternalLink, Gauge, Server, Sparkles, Zap } from 'lucide-react';
import { useI18n } from '../../../i18n';
import { connectFirstModel } from '../../../api/setupGuide';
import { firstRunOp } from '../../../api/firstRun';
import {
  ChoiceCard, ConfiguredFrame, DoneNote, ErrorLine, LaterButton, Pills, PrimaryButton, StepFrame,
} from '../ui';
import {
  CLOUD, PROVIDER_LABELS, SERVERS, TIERED, errorText, fieldCls,
} from '../firstRunModel';

const KEY_PAGES = {
  openai: 'https://platform.openai.com/api-keys',
  anthropic: 'https://console.anthropic.com/settings/keys',
  google: 'https://aistudio.google.com/apikey',
};
const SERVER_URLS = { ollama: 'http://localhost:11434', lmstudio: 'http://localhost:1234' };

export function ModelStep({ ctx, refreshCtx, next }) {
  const { t } = useI18n();
  const connected = (ctx?.providers || []).length > 0;
  const [way, setWay] = useState(connected ? 'connected' : '');
  const [provider, setProvider] = useState('openai');
  const [server, setServer] = useState('ollama');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

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
        <DoneNote
          testId="first-run-model-connected"
          title={t('firstRun.model.connected', { providers: names })}
          detail={dm.model ? t('firstRun.model.defaultIs', { model: `${PROVIDER_LABELS[dm.provider] || dm.provider} · ${dm.model}` }) : ''}
        />
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

      <ErrorLine>{error}</ErrorLine>
    </StepFrame>
  );
}

const TIERS = [
  { id: 'fast', icon: Zap },
  { id: 'balanced', icon: Gauge },
  { id: 'strong', icon: Sparkles },
];

export function ChooseModelStep({ ctx, options, optionsError, refreshCtx, next, configured }) {
  const { t } = useI18n();
  const [changing, setChanging] = useState(false);
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

  const was = configured?.model;
  if (was && !changing) {
    const label = `${PROVIDER_LABELS[was.provider] || was.provider} · ${was.model}`;
    return (
      <ConfiguredFrame icon={Gauge} title={t('firstRun.choose.title')} subtitle={t('firstRun.choose.subtitle')}
        summary={label} onContinue={() => next()} onChange={() => setChanging(true)}
        testId="first-run-choose" t={t} />
    );
  }

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
