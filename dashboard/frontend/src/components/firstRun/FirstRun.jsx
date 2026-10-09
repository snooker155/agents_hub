/**
 * The first run itself (docs/installation.md, "The first run in the browser"): a screen of its own in place of
 * the whole app, one decision per screen, the way a new phone sets itself
 * up. No sidebar, no header, no link out: the only ways on are the step's
 * own buttons, and the only way out is the last screen.
 *
 * The step is saved on the server as it changes, so a reload, or another
 * browser, goes on where it stopped. A saved step past the model screen with
 * no model in place (one removed meanwhile) goes back to the model screen:
 * nothing after it works without one.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ChevronLeft } from 'lucide-react';
import { useI18n } from '../../i18n';
import { firstRunAction, getFirstRunContext, getFirstRunOptions } from '../../api/firstRun';
import { AppearanceStep, HelloStep, LanguageStep } from './steps/WelcomeSteps';
import { ChooseModelStep, ModelStep } from './steps/ModelSteps';
import { DemoStep, MemoryStep, SearchStep } from './steps/ExtraSteps';
import VoiceStep from './steps/VoiceStep';
import AssistDock from './AssistDock';
import useWizardAssistant from './useWizardAssistant';
import DoneStep from './steps/DoneStep';
import { MODEL_AT, STEPS, hasModel, stepFrom, visibleSteps } from './firstRunModel';
import './firstRun.css';

// The screens that need the providers asked (model tiers, voices).
const NEEDS_OPTIONS = new Set(['choose_model', 'voice']);
// From the voice screen on the assistant can talk: the screens after it carry it.
const ASSISTED = new Set(['search', 'memory', 'demo', 'done']);

export default function FirstRun({ initial, onFinish }) {
  const { t, language } = useI18n();
  // One assistant for the whole run: one voice, one microphone, one thread.
  const assistant = useWizardAssistant({ language });
  // What the voice check found: the voice heard, the microphone heard.
  const [checked, setChecked] = useState(null);
  const [saved, setSaved] = useState(STEPS.includes(initial?.step) ? initial.step : 'hello');
  const [ctx, setCtx] = useState(null);
  // Options belong to the set of providers they were asked with.
  const [asked, setAsked] = useState({ key: null, data: null, error: '' });

  // The latest context, for a step that refreshes it and moves on in one go:
  // the screens after the model depend on what was just connected.
  const latest = useRef(null);
  const refreshCtx = useCallback(async () => {
    try {
      const { data } = await getFirstRunContext();
      latest.current = data || {};
      setCtx(data || {});
      return data;
    } catch {
      setCtx((c) => c || {});
      return null;
    }
  }, []);

  useEffect(() => {
    let live = true;
    getFirstRunContext()
      .then(({ data }) => { if (live) { latest.current = data || {}; setCtx(data || {}); } })
      .catch(() => { if (live) setCtx({}); });
    return () => { live = false; };
  }, []);

  // Past the model screen with no model (one removed meanwhile): back there.
  const step = ctx && STEPS.indexOf(saved) > MODEL_AT && !hasModel(ctx) ? 'model' : saved;

  const { cancelSpeech } = assistant;
  const go = useCallback((to, extra = {}) => {
    cancelSpeech();
    setSaved(to);
    firstRunAction('progress', { step: to, ...extra }).catch(() => { /* the step still moves on here */ });
  }, [cancelSpeech]);

  const providersKey = (ctx?.providers || []).join(',');
  const current = asked.key === providersKey;
  const options = current ? asked.data : null;
  const optionsError = current ? asked.error : '';
  useEffect(() => {
    if (!ctx || !NEEDS_OPTIONS.has(step) || asked.key === providersKey) return undefined;
    let live = true;
    getFirstRunOptions()
      .then(({ data }) => { if (live) setAsked({ key: providersKey, data: data || {}, error: '' }); })
      .catch((e) => { if (live) setAsked({ key: providersKey, data: null, error: e?.message || 'failed' }); });
    return () => { live = false; };
  }, [ctx, step, asked.key, providersKey]);

  const visible = useMemo(() => visibleSteps(ctx), [ctx]);
  const index = Math.max(0, visible.indexOf(step));
  const next = (extra) => go(stepFrom(latest.current || ctx, step, 1), extra || {});
  const back = () => { if (index > 0) go(stepFrom(ctx, step, -1)); };

  const finish = async (target) => {
    await firstRunAction('finish');
    assistant.cancelSpeech();
    onFinish(target, { voice: checked?.speech === 'ok' });
  };

  const props = { ctx, refreshCtx, options, optionsError, next };
  // The dots count the decisions, not hello and the last screen.
  const dots = visible.filter((s) => s !== 'hello' && s !== 'done');
  const dotAt = dots.indexOf(step);

  let body;
  if (!ctx && step !== 'hello' && step !== 'language' && step !== 'appearance') {
    body = <p className="m-auto text-sm text-gray-400">{t('firstRun.loading')}</p>;
  } else if (step === 'hello') body = <HelloStep next={() => next()} />;
  else if (step === 'language') body = <LanguageStep next={next} />;
  else if (step === 'appearance') body = <AppearanceStep next={next} />;
  else if (step === 'model') body = <ModelStep {...props} />;
  else if (step === 'choose_model') body = <ChooseModelStep {...props} />;
  else if (step === 'voice') body = <VoiceStep {...props} assistant={assistant} onChecked={setChecked} />;
  else if (step === 'search') body = <SearchStep {...props} />;
  else if (step === 'memory') body = <MemoryStep {...props} />;
  else if (step === 'demo') body = <DemoStep {...props} />;
  else body = <DoneStep ctx={ctx} onFinish={finish} assistantReady={checked?.model === 'ok'} />;

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-gray-50 first-run" data-testid="first-run" role="main">
      <header className="shrink-0 h-14 px-4 sm:px-6 flex items-center">
        <div className="w-24">
          {index > 0 && (
            <button type="button" onClick={back} data-testid="first-run-back"
              className="flex items-center gap-0.5 text-sm font-medium text-indigo-600 hover:text-indigo-800">
              <ChevronLeft className="w-5 h-5" />{t('firstRun.back')}
            </button>
          )}
        </div>
        <div className="flex-1 flex justify-center gap-1.5" aria-hidden={dotAt < 0}>
          {dotAt >= 0 && dots.map((s, i) => (
            <span key={s} className={`h-1.5 rounded-full transition-all ${i === dotAt ? 'w-6 bg-indigo-600' : i < dotAt ? 'w-1.5 bg-indigo-300' : 'w-1.5 bg-gray-300'}`} />
          ))}
        </div>
        <div className="w-24" />
      </header>
      <div key={step} className="flex-1 min-h-0 flex flex-col px-4 sm:px-6 pb-4 sm:pb-8 first-run-enter">
        {body}
      </div>
      {ctx && ASSISTED.has(step) && (
        // Keyed by the screen: an answer about the last screen is not shown on this one.
        <AssistDock key={`dock-${step}`} assistant={assistant} screen={step} speak={checked?.speech === 'ok'} />
      )}
    </div>
  );
}
