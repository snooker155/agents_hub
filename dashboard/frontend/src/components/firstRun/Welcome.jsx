/**
 * The welcome for an account an administrator made (common/member_welcome.py,
 * docs/installation.md, "The welcome for a new account"): the first run of a
 * person on a multi mode hub, with nothing to set up.
 *
 * Two screens in place of the app: the hub's mark putting itself together and
 * a greeting by name ("Welcome to" typed in each language in turn), then what the administrator set up for them, read only
 * (their workspaces, the model and whether it is fixed, the voice, web search,
 * personal memory, the monthly limit). Their own language and look are the
 * only choices. Docs, Welcome shows it again (components/docs/WelcomeDoc.jsx). The screen is saved on
 * the server, so a reload goes on from there.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Brain, ChevronLeft, Compass, Cpu, Folder, Gauge, Globe, Languages, Lock, MessageSquare, Mic, Palette,
  User, Wallet,
} from 'lucide-react';
import { LANGUAGES, translate, useFormatters, useI18n } from '../../i18n';
import { useTheme } from '../theme';
import { getWelcomeOverview, welcomeAction } from '../../api/firstRun';
import MarkReveal from './MarkReveal';
import useTypewriter from './useTypewriter';
import { ErrorLine, LaterButton, Pills, PrimaryButton } from './ui';
import { PROVIDER_LABELS, errorText } from './firstRunModel';
import './firstRun.css';

const STEPS = ['hello', 'overview'];

// The greeting comes in as the last satellite lands (MarkReveal MARK_REVEAL_MS).
const TEXT_AT_MS = 1900;

/**
 * "Welcome to" typed in each language the hub speaks in turn, the page's own
 * first, with Agents Hub in the brand colour after it, and the person's name
 * on its own line under it.
 */
function HelloScreen({ name, next }) {
  const { t, language } = useI18n();
  const [settled, setSettled] = useState(false);
  const [typing, setTyping] = useState(false);
  const onDone = useCallback(() => setSettled(true), []);
  useEffect(() => {
    const id = setTimeout(() => setTyping(true), TEXT_AT_MS);
    return () => clearTimeout(id);
  }, []);
  const phrases = useMemo(() => {
    const order = [language, ...LANGUAGES.map((l) => l.code).filter((c) => c !== language)];
    return order.map((code) => translate(code, 'firstRun.welcome.hello.lead'));
  }, [language]);
  const { text } = useTypewriter(phrases, typing);
  const lead = t('firstRun.welcome.hello.lead');
  return (
    <section className="flex-1 flex flex-col items-center justify-center text-center" data-testid="welcome-hello">
      <MarkReveal size={272} onDone={onDone} />
      <h1 className="mt-6 text-4xl sm:text-5xl font-semibold tracking-tight text-gray-900 max-w-3xl welcome-rise"
        style={{ animationDelay: `${TEXT_AT_MS}ms` }}
        aria-label={name ? `${lead} Agents Hub, ${name}` : `${lead} Agents Hub`}>
        <span className="block" aria-hidden="true">
          <span data-testid="welcome-typed">{text}</span>
          <span className="welcome-caret" />
          {' '}<span className="text-indigo-600">Agents Hub</span>{name ? ',' : ''}
        </span>
        {name && (
          <span className="block welcome-rise" aria-hidden="true" style={{ animationDelay: `${TEXT_AT_MS + 700}ms` }}>
            {name}
          </span>
        )}
      </h1>
      <p className="mt-4 text-lg text-gray-500 max-w-md welcome-rise" style={{ animationDelay: `${TEXT_AT_MS + 1100}ms` }}>
        {t('firstRun.welcome.hello.subtitle')}
      </p>
      <div className="mt-12 w-full flex justify-center welcome-rise" style={{ animationDelay: `${TEXT_AT_MS + 1350}ms` }}>
        <PrimaryButton onClick={next} testId="welcome-start" disabled={!settled}>{t('firstRun.welcome.hello.start')}</PrimaryButton>
      </div>
    </section>
  );
}

function Card({ title, note, children, testId }) {
  return (
    <div className="rounded-2xl border border-gray-200 bg-white" data-testid={testId}>
      <div className="px-5 pt-4 pb-1 flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
        {note && <span className="text-xs text-gray-400 flex items-center gap-1 whitespace-nowrap">{note}</span>}
      </div>
      <div className="px-5 pb-2">{children}</div>
    </div>
  );
}

