/**
 * The Assistant: the whole service through one agent, by voice and by text
 * (docs/assistant.md). The thread is the person's own (routes/assistant.py);
 * every turn is an ordinary chat turn, so approvals, budgets and the run
 * ledger apply, and voice is a way in and out of it:
 *
 *  - hold the button (or Space) and speak: the recording is turned into text
 *    by the workspace's transcription model, or by the browser's own
 *    recognition when there is none;
 *  - the first paragraph of the answer is read aloud sentence by sentence
 *    while it streams, by the workspace's speech model or the browser's voice;
 *  - while a card waits, a short spoken yes or no answers it.
 *
 * The live mark in the middle shows what is going on: listening, thinking,
 * the step the turn is on, waiting for a card, speaking. Links in an answer
 * open the page in a panel beside the conversation ("show on screen"). On a
 * phone the page is the mark, the button and the last answer.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  AudioLines, Keyboard, Loader2, MessageSquarePlus, Mic, PanelRightClose, PanelRightOpen,
  Send, Settings2, StopCircle, Volume2, VolumeX,
} from 'lucide-react';
import { useI18n } from '../i18n';
import LiveMark from '../components/liveMark/LiveMark';
import ChatMarkdown from '../components/chat/ChatMarkdown';
import { FeedItem } from '../components/flow/ChatFeed';
import { ToolApprovalCard } from '../components/chat/ToolApprovalCard';
import { upsertApproval } from '../components/chat/toolApprovals';
import ScreenPanel from '../components/assistant/ScreenPanel';
import { useInlineChatOpen } from '../components/pageChat/pageChat';
import useRecorder from '../components/assistant/useRecorder';
import useSpeaker, { browserSpeechAvailable } from '../components/assistant/useSpeaker';
import useBrowserRecognition from '../components/assistant/useBrowserRecognition';
import { SentenceStream, firstParagraphSentences, isSpeakable } from '../components/assistant/sentences';
import {
  applyTurnEvent, delegateOf, feedFromThread, firstParagraph, isScreenLink, lastAnswer, markState,
  readPrefs, showModeSwitch, writePrefs,
} from '../components/assistant/assistantState';
import {
  AssistantError, clearAssistant, getAssistant, stopAssistant, streamAssistantTurn,
  transcribeRecording,
} from '../api/assistant';

//: Quiet for this long into a turn, the hub says which step it is on.
const ANNOUNCE_AFTER_MS = 7000;
//: Wide enough for the side column; below it a link leaves the page instead.
const WIDE_QUERY = '(min-width: 1024px)';

function isTyping(target) {
  const tag = target?.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target?.isContentEditable;
}

function useWide() {
  const [wide, setWide] = useState(() => (typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia(WIDE_QUERY).matches : true));
  useEffect(() => {
    if (!window.matchMedia) return undefined;
    const mq = window.matchMedia(WIDE_QUERY);
    const on = () => setWide(mq.matches);
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);
  return wide;
}

export default function Assistant() {
  const { t, language } = useI18n();
  const navigate = useNavigate();
  const wide = useWide();
  // The page is a conversation already: the page chat's button stands down.
  useInlineChatOpen(true);
  const [prefs, setPrefsState] = useState(readPrefs);
  const [mode, setMode] = useState('personal');
  const [meta, setMeta] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [feed, setFeed] = useState([]);
  const [cards, setCards] = useState([]);
  const [workspace, setWorkspace] = useState('');
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [talking, setTalking] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [notice, setNotice] = useState('');
  const [screen, setScreen] = useState(null);
  const [rightTab, setRightTab] = useState('transcript');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sttFallback, setSttFallback] = useState(false);
  const [turn, setTurn] = useState({ tool: '', thinking: false, text: false });

  const runIdRef = useRef(null);
  const abortRef = useRef(null);
  const sentencesRef = useRef(null);
  const speakTurnRef = useRef(false);
  const spokeSegmentRef = useRef(false);
  const lastSpokeRef = useRef(0);
  const talkRef = useRef(null);       // 'server' | 'browser' while the button is held
  const typedFromVoiceRef = useRef(false);
  const inputRef = useRef(null);
  const feedRef = useRef(null);

  const setPrefs = useCallback((patch) => {
    setPrefsState((prev) => {
      const next = { ...prev, ...patch };
      writePrefs(next);
      return next;
    });
  }, []);

  const voice = meta?.voice || {};
  const serverStt = Boolean(voice.transcription) && !sttFallback;
  const serverSpeech = Boolean(voice.speech);
  const speaker = useSpeaker({
    serverSpeech, language, voice: prefs.voice,
    onFallback: () => setNotice(t('assistant.notes.voiceFellBack')),
  });
  const browserRec = useBrowserRecognition();
  const endTalkRef = useRef(() => {});
  const recorder = useRecorder({
    maxSeconds: Math.min(60, Number(voice.max_seconds) || 60),
    onLimit: () => endTalkRef.current(),
  });
  const canListen = (serverStt && recorder.supported) || browserRec.available;
  const pending = cards.some((c) => c.status === 'pending');

  // ── the thread ─────────────────────────────────────────────────────────────
  const load = useCallback(async () => {
    try {
      const { data } = await getAssistant(mode);
      setMeta(data);
      setFeed(feedFromThread(data));
      setWorkspace((prev) => ((data.workspaces || []).includes(prev) ? prev : data.home));
      setLoadError('');
    } catch (e) {
      setLoadError(e?.response?.data?.detail || e.message || t('assistant.errors.load'));
    }
  }, [mode, t]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    if (feedRef.current) feedRef.current.scrollTop = feedRef.current.scrollHeight;
  }, [feed, cards]);

  // ── speech out ─────────────────────────────────────────────────────────────
  const sayLocal = useCallback((text) => {
    if (!prefs.muted && text) speaker.say({ local: true, fallback: text });
  }, [prefs.muted, speaker]);

  const saySentence = useCallback((sentence) => {
    if (!isSpeakable(sentence) || !runIdRef.current) return;
    speaker.say({ run_id: runIdRef.current, text: sentence });
    spokeSegmentRef.current = true;
    lastSpokeRef.current = Date.now();
  }, [speaker]);

  // ── a turn ─────────────────────────────────────────────────────────────────
  const runTurn = useCallback(async (message, { spoken = false } = {}) => {
    const text = String(message || '').trim();
    if (!text || busy) return;
    speaker.cancel();
    setBusy(true);
    setNotice('');
    setCards([]);
    setTurn({ tool: '', thinking: false, text: false });
    setFeed((f) => [...f, { k: 'user', text }]);
    runIdRef.current = null;
    speakTurnRef.current = !prefs.muted && (spoken || prefs.voiceOnly);
    sentencesRef.current = new SentenceStream();
    spokeSegmentRef.current = false;
    lastSpokeRef.current = Date.now();
    const ac = new AbortController();
    abortRef.current = ac;
    try {
      await streamAssistantTurn({
        body: { message: text, workspace: workspace || undefined, mode, voice: spoken },
        signal: ac.signal,
        onEvent: (ev) => {
          setFeed((f) => applyTurnEvent(f, ev));
          const speak = speakTurnRef.current;
          switch (ev.type) {
            case 'run':
              runIdRef.current = ev.run_id;
              break;
            case 'token':
              setTurn((s) => ({ ...s, text: true, thinking: false }));
              if (speak) sentencesRef.current.push(ev.token || '').forEach(saySentence);
              break;
            case 'tool_start': {
              setTurn((s) => ({ ...s, tool: ev.tool || '', thinking: false }));
              if (!speak) break;
              // What the model said before a tool is a segment of its own; the
              // answer after the tools starts a fresh first paragraph.
              sentencesRef.current.end().forEach(saySentence);
              sentencesRef.current = new SentenceStream();
              spokeSegmentRef.current = false;
              if (Date.now() - lastSpokeRef.current > ANNOUNCE_AFTER_MS && runIdRef.current) {
                speaker.say({
                  run_id: runIdRef.current, tool: ev.tool, agent: delegateOf(ev.input),
                  fallback: t('assistant.voice.working', { step: String(ev.tool || '').replace(/_tool$/, '').replace(/_/g, ' ') }),
                });
                lastSpokeRef.current = Date.now();
              }
              break;
            }
            case 'tool_end':
            case 'tool_error':
              setTurn((s) => ({ ...s, tool: '' }));
              break;
            case 'think_delta':
            case 'think':
              setTurn((s) => ({ ...s, thinking: true }));
              break;
            case 'tool_approval':
              setCards((c) => upsertApproval(c, ev));
              if (speak && runIdRef.current) {
                speaker.say({
                  run_id: runIdRef.current, approval_id: ev.approval_id,
                  fallback: ev.tool === 'propose_connection'
                    ? t('assistant.voice.connection') : t('assistant.voice.approval'),
                });
                lastSpokeRef.current = Date.now();
              }
              break;
            case 'tool_approval_resolved':
              setCards((c) => upsertApproval(c, ev));
              break;
            case 'message':
              if (speak) {
                const rest = sentencesRef.current.end();
                if (!spokeSegmentRef.current && !rest.length) {
                  firstParagraphSentences(ev.content).forEach(saySentence);
                } else {
                  rest.forEach(saySentence);
                }
              }
              break;
            default:
              break;
          }
        },
      });
    } catch (e) {
      if (e.name !== 'AbortError') {
        const msg = e instanceof AssistantError && e.code === 'budget'
          ? t('assistant.errors.budget')
          : e instanceof AssistantError && e.code === 'busy' ? t('assistant.errors.busy')
            : (e.message || t('assistant.errors.turn'));
        setFeed((f) => [...f, { k: 'error', text: msg }]);
        // Under the button as well: on a phone there is no transcript to read it in.
        setNotice(msg);
        if (speakTurnRef.current || spoken) sayLocal(msg);
      }
    } finally {
      setBusy(false);
      setStopping(false);
      setTurn({ tool: '', thinking: false, text: false });
      abortRef.current = null;
    }
  }, [busy, mode, prefs.muted, prefs.voiceOnly, saySentence, sayLocal, speaker, t, workspace]);

  /** A spoken yes or no while a card waits: answered by the hub, no turn. */
  const answerByVoice = useCallback(async (text) => {
    try {
      await streamAssistantTurn({
        body: { message: text, workspace: workspace || undefined, mode, voice: true },
        onEvent: (ev) => {
          if (ev.type !== 'voice_answer') return;
          const key = ev.status === 'on_screen' || ev.status === 'ambiguous' ? ev.status : ev.decision;
          setFeed((f) => [...f, { k: 'note', text: t(`assistant.voiceAnswer.${key}`) }]);
          if (ev.status === 'on_screen' || ev.status === 'ambiguous') sayLocal(t(`assistant.voiceAnswer.${key}`));
        },
      });
    } catch (e) {
      setNotice(e instanceof AssistantError && e.code === 'busy' ? t('assistant.errors.busyCard') : e.message);
    }
  }, [mode, sayLocal, t, workspace]);

  // ── speech in ──────────────────────────────────────────────────────────────
  const heard = useCallback((text) => {
    const said = String(text || '').trim();
    if (!said) { setNotice(t('assistant.notes.nothingHeard')); return; }
    if (busy) {
      if (pending) answerByVoice(said);
      else { setInput(said); setNotice(t('assistant.errors.busy')); }
      return;
    }
    if (prefs.voiceOnly) { runTurn(said, { spoken: true }); return; }
    // Shown first, so a misheard word is fixed before it becomes an instruction.
    setInput(said);
    typedFromVoiceRef.current = true;
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [answerByVoice, busy, pending, prefs.voiceOnly, runTurn, t]);

  const startTalk = useCallback(async () => {
    if (talkRef.current || transcribing) return;
    speaker.cancel();
    speaker.unlock();
    setNotice('');
    if (serverStt && recorder.supported) {
      talkRef.current = 'server';
      setTalking(true);
      try {
        await recorder.start();
      } catch {
        talkRef.current = null;
        setTalking(false);
        setNotice(t(recorder.error === 'denied' ? 'assistant.errors.micDenied' : 'assistant.errors.mic'));
      }
    } else if (browserRec.available) {
      talkRef.current = 'browser';
      setTalking(true);
      browserRec.start(language);
    } else {
      setNotice(t('assistant.notes.noListen'));
    }
  }, [browserRec, language, recorder, serverStt, speaker, t, transcribing]);

  const endTalk = useCallback(async () => {
    const how = talkRef.current;
    if (!how) return;
    talkRef.current = null;
    setTalking(false);
    if (how === 'browser') { heard(await browserRec.stop()); return; }
    const take = await recorder.stop();
    if (!take) return;
    setTranscribing(true);
    try {
      const res = await transcribeRecording(take.blob, { mode, language });
      heard(res.text);
    } catch (e) {
      if (e instanceof AssistantError && e.code === 'model_not_added') {
        setSttFallback(true);
        setNotice(t('assistant.notes.browserListen'));
      } else if (e instanceof AssistantError && e.code === 'budget') {
        setNotice(t('assistant.errors.budget'));
        sayLocal(t('assistant.errors.budget'));
      } else {
        setNotice(e.message || t('assistant.errors.transcribe'));
      }
    } finally {
      setTranscribing(false);
    }
  }, [browserRec, heard, language, mode, recorder, sayLocal, t]);
  useEffect(() => { endTalkRef.current = endTalk; }, [endTalk]);

  // Space is the talk button while nothing else wants the key.
  useEffect(() => {
    const down = (e) => {
      if (e.code !== 'Space' || e.repeat || isTyping(e.target) || !canListen) return;
      e.preventDefault();
      startTalk();
    };
    const up = (e) => {
      if (e.code !== 'Space' || !talkRef.current) return;
      e.preventDefault();
      endTalk();
    };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
    };
  }, [canListen, endTalk, startTalk]);

  // ── controls ───────────────────────────────────────────────────────────────
  const onSend = () => {
    const text = input.trim();
    if (!text) return;
    setInput('');
    const spoken = typedFromVoiceRef.current;
    typedFromVoiceRef.current = false;
    runTurn(text, { spoken });
  };

  const onStop = async () => {
    speaker.cancel();
    if (!busy) return;
    setStopping(true);
    try { await stopAssistant(mode); } catch (e) { setNotice(e.message); setStopping(false); }
  };

  const onNewThread = async () => {
    if (busy) return;
    speaker.cancel();
    try {
      await clearAssistant(mode);
      setFeed([]);
      setCards([]);
    } catch (e) {
      setNotice(e?.response?.data?.detail || e.message);
    }
  };

  const openScreen = useCallback((path) => {
    if (!wide) { navigate(path); return; }
    setScreen(path);
    setRightTab('screen');
    if (!prefs.transcriptOpen) setPrefs({ transcriptOpen: true });
  }, [navigate, prefs.transcriptOpen, setPrefs, wide]);

  const AnswerLink = useCallback(({ href, children }) => {
    if (isScreenLink(href)) {
      return (
        <button type="button" onClick={() => openScreen(href)}
          className="inline text-indigo-600 dark:text-indigo-300 underline hover:text-indigo-800 rounded-sm">
          {children}
        </button>
      );
    }
    if (/^https?:\/\//i.test(href || '')) return <a href={href} target="_blank" rel="noreferrer noopener">{children}</a>;
    return <>{children}</>;
  }, [openScreen]);
  const renderReply = useCallback(
    (text) => <div className="whitespace-normal"><ChatMarkdown content={text} linkComponent={AnswerLink} /></div>,
    [AnswerLink],
  );

  const state = markState({
    recording: talking, transcribing, speaking: speaker.speaking, busy, waiting: pending,
    tool: turn.tool, thinking: turn.thinking, text: turn.text,
  });
  const answer = firstParagraph(lastAnswer(feed));
  const voiceNames = voice.speech?.voices || [];
  const rightOpen = wide && prefs.transcriptOpen;
  const listenNote = !serverStt && browserRec.available ? t('assistant.notes.browserListen')
    : !canListen ? t('assistant.notes.noListen') : '';
  const speakNote = !serverSpeech ? (browserSpeechAvailable() ? t('assistant.notes.browserVoice') : t('assistant.notes.noVoice')) : '';

  return (
    <div className="h-full flex min-h-0" data-testid="assistant-page">
      <section className="flex-1 min-w-0 flex flex-col">
        <header className="flex flex-wrap items-center gap-2 px-4 sm:px-6 py-3 border-b border-gray-200 bg-white">
          <h1 className="text-lg font-semibold text-gray-900 flex items-center gap-2 mr-auto">
            <AudioLines className="w-5 h-5 text-indigo-600" /> {t('assistant.title')}
          </h1>
          {showModeSwitch(meta) && (
            <div className="inline-flex rounded-lg border border-gray-200 overflow-hidden text-xs" role="tablist"
              aria-label={t('assistant.mode.label')} data-testid="assistant-mode-switch">
              {['personal', 'service'].map((m, i) => (
                <button key={m} type="button" role="tab" aria-selected={mode === m} disabled={busy}
                  onClick={() => setMode(m)}
                  className={`px-3 py-1.5 ${i ? 'border-l border-gray-200' : ''} ${mode === m
                    ? 'bg-indigo-50 text-indigo-700' : 'text-gray-600 hover:bg-gray-50'}`}>
                  {t(`assistant.mode.${m}`)}
                </button>
              ))}
            </div>
          )}
          {(meta?.workspaces || []).length > 1 && (
            <label className="text-xs text-gray-500 flex items-center gap-1.5">
              <span className="hidden sm:inline">{t('assistant.workspace')}</span>
              <select value={workspace} onChange={(e) => setWorkspace(e.target.value)} disabled={busy}
                className="text-sm border border-gray-200 rounded-lg px-2 py-1 bg-white">
                {meta.workspaces.map((w) => (
                  <option key={w} value={w}>{w === meta.home && mode === 'personal' && meta.home !== 'default'
                    ? t('assistant.personalWorkspace') : w}</option>
                ))}
              </select>
            </label>
          )}
          <button type="button" onClick={() => setPrefs({ muted: !prefs.muted })}
            title={prefs.muted ? t('assistant.unmute') : t('assistant.mute')}
            aria-label={prefs.muted ? t('assistant.unmute') : t('assistant.mute')} aria-pressed={prefs.muted}
            className="p-2 rounded-lg text-gray-500 hover:bg-gray-100">
            {prefs.muted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
          </button>
          <div className="relative">
            <button type="button" onClick={() => setSettingsOpen((o) => !o)} aria-expanded={settingsOpen}
              title={t('assistant.settings.title')} aria-label={t('assistant.settings.title')}
              className="p-2 rounded-lg text-gray-500 hover:bg-gray-100">
              <Settings2 className="w-4 h-4" />
            </button>
            {settingsOpen && (
              <div className="absolute right-0 top-full mt-1 z-20 w-72 rounded-lg border border-gray-200 bg-white shadow-lg p-3 space-y-3 text-sm"
                data-testid="assistant-settings">
                <label className="flex items-start gap-2">
                  <input type="checkbox" checked={prefs.voiceOnly} onChange={(e) => setPrefs({ voiceOnly: e.target.checked })} className="mt-1" />
                  <span><span className="font-medium text-gray-800">{t('assistant.settings.voiceOnly')}</span>
                    <span className="block text-xs text-gray-500">{t('assistant.settings.voiceOnlyHint')}</span></span>
                </label>
                <label className="flex items-start gap-2">
                  <input type="checkbox" checked={prefs.muted} onChange={(e) => setPrefs({ muted: e.target.checked })} className="mt-1" />
                  <span><span className="font-medium text-gray-800">{t('assistant.settings.muted')}</span>
                    <span className="block text-xs text-gray-500">{t('assistant.settings.mutedHint')}</span></span>
                </label>
                {serverSpeech && (
                  <label className="block text-xs text-gray-600">
                    {t('assistant.settings.voice')}
                    <select value={prefs.voice} onChange={(e) => setPrefs({ voice: e.target.value })}
                      className="mt-1 w-full border border-gray-200 rounded px-2 py-1 bg-white text-sm">
                      <option value="">{t('assistant.settings.modelVoice', { voice: voice.speech?.voice || t('assistant.settings.providerDefault') })}</option>
                      {voiceNames.map((v) => <option key={v} value={v}>{v}</option>)}
                    </select>
                    <span className="block mt-1 text-gray-400">{t('assistant.settings.voiceHint')}</span>
                  </label>
                )}
              </div>
            )}
          </div>
          <button type="button" onClick={onNewThread} disabled={busy || !feed.length}
            title={t('assistant.newThread')} aria-label={t('assistant.newThread')}
            className="p-2 rounded-lg text-gray-500 hover:bg-gray-100 disabled:opacity-40">
            <MessageSquarePlus className="w-4 h-4" />
          </button>
          {wide && (
            <button type="button" onClick={() => setPrefs({ transcriptOpen: !prefs.transcriptOpen })}
              title={prefs.transcriptOpen ? t('assistant.hideTranscript') : t('assistant.showTranscript')}
              aria-label={prefs.transcriptOpen ? t('assistant.hideTranscript') : t('assistant.showTranscript')}
              className="p-2 rounded-lg text-gray-500 hover:bg-gray-100">
              {prefs.transcriptOpen ? <PanelRightClose className="w-4 h-4" /> : <PanelRightOpen className="w-4 h-4" />}
            </button>
          )}
        </header>

        <div className="flex-1 min-h-0 overflow-y-auto flex flex-col items-center justify-center gap-6 px-4 sm:px-8 py-8">
          {loadError && <div className="text-sm text-red-600">{loadError}</div>}
          <div className="flex flex-col items-center gap-2">
            <LiveMark state={state} size={wide ? 168 : 128} minHold={500} />
            <div className="text-sm text-gray-500 h-5" aria-live="polite" data-testid="assistant-state">
              {state === 'idle' ? '' : t(`liveMark.states.${state}`, { defaultValue: '' })}
            </div>
          </div>

          <div className="w-full max-w-2xl min-h-[3rem] text-center text-lg leading-relaxed text-gray-800"
            data-testid="assistant-answer">
            {answer ? renderReply(answer)
              : <p className="text-gray-400 text-base">{t(canListen ? 'assistant.emptyVoice' : 'assistant.emptyText')}</p>}
          </div>

          {cards.length > 0 && (
            <div className="w-full max-w-xl space-y-2">
              {cards.map((c) => <ToolApprovalCard key={c.approval_id} approval={c} live={busy} />)}
            </div>
          )}

          <div className="flex flex-col items-center gap-2">
            <div className="relative">
              {/* The level of the voice, around the button, while it is held. */}
              <span aria-hidden className="absolute inset-0 rounded-full bg-indigo-400/30 transition-transform duration-75"
                style={{ transform: `scale(${talking ? 1 + recorder.level * 0.6 : 1})` }} />
              <button
                type="button"
                data-tour="assistant-talk"
                disabled={!canListen || transcribing}
                onPointerDown={(e) => { e.preventDefault(); startTalk(); }}
                onPointerUp={endTalk}
                onPointerLeave={() => { if (talkRef.current) endTalk(); }}
                onContextMenu={(e) => e.preventDefault()}
                aria-label={t('assistant.talk')}
                aria-pressed={talking}
                className={`relative w-20 h-20 rounded-full flex items-center justify-center text-white shadow-lg select-none touch-none transition-colors ${
                  talking ? 'bg-red-500' : 'bg-indigo-600 hover:bg-indigo-700'} disabled:opacity-40`}
              >
                {transcribing ? <Loader2 className="w-8 h-8 animate-spin" /> : <Mic className="w-8 h-8" />}
              </button>
            </div>
            <div className="text-xs text-gray-500">
              {talking ? t('assistant.release') : transcribing ? t('assistant.transcribing') : t('assistant.hold')}
            </div>
            {(busy || speaker.speaking) && (
              <button type="button" onClick={onStop} disabled={stopping}
                className="inline-flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border border-gray-200 text-gray-600 hover:bg-gray-50">
                <StopCircle className="w-3.5 h-3.5" /> {stopping ? t('assistant.stopping') : t('assistant.stop')}
              </button>
            )}
          </div>

          {(notice || listenNote || speakNote) && (
            <div className="max-w-xl text-center space-y-1" data-testid="assistant-notes">
              {notice && <p className="text-xs text-amber-700">{notice}</p>}
              {!notice && listenNote && <p className="text-[11px] text-gray-400">{listenNote}</p>}
              {!notice && speakNote && !prefs.muted && <p className="text-[11px] text-gray-400">{speakNote}</p>}
            </div>
          )}
        </div>

        {!prefs.voiceOnly && (
          <div className="shrink-0 border-t border-gray-200 bg-white px-4 sm:px-6 py-3">
            <div className="max-w-3xl mx-auto flex items-end gap-2">
              <Keyboard className="w-4 h-4 text-gray-400 mb-3 hidden sm:block" />
              <textarea
                ref={inputRef}
                rows={1}
                value={input}
                onChange={(e) => { setInput(e.target.value); if (!e.target.value) typedFromVoiceRef.current = false; }}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend(); } }}
                placeholder={t('assistant.placeholder')}
                disabled={busy}
                className="flex-1 resize-none text-sm leading-5 border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-indigo-400 disabled:bg-gray-50"
              />
              <button type="button" onClick={onSend} disabled={busy || !input.trim()}
                title={t('assistant.send')} aria-label={t('assistant.send')}
                className="h-10 w-10 inline-flex items-center justify-center rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40">
                <Send className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </section>

      {rightOpen && (
        <aside className="w-[26rem] xl:w-[32rem] shrink-0 border-l border-gray-200 bg-white flex flex-col min-h-0"
          data-testid="assistant-side">
          <div className="flex items-center gap-1 px-3 h-11 border-b border-gray-200 shrink-0 text-xs" role="tablist">
            {['transcript', ...(screen ? ['screen'] : [])].map((tab) => (
              <button key={tab} type="button" role="tab" aria-selected={rightTab === tab}
                onClick={() => setRightTab(tab)}
                className={`px-2.5 py-1 rounded-md ${rightTab === tab ? 'bg-indigo-50 text-indigo-700' : 'text-gray-500 hover:bg-gray-50'}`}>
                {t(`assistant.tabs.${tab}`)}
              </button>
            ))}
          </div>
          {rightTab === 'screen' && screen ? (
            <ScreenPanel path={screen} onClose={() => { setScreen(null); setRightTab('transcript'); }} />
          ) : (
            <div ref={feedRef} className="flex-1 min-h-0 overflow-y-auto p-3 space-y-2" data-testid="assistant-transcript">
              {feed.length === 0 && <p className="text-xs text-gray-400">{t('assistant.transcriptEmpty')}</p>}
              {feed.map((e, i) => (e.k === 'note'
                ? <div key={i} className="text-[11px] text-center text-gray-500">{e.text}</div>
                : <FeedItem key={i} e={e} renderText={renderReply} />))}
              {busy && (
                <div className="flex items-center gap-1.5 text-xs text-violet-500">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" /> {t('assistant.working')}
                </div>
              )}
            </div>
          )}
        </aside>
      )}
    </div>
  );
}
