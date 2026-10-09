/**
 * The voice screen, in two parts.
 *
 * Choose: a cloud voice (OpenAI or Google, with a voice for each page
 * language), the hub's own runtime (a Piper voice per language, downloaded in
 * the background) or the browser's own voice.
 *
 * Check: meet the assistant. It says hello out loud (a real turn: the model,
 * then the speech model, each sentence in its language's voice), the person
 * says whether they heard it, then answers by voice (the transcription model
 * or the browser's recognition) and hears the reply. From here on the
 * assistant is at hand on every screen (AssistDock), and the last screen can
 * hand over to it.
 */
import { useEffect, useRef, useState } from 'react';
import {
  Check, Cloud, Cpu, Loader2, Mic, MicOff, Play, RotateCcw, Square, Volume2, X,
} from 'lucide-react';
import { LANGUAGES, useI18n } from '../../../i18n';
import { firstRunOp } from '../../../api/firstRun';
import { sampleAssistantVoice } from '../../../api/assistant';
import LiveMark from '../../liveMark/LiveMark';
import useSetupGuide from '../../setup/useSetupGuide';
import { browserSpeechAvailable } from '../../assistant/useSpeaker';
import {
  ChoiceCard, DoneNote, ErrorLine, LaterButton, Pills, PrimaryButton, StepFrame,
} from '../ui';
import { PROVIDER_LABELS, errorText, localSetStarted, voiceLabel } from '../firstRunModel';

/** A distinct voice for each language by default; the person can change any. */
const DEFAULT_VOICES = {
  openai: { en: 'nova', ru: 'coral', de: 'shimmer' },
  google: { en: 'Kore', ru: 'Aoede', de: 'Leda' },
};
const SAMPLES = {
  en: 'Hello! This is how I sound in English.',
  ru: 'Привет! Так я звучу по-русски.',
  de: 'Hallo! So klinge ich auf Deutsch.',
};
const LOCALES = { en: 'en-US', ru: 'ru-RU', de: 'de-DE' };

/** The page languages, the chosen one first. */
function languageOrder(current) {
  return [...LANGUAGES].sort((a, b) => (a.code === current ? -1 : b.code === current ? 1 : 0));
}

function defaultVoices(provider, spec) {
  const pref = DEFAULT_VOICES[provider] || {};
  const known = new Set(spec?.voices || []);
  const first = (spec?.voices || [])[0] || '';
  return Object.fromEntries(LANGUAGES.map(({ code }) => [code, known.has(pref[code]) ? pref[code] : first]));
}

// ── choose ───────────────────────────────────────────────────────────────────

