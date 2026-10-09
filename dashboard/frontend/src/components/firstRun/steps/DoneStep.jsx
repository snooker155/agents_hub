/**
 * The last screen: what is set, and the way into the app. Each button
 * finishes the first run; they differ only in where the app opens.
 */
import { useState } from 'react';
import { Bot, Compass, MessageSquare } from 'lucide-react';
import LiveMark from '../../liveMark/LiveMark';
import { LANGUAGES, useI18n } from '../../../i18n';
import { useTheme } from '../../theme';
import { ErrorLine, PrimaryButton } from '../ui';
import { PROVIDER_LABELS, errorText, localSetStarted } from '../firstRunModel';

function Row({ label, value }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-2.5 border-b border-gray-100 last:border-0">
      <span className="text-sm text-gray-500">{label}</span>
      <span className="text-sm font-medium text-gray-900 text-right break-words min-w-0">{value}</span>
    </div>
  );
}

export default function DoneStep({ ctx, onFinish, assistantReady = false }) {
  const { t, language } = useI18n();
  const { theme } = useTheme();
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const finish = async (target) => {
    setBusy(target);
    setError('');
    try {
      await onFinish(target);
    } catch (e) {
      setError(errorText(e, t));
      setBusy('');
    }
  };

  const dm = ctx?.default_model || {};
  const model = dm.model
    ? `${PROVIDER_LABELS[dm.provider] || dm.provider} · ${dm.model}`
    : localSetStarted(ctx) ? t('firstRun.done.downloading') : t('firstRun.done.none');
  const search = ctx?.search?.key_set ? (PROVIDER_LABELS[ctx.search.provider] || ctx.search.provider) : t('firstRun.done.off');
  const voice = ctx?.voice || localSetStarted(ctx) ? t('firstRun.done.voiceOwn') : t('firstRun.done.voiceBrowser');

  return (
    <section className="w-full max-w-xl mx-auto flex flex-col flex-1 min-h-0" data-testid="first-run-done">
      <div className="flex-1 min-h-0 overflow-y-auto px-1">
        <div className="flex flex-col items-center text-center pt-2 sm:pt-6">
          <LiveMark state="idle" size={80} frame="logo" label="Agents Hub" />
          <h1 className="mt-6 text-3xl font-semibold tracking-tight text-gray-900">{t('firstRun.done.title')}</h1>
          <p className="mt-3 text-base text-gray-500 max-w-md">{t('firstRun.done.subtitle')}</p>
        </div>
        <div className="mt-8 rounded-2xl border border-gray-200 bg-white px-5 py-1">
          <Row label={t('firstRun.done.language')} value={LANGUAGES.find((l) => l.code === language)?.label || language} />
          <Row label={t('firstRun.done.look')} value={t(`firstRun.appearance.${theme}`)} />
          <Row label={t('firstRun.done.model')} value={model} />
          <Row label={t('firstRun.done.voice')} value={voice} />
          <Row label={t('firstRun.done.search')} value={search} />
          <Row label={t('firstRun.done.demo')} value={ctx?.demo ? t('firstRun.done.added') : t('firstRun.done.notAdded')} />
        </div>
        <p className="mt-3 text-sm text-center text-gray-400">{t('firstRun.done.changeLater')}</p>
      </div>
      <div className="shrink-0 pt-4 pb-2 flex flex-col items-center gap-3">
        {assistantReady ? (
          <>
            <PrimaryButton onClick={() => finish('assistant')} busy={busy === 'assistant'} disabled={Boolean(busy)} testId="first-run-finish-assistant">
              <Bot className="w-4 h-4" />{t('firstRun.done.assistantPrimary')}
            </PrimaryButton>
            <p className="text-sm text-center text-gray-500 max-w-sm">{t('firstRun.done.assistantWhy')}</p>
          </>
        ) : (
          <PrimaryButton onClick={() => finish('chat')} busy={busy === 'chat'} disabled={Boolean(busy)} testId="first-run-finish">
            <MessageSquare className="w-4 h-4" />{t('firstRun.done.start')}
          </PrimaryButton>
        )}
        <div className="flex flex-wrap justify-center gap-x-6 gap-y-2">
          {assistantReady ? (
            <button type="button" onClick={() => finish('chat')} disabled={Boolean(busy)} data-testid="first-run-finish"
              className="flex items-center gap-1.5 text-sm font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-40">
              <MessageSquare className="w-4 h-4" />{t('firstRun.done.start')}
            </button>
          ) : (
            <button type="button" onClick={() => finish('assistant')} disabled={Boolean(busy)} data-testid="first-run-finish-assistant"
              className="flex items-center gap-1.5 text-sm font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-40">
              <Bot className="w-4 h-4" />{t('firstRun.done.assistant')}
            </button>
          )}
          <button type="button" onClick={() => finish('tour')} disabled={Boolean(busy)} data-testid="first-run-finish-tour"
            className="flex items-center gap-1.5 text-sm font-medium text-indigo-600 hover:text-indigo-800 disabled:opacity-40">
            <Compass className="w-4 h-4" />{t('firstRun.done.tour')}
          </button>
        </div>
        <ErrorLine>{error}</ErrorLine>
      </div>
    </section>
  );
}