/** A setting the person sees and does not change: a lock says so. */
function Row({ icon: Icon, label, value, detail }) {
  return (
    <div className={`flex ${detail ? 'items-start' : 'items-center'} gap-3 py-3 border-b border-gray-100 last:border-0`}>
      <span className="w-8 h-8 shrink-0 rounded-lg bg-gray-100 text-gray-500 flex items-center justify-center">
        <Icon className="w-4 h-4" />
      </span>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-sm text-gray-500">{label}</span>
          <span className="text-sm font-medium text-gray-900 text-right break-words min-w-0">{value}</span>
        </div>
        {detail && <p className="mt-0.5 text-xs text-gray-400">{detail}</p>}
      </div>
    </div>
  );
}

// Web search services by name; a model key that searches is named by its provider.
const SEARCH_LABELS = { brave: 'Brave', tavily: 'Tavily', exa: 'Exa', ...PROVIDER_LABELS };

const modelName = (m) => (m?.model ? `${PROVIDER_LABELS[m.provider] || m.provider} · ${m.model}` : '');

function OverviewScreen({ data, error, onFinish }) {
  const { t, language, setLanguage } = useI18n();
  const { formatNumber } = useFormatters();
  const { theme, setTheme } = useTheme();
  const [busy, setBusy] = useState('');
  const [failed, setFailed] = useState('');

  const finish = async (target) => {
    setBusy(target);
    setFailed('');
    try {
      await onFinish(target);
    } catch (e) {
      setFailed(errorText(e, t));
      setBusy('');
    }
  };

  const w = (k, o) => t(`firstRun.welcome.overview.${k}`, o);
  // The account keeps it too, so another browser opens in it.
  const pickLanguage = (code) => {
    setLanguage(code);
    welcomeAction('progress', { language: code }).catch(() => { /* this browser has it */ });
  };
  const model = data?.model;
  const policy = model?.policy;
  let modelDetail = w('modelFree');
  if (policy?.models?.length === 1) modelDetail = w('modelFixed');
  else if (policy?.models?.length > 1) modelDetail = w('modelChoice', { count: policy.models.length });
  const voice = data?.voice?.speech || data?.voice?.transcription ? w('voiceOn') : w('voiceBrowser');
  const search = data?.search?.on ? (SEARCH_LABELS[data.search.provider] || data.search.provider) : w('off');
  const budget = data?.budget;
  const limit = !budget ? '' : budget.limit_usd > 0
    ? `${formatNumber(budget.limit_usd, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 })} ${w('perMonth')}`
    : w('unlimited');

  return (
    <section className="w-full max-w-xl mx-auto flex flex-col flex-1 min-h-0" data-testid="welcome-overview">
      <div className="min-h-0 overflow-y-auto px-1 pb-4">
        <div className="flex flex-col items-center text-center pt-2 sm:pt-4">
          <div className="w-16 h-16 rounded-2xl bg-indigo-50 text-indigo-600 flex items-center justify-center mb-5">
            <Lock className="w-8 h-8" />
          </div>
          <h1 className="text-2xl sm:text-3xl font-semibold tracking-tight text-gray-900">{w('title')}</h1>
          <p className="mt-3 text-base text-gray-500 max-w-md leading-relaxed">{w('subtitle')}</p>
        </div>
        {!data && !error && <p className="mt-8 text-sm text-center text-gray-400">{t('firstRun.loading')}</p>}
        <ErrorLine>{error}</ErrorLine>
        {data && (
          <div className="mt-8 space-y-4">
            {data.workspaces?.length > 0 && (
              <Card title={w('workspaces')} note={<><Lock className="w-3 h-3" />{w('byAdmin')}</>} testId="welcome-workspaces">
                {data.workspaces.map((ws) => (
                  <Row
                    key={ws.name}
                    icon={ws.personal ? User : Folder}
                    label={ws.personal ? w('personal') : ws.display_name}
                    value={t(`firstRun.welcome.roles.${ws.role}`, { defaultValue: ws.role })}
                  />
                ))}
              </Card>
            )}
            <Card title={w('settings')} note={<><Lock className="w-3 h-3" />{w('byAdmin')}</>} testId="welcome-settings">
              {model && <Row icon={Cpu} label={w('model')} value={modelName(model) || w('none')} detail={modelDetail} />}
              {model?.effort && <Row icon={Gauge} label={w('effort')} value={t(`firstRun.welcome.efforts.${model.effort}`, { defaultValue: model.effort })} />}
              {data.voice && <Row icon={Mic} label={w('voice')} value={voice} />}
              {data.search && <Row icon={Globe} label={w('search')} value={search} />}
              {data.memory && <Row icon={Brain} label={w('memory')} value={data.memory.enabled ? w('on') : w('off')} detail={w('memoryDetail')} />}
              {budget && <Row icon={Wallet} label={w('budget')} value={limit} />}
            </Card>
            <Card title={w('yours')} note={w('yoursNote')} testId="welcome-yours">
              <div className="py-3 space-y-4">
                <div className="space-y-2">
                  <p className="text-sm text-gray-500 flex items-center gap-2"><Languages className="w-4 h-4" />{w('language')}</p>
                  <Pills
                    items={LANGUAGES.map((l) => ({ id: l.code, label: `${l.flag} ${l.label}` }))}
                    value={language}
                    onChange={pickLanguage}
                    testId="welcome-language"
                  />
                </div>
                <div className="space-y-2">
                  <p className="text-sm text-gray-500 flex items-center gap-2"><Palette className="w-4 h-4" />{w('look')}</p>
                  <Pills
                    items={['light', 'dark', 'system'].map((id) => ({ id, label: t(`firstRun.appearance.${id}`) }))}
                    value={theme}
                    onChange={setTheme}
                    testId="welcome-look"
                  />
                </div>
              </div>
            </Card>
            <p className="text-sm text-center text-gray-400">{w('askAdmin')}</p>
          </div>
        )}
      </div>
      <div className="shrink-0 pt-4 pb-2 flex flex-col items-center gap-3">
        <PrimaryButton onClick={() => finish('chat')} busy={busy === 'chat'} disabled={Boolean(busy)} testId="welcome-finish">
          <MessageSquare className="w-4 h-4" />{w('start')}
        </PrimaryButton>
        <LaterButton onClick={() => finish('tour')} disabled={Boolean(busy)} testId="welcome-finish-tour">
          <span className="flex items-center gap-1.5"><Compass className="w-4 h-4" />{w('tour')}</span>
        </LaterButton>
        <ErrorLine>{failed}</ErrorLine>
      </div>
    </section>
  );
}