function ChooseVoice({ options, optionsError, ctx, refreshCtx, onSaved, next, language }) {
  const { t } = useI18n();
  const cloud = Object.entries(options?.voice?.cloud || {}).filter(([, v]) => v.connected);
  const local = options?.voice?.local;
  const [picked, setChoice] = useState('');
  // With nothing else on offer the browser's own voice is the choice.
  const choice = picked || (options && !cloud.length && !local?.available ? 'browser' : '');
  const [voices, setVoices] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const voicesOf = (provider, spec) => ({ ...defaultVoices(provider, spec), ...(voices[provider] || {}) });

  const save = async () => {
    setBusy(true);
    setError('');
    try {
      if (choice.startsWith('cloud:')) {
        const provider = choice.slice(6);
        const spec = options.voice.cloud[provider];
        const map = voicesOf(provider, spec);
        await firstRunOp('voice_cloud', { provider, voice: map[language] || spec.voices[0], voices: map });
      } else if (choice === 'local') {
        await firstRunOp('voice_local', {});
      }
      await refreshCtx();
      await onSaved(choice === 'browser' ? 'browser' : 'hub');
    } catch (e) {
      setError(errorText(e, t));
    } finally {
      setBusy(false);
    }
  };

  const localSize = local ? (local.speech || []).find((s) => s.id === local.default_speech)?.size : '';
  const order = languageOrder(language);

  return (
    <StepFrame
      icon={Mic}
      title={t('firstRun.voice.title')}
      subtitle={t('firstRun.voice.subtitle')}
      testId="first-run-voice"
      footer={(
        <>
          <PrimaryButton onClick={save} busy={busy} disabled={!choice} testId="first-run-continue">{t('firstRun.continue')}</PrimaryButton>
          <LaterButton onClick={() => next()} testId="first-run-later">{t('firstRun.notNow')}</LaterButton>
        </>
      )}
    >
      {!options && !optionsError ? (
        <p className="text-center text-sm text-gray-400">{t('firstRun.loading')}</p>
      ) : (
        <div className="space-y-3">
          {cloud.map(([provider, spec]) => (
            <div key={provider} className="space-y-2">
              <ChoiceCard
                icon={Cloud}
                title={t('firstRun.voice.cloud', { provider: PROVIDER_LABELS[provider] || provider })}
                detail={t('firstRun.voice.cloudDetail')}
                selected={choice === `cloud:${provider}`}
                onClick={() => setChoice(`cloud:${provider}`)}
                testId={`first-run-voice-cloud-${provider}`}
              />
              {choice === `cloud:${provider}` && (spec.voices || []).length > 0 && (
                <div className="rounded-2xl border border-gray-200 bg-white px-4 py-3 space-y-3" data-testid="first-run-voice-names">
                  <p className="text-xs text-gray-500">{t('firstRun.voice.perLanguage')}</p>
                  {order.map((l) => (
                    <div key={l.code} className="flex flex-col sm:flex-row sm:items-center gap-2">
                      <span className="w-28 shrink-0 text-sm font-medium text-gray-700">{l.flag} {l.label}</span>
                      <select
                        aria-label={t('firstRun.voice.voiceFor', { language: l.label })}
                        value={voicesOf(provider, spec)[l.code]}
                        onChange={(e) => setVoices((m) => ({ ...m, [provider]: { ...(m[provider] || {}), [l.code]: e.target.value } }))}
                        className="flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:outline-none"
                        data-testid={`first-run-voice-${provider}-${l.code}`}
                      >
                        {spec.voices.map((v) => <option key={v} value={v}>{v}</option>)}
                      </select>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
          {local?.available && (
            <div className="space-y-2">
              <ChoiceCard
                icon={Cpu}
                title={t('firstRun.voice.local')}
                detail={t('firstRun.voice.localDetail', { size: localSize || '' })}
                selected={choice === 'local'}
                onClick={() => setChoice('local')}
                testId="first-run-voice-local"
              />
              {choice === 'local' && local.languages && (
                <div className="rounded-2xl border border-gray-200 bg-white px-4 py-3 space-y-1.5" data-testid="first-run-voice-local-languages">
                  <p className="text-xs text-gray-500">{t('firstRun.voice.perLanguageLocal')}</p>
                  {order.filter((l) => local.languages[l.code]).map((l) => (
                    <p key={l.code} className="text-sm text-gray-700">
                      <span className="font-medium">{l.flag} {l.label}:</span> {local.languages[l.code]}
                    </p>
                  ))}
                </div>
              )}
            </div>
          )}
          <ChoiceCard
            icon={Volume2}
            title={t('firstRun.voice.browser')}
            detail={t('firstRun.voice.browserDetail')}
            selected={choice === 'browser'}
            onClick={() => setChoice('browser')}
            testId="first-run-voice-browser"
          />
        </div>
      )}
      {ctx?.local_set && localSetStarted(ctx) && (
        <p className="mt-3 text-sm text-center text-gray-400">{t('firstRun.voice.setNote')}</p>
      )}
      <ErrorLine>{error}</ErrorLine>
    </StepFrame>
  );
}

// ── check ────────────────────────────────────────────────────────────────────

function Mark({ state }) {
  const cls = {
    ok: 'bg-emerald-500 text-white', fail: 'bg-amber-400 text-white', wait: 'bg-gray-200 text-gray-400',
  }[state] || 'bg-gray-200 text-gray-400';
  const Icon = state === 'ok' ? Check : state === 'fail' ? X : null;
  return <span className={`w-5 h-5 rounded-full flex items-center justify-center ${cls}`}>{Icon && <Icon className="w-3.5 h-3.5" />}</span>;
}

/** One row per page language: the voice that reads it, and a sample. */
function LanguageVoices({ assistant, language }) {
  const { t } = useI18n();
  const speech = assistant.voice?.speech;
  const [playing, setPlaying] = useState('');
  const [error, setError] = useState('');

  const describe = (code) => {
    if (!speech) return t('firstRun.voice.browserShort');
    const own = (speech.by_language || {})[code] || {};
    if (own.voice) return own.voice;
    if (own.model) return voiceLabel(own.model);
    return speech.voice || voiceLabel(speech.model);
  };

  const play = async (code) => {
    setError('');
    setPlaying(code);
    try {
      if (speech) {
        const { blob } = await sampleAssistantVoice({ language: code });
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        audio.onended = () => { URL.revokeObjectURL(url); setPlaying(''); };
        await audio.play();
        return;
      }
      if (browserSpeechAvailable()) {
        const u = new window.SpeechSynthesisUtterance(SAMPLES[code]);
        u.lang = LOCALES[code];
        u.onend = () => setPlaying('');
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(u);
        return;
      }
      setPlaying('');
    } catch (e) {
      setError(e?.message || t('firstRun.errors.failed'));
      setPlaying('');
    }
  };

  return (
    <div className="rounded-2xl border border-gray-200 bg-white px-4 py-2" data-testid="first-run-voice-languages">
      {languageOrder(language).map((l) => (
        <div key={l.code} className="flex items-center gap-3 py-2 border-b border-gray-100 last:border-0">
          <span className="w-28 shrink-0 text-sm font-medium text-gray-700">{l.flag} {l.label}</span>
          <span className="flex-1 min-w-0 text-sm text-gray-500 truncate" data-testid={`first-run-voice-of-${l.code}`}>{describe(l.code)}</span>
          <button type="button" onClick={() => play(l.code)} disabled={Boolean(playing)}
            aria-label={t('firstRun.voice.listen', { language: l.label })}
            className="w-9 h-9 rounded-full border border-gray-200 flex items-center justify-center text-indigo-600 hover:bg-indigo-50 disabled:opacity-40"
            data-testid={`first-run-voice-play-${l.code}`}>
            {playing === l.code ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          </button>
        </div>
      ))}
      {error && <p className="py-1 text-xs text-red-600">{error}</p>}
    </div>
  );
}

function VoiceCheck({ assistant, onResult, downloading }) {
  const { t } = useI18n();
  const [stage, setStage] = useState('idle');
  // The next thing to press comes into view as the check goes on.
  const end = useRef(null);
  useEffect(() => {
    if (stage !== 'idle' && typeof end.current?.scrollIntoView === 'function') {
      end.current.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [stage, assistant.reply]);
  const [result, setResult] = useState({ model: 'wait', speech: 'wait', hearing: 'wait' });
  const [noEar, setNoEar] = useState(false);

  const mark = (patch) => {
    const out = { ...result, ...patch };
    setResult(out);
    onResult(out);
  };

  const greet = async () => {
    assistant.unlock();
    setStage('greeting');
    const answer = await assistant.ask(t('firstRun.voice.checkPrompt'), { screen: 'voice' });
    mark({ model: answer ? 'ok' : 'fail' });
    setStage(answer ? 'heard' : 'idle');
  };

  const heard = (yes) => {
    mark({ speech: yes ? 'ok' : 'fail' });
    setNoEar(!yes);
    setStage('talk');
  };

  const talk = async () => {
    if (assistant.listening) {
      const text = await assistant.stopListening();
      if (!text) return;
      mark({ hearing: 'ok' });
      setStage('answering');
      await assistant.ask(text, { spoken: true, screen: 'voice' });
      setStage('done');
      return;
    }
    assistant.unlock();
    await assistant.startListening();
  };

  const markState = assistant.listening ? 'listen' : assistant.speaking ? 'speak'
    : assistant.busy ? 'think' : 'idle';

  return (
    <div className="space-y-4" data-testid="first-run-voice-check">
      <div className="flex flex-col items-center gap-3">
        <LiveMark state={markState} size={72} frame="logo" label="Agents Hub" />
        <div className="flex flex-wrap justify-center gap-x-5 gap-y-1 text-sm text-gray-600">
          {['model', 'speech', 'hearing'].map((k) => (
            <span key={k} className="flex items-center gap-1.5" data-testid={`first-run-check-${k}-${result[k]}`}>
              <Mark state={result[k]} />{t(`firstRun.voice.checks.${k}`)}
            </span>
          ))}
        </div>
      </div>

      {(assistant.heard || assistant.reply) && (
        <div className="space-y-2">
          {assistant.heard && (
            <p className="ml-auto max-w-[85%] w-fit rounded-2xl rounded-br-md bg-indigo-600 text-white px-4 py-2 text-sm" data-testid="first-run-heard">
              {assistant.heard}
            </p>
          )}
          {assistant.reply && (
            <p className="max-w-[85%] w-fit rounded-2xl rounded-bl-md bg-white border border-gray-200 px-4 py-2 text-sm text-gray-800 whitespace-pre-wrap" data-testid="first-run-reply">
              {assistant.reply}
            </p>
          )}
        </div>
      )}

      {assistant.speechFailed && (
        <p className="text-sm text-center text-amber-700">{t('firstRun.voice.hubVoiceFailed')}</p>
      )}
      {assistant.held && <p className="text-sm text-center text-gray-500">{t('firstRun.assist.held')}</p>}
      <ErrorLine>{assistant.error || assistant.micError}</ErrorLine>

      <div className="flex flex-col items-center gap-3">
        {stage === 'idle' && (
          <button type="button" onClick={greet} disabled={downloading}
            className="flex items-center gap-2 rounded-full bg-indigo-600 px-6 py-3 text-white font-semibold shadow-sm hover:bg-indigo-700 disabled:opacity-40"
            data-testid="first-run-greet">
            <Volume2 className="w-5 h-5" />{t('firstRun.voice.greet')}
          </button>
        )}
        {(stage === 'greeting' || stage === 'answering') && (
          <p className="text-sm text-gray-500 flex items-center gap-2">
            <Loader2 className="w-4 h-4 animate-spin" />{t(assistant.speaking ? 'firstRun.voice.speaking' : 'firstRun.voice.thinking')}
          </p>
        )}
        {stage === 'heard' && (
          <div className="flex flex-col items-center gap-2">
            <p className="text-base font-medium text-gray-900">{t('firstRun.voice.didYouHear')}</p>
            <div className="flex flex-wrap justify-center gap-2">
              <button type="button" onClick={() => heard(true)} data-testid="first-run-heard-yes"
                className="rounded-full bg-indigo-600 px-5 py-2 text-sm font-semibold text-white hover:bg-indigo-700">{t('firstRun.voice.yes')}</button>
              <button type="button" onClick={() => assistant.replay()} data-testid="first-run-heard-again"
                className="flex items-center gap-1 rounded-full border border-gray-200 bg-white px-5 py-2 text-sm font-medium text-gray-700">
                <RotateCcw className="w-4 h-4" />{t('firstRun.voice.again')}
              </button>
              <button type="button" onClick={() => heard(false)} data-testid="first-run-heard-no"
                className="rounded-full border border-gray-200 bg-white px-5 py-2 text-sm font-medium text-gray-700">{t('firstRun.voice.no')}</button>
            </div>
          </div>
        )}
        {noEar && stage !== 'heard' && <p className="text-sm text-center text-gray-500 max-w-sm">{t('firstRun.voice.noEarHint')}</p>}
        {stage === 'talk' && (assistant.canListen ? (
          <div className="flex flex-col items-center gap-2">
            <button type="button" onClick={talk}
              aria-label={assistant.listening ? t('firstRun.voice.stop') : t('firstRun.voice.talk')}
              className={`w-16 h-16 rounded-full flex items-center justify-center text-white shadow-md transition-transform ${assistant.listening ? 'bg-red-500 scale-110' : 'bg-indigo-600 hover:bg-indigo-700'}`}
              data-testid="first-run-talk">
              {assistant.listening ? <Square className="w-6 h-6" /> : <Mic className="w-7 h-7" />}
            </button>
            <p className="text-sm text-gray-500 text-center max-w-xs">
              {assistant.listening ? t('firstRun.voice.listening') : t('firstRun.voice.talkHint')}
            </p>
          </div>
        ) : (
          <p className="text-sm text-gray-500 flex items-center gap-2" data-testid="first-run-no-mic">
            <MicOff className="w-4 h-4" />{t('firstRun.voice.noMic')}
          </p>
        ))}
        {stage === 'done' && (
          <DoneNote testId="first-run-voice-ready" title={t('firstRun.voice.ready')} detail={t('firstRun.voice.readyDetail')} />
        )}
        <span ref={end} />
      </div>
    </div>
  );
}

export default function VoiceStep({ ctx, options, optionsError, refreshCtx, next, assistant, onChecked }) {
  const { t, language } = useI18n();
  const settled = Boolean(ctx?.voice) || localSetStarted(ctx);
  const [phase, setPhase] = useState(settled ? 'check' : 'choose');
  const [result, setResult] = useState(null);
  const { guide } = useSetupGuide();
  const work = guide?.work;
  const downloading = Boolean(work && (work.phase === 'starting' || work.phase === 'submitted'));
  const setJob = ctx?.local_set && ['queued', 'running'].includes(ctx.local_set.status);

  const toCheck = async () => {
    await assistant.refreshVoice();
    setPhase('check');
  };

  // The voice models the assistant uses, read when the screen opens.
  const { refreshVoice } = assistant;
  useEffect(() => { refreshVoice(); }, [refreshVoice]);

  if (phase === 'choose') {
    return (
      <ChooseVoice options={options} optionsError={optionsError} ctx={ctx} refreshCtx={refreshCtx}
        onSaved={toCheck} next={next} language={language} />
    );
  }

  const finish = () => {
    assistant.cancelSpeech();
    if (result) onChecked(result);
    next();
  };

  return (
    <StepFrame
      icon={Volume2}
      title={t('firstRun.voice.checkTitle')}
      subtitle={t('firstRun.voice.checkSubtitle')}
      testId="first-run-voice-check-step"
      footer={(
        <>
          <PrimaryButton onClick={finish} testId="first-run-continue">{t('firstRun.continue')}</PrimaryButton>
          <LaterButton onClick={() => { assistant.cancelSpeech(); setPhase('choose'); }} testId="first-run-voice-change">
            {t('firstRun.voice.change')}
          </LaterButton>
        </>
      )}
    >
      <div className="space-y-4">
        {(downloading || setJob) && (
          <div className="rounded-2xl border border-gray-200 bg-white px-4 py-3 text-sm text-gray-600 flex items-start gap-2" data-testid="first-run-voice-downloading">
            <Loader2 className="w-4 h-4 mt-0.5 animate-spin text-indigo-500 shrink-0" />
            <span>{t('firstRun.voice.downloading')}{work?.jobs?.length ? ` ${work.jobs.filter((j) => j.status === 'done').length}/${work.jobs.length}` : ''}</span>
          </div>
        )}
        <LanguageVoices assistant={assistant} language={language} />
        <VoiceCheck assistant={assistant} onResult={setResult} downloading={downloading || Boolean(setJob)} />
      </div>
    </StepFrame>
  );
}
