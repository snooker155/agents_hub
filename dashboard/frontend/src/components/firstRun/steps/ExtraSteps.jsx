/**
 * The screens after the voice, each with a "not now" or a plain choice: web
 * search, personal memory, the demo workspace. A screen whose answer is
 * already in place (a voice set by the local set, search through the model's
 * own key, the demo already there) says so and only asks to go on.
 */
import { useEffect, useState } from 'react';
import {
  Brain, BrainCircuit, ExternalLink, Globe, LayoutGrid, Sparkles, SquareDashed,
} from 'lucide-react';
import { useI18n } from '../../../i18n';
import { updateSettings } from '../../../api/models';
import { setDemo } from '../../../api/demo';
import { getWorkspacePersonalMemory, updateWorkspacePersonalMemory } from '../../../api/workspaces';
import {
  ChoiceCard, DoneNote, ErrorLine, LaterButton, Pills, PrimaryButton, StepFrame,
} from '../ui';
import { PROVIDER_LABELS, errorText, fieldCls } from '../firstRunModel';

/** Runs one save, then moves on; keeps the error on screen if it fails. */
function useSave(next) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = async (fn) => {
    setBusy(true);
    setError('');
    try {
      await fn();
      next();
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

const SEARCH = [
  { id: 'brave', label: 'Brave', page: 'https://api-dashboard.search.brave.com/app/keys' },
  { id: 'tavily', label: 'Tavily', page: 'https://app.tavily.com/home' },
  { id: 'exa', label: 'Exa', page: 'https://dashboard.exa.ai/api-keys' },
];

export function SearchStep({ ctx, refreshCtx, next }) {
  const { t } = useI18n();
  const { busy, error, run } = useSave(next);
  const [service, setService] = useState('brave');
  const [key, setKey] = useState('');
  const search = ctx?.search || {};
  const on = Boolean(search.key_set);
  const page = SEARCH.find((s) => s.id === service)?.page;

  const save = () => run(async () => {
    await updateSettings({ web_search_provider: service, web_search_api_key: key.trim() });
    setKey('');
    await refreshCtx();
  });

  return (
    <StepFrame
      icon={Globe}
      title={t('firstRun.search.title')}
      subtitle={t('firstRun.search.subtitle')}
      testId="first-run-search"
      footer={on ? (
        <PrimaryButton onClick={() => next()} testId="first-run-continue">{t('firstRun.continue')}</PrimaryButton>
      ) : (
        <>
          <PrimaryButton onClick={save} busy={busy} disabled={!key.trim()} testId="first-run-continue">{t('firstRun.search.turnOn')}</PrimaryButton>
          <LaterButton onClick={() => next()} testId="first-run-later">{t('firstRun.notNow')}</LaterButton>
        </>
      )}
    >
      {on ? (
        <DoneNote
          testId="first-run-search-on"
          title={t('firstRun.search.on')}
          detail={search.source === 'model_key'
            ? t('firstRun.search.viaModel', { provider: PROVIDER_LABELS[search.provider] || search.provider })
            : t('firstRun.search.via', { provider: search.provider })}
        />
      ) : (
        <div className="space-y-4">
          <p className="text-sm text-center text-gray-500">{t('firstRun.search.why')}</p>
          <Pills items={SEARCH} value={service} onChange={setService} />
          <input
            type="password"
            autoComplete="new-password"
            aria-label={t('firstRun.search.key')}
            placeholder={t('firstRun.search.keyPlaceholder', { service: SEARCH.find((s) => s.id === service)?.label })}
            value={key}
            onChange={(e) => setKey(e.target.value)}
            className={fieldCls}
            data-testid="first-run-search-key"
          />
          {page && (
            <a href={page} target="_blank" rel="noreferrer"
              className="flex items-center justify-center gap-1 text-sm text-indigo-600 hover:text-indigo-800">
              {t('firstRun.search.getKey')}<ExternalLink className="w-3.5 h-3.5" />
            </a>
          )}
        </div>
      )}
      <ErrorLine>{error}</ErrorLine>
    </StepFrame>
  );
}

export function MemoryStep({ next }) {
  const { t } = useI18n();
  const { busy, error, run } = useSave(next);
  const [was, setWas] = useState(null);
  const [enabled, setEnabled] = useState(true);
  useEffect(() => {
    let live = true;
    getWorkspacePersonalMemory('default')
      .then(({ data }) => { if (live) { setWas(Boolean(data?.enabled)); setEnabled(Boolean(data?.enabled)); } })
      .catch(() => { if (live) setWas(true); });
    return () => { live = false; };
  }, []);

  const save = () => run(async () => {
    if (was !== null && enabled !== was) await updateWorkspacePersonalMemory('default', enabled);
  });

  return (
    <StepFrame
      icon={BrainCircuit}
      title={t('firstRun.memory.title')}
      subtitle={t('firstRun.memory.subtitle')}
      testId="first-run-memory"
      footer={<PrimaryButton onClick={save} busy={busy} disabled={was === null} testId="first-run-continue">{t('firstRun.continue')}</PrimaryButton>}
    >
      <div className="space-y-3">
        <ChoiceCard icon={Brain} title={t('firstRun.memory.on')} detail={t('firstRun.memory.onDetail')}
          selected={enabled} onClick={() => setEnabled(true)} testId="first-run-memory-on" />
        <ChoiceCard icon={SquareDashed} title={t('firstRun.memory.off')} detail={t('firstRun.memory.offDetail')}
          selected={!enabled} onClick={() => setEnabled(false)} testId="first-run-memory-off" />
      </div>
      <p className="mt-4 text-sm text-center text-gray-400">{t('firstRun.memory.later')}</p>
      <ErrorLine>{error}</ErrorLine>
    </StepFrame>
  );
}

export function DemoStep({ ctx, refreshCtx, next }) {
  const { t } = useI18n();
  const { busy, error, run } = useSave(next);
  const [add, setAdd] = useState(true);
  const present = Boolean(ctx?.demo);

  const save = () => run(async () => {
    if (add && !present) {
      await setDemo(true);
      await refreshCtx();
    }
  });

  return (
    <StepFrame
      icon={LayoutGrid}
      title={t('firstRun.demo.title')}
      subtitle={t('firstRun.demo.subtitle')}
      testId="first-run-demo"
      footer={<PrimaryButton onClick={present ? () => next() : save} busy={busy} testId="first-run-continue">{t('firstRun.continue')}</PrimaryButton>}
    >
      {present ? (
        <DoneNote title={t('firstRun.demo.present')} detail={t('firstRun.demo.presentDetail')} />
      ) : (
        <div className="space-y-3">
          <ChoiceCard icon={Sparkles} title={t('firstRun.demo.add')} detail={t('firstRun.demo.addDetail')}
            selected={add} onClick={() => setAdd(true)} testId="first-run-demo-add" />
          <ChoiceCard icon={SquareDashed} title={t('firstRun.demo.empty')} detail={t('firstRun.demo.emptyDetail')}
            selected={!add} onClick={() => setAdd(false)} testId="first-run-demo-empty" />
        </div>
      )}
      <ErrorLine>{error}</ErrorLine>
    </StepFrame>
  );
}