export default function Welcome({ initial, onFinish }) {
  const { t } = useI18n();
  const [step, setStep] = useState(STEPS.includes(initial?.step) ? initial.step : 'hello');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');

  // Read while the mark plays, so the second screen opens filled.
  useEffect(() => {
    let live = true;
    getWelcomeOverview()
      .then(({ data: got }) => { if (live) setData(got || {}); })
      .catch((e) => { if (live) setError(errorText(e, t)); });
    return () => { live = false; };
    // One read per welcome; the language may change on the second screen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const go = (to) => {
    setStep(to);
    welcomeAction('progress', { step: to }).catch(() => { /* the screen still moves on here */ });
  };

  const finish = async (target) => {
    await welcomeAction('finish');
    onFinish(target);
  };

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-gray-50 first-run" data-testid="welcome" role="main">
      <header className="shrink-0 h-14 px-4 sm:px-6 flex items-center">
        {step === 'overview' && (
          <button type="button" onClick={() => go('hello')} data-testid="welcome-back"
            className="flex items-center gap-0.5 text-sm font-medium text-indigo-600 hover:text-indigo-800">
            <ChevronLeft className="w-5 h-5" />{t('firstRun.back')}
          </button>
        )}
      </header>
      <div key={step} className={`flex-1 min-h-0 flex flex-col px-4 sm:px-6 pb-4 sm:pb-8 ${step === 'hello' ? '' : 'first-run-enter'}`}>
        {step === 'hello'
          ? <HelloScreen name={String(initial?.name || data?.person?.name || '').trim()} next={() => go('overview')} />
          : <OverviewScreen data={data} error={error} onFinish={finish} />}
      </div>
    </div>
  );
}
