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
import { useLocation, useNavigate } from 'react-router-dom';
import {
  AudioLines, Eraser, Keyboard, Loader2, MessageSquarePlus, Mic, PanelRightClose, PanelRightOpen,
  Phone, PhoneOff, Play, Send, Settings2, Square, StopCircle, Volume2, VolumeX,
} from 'lucide-react';
import { useI18n } from '../i18n';
import LiveMark from '../components/liveMark/LiveMark';
import ChatMarkdown from '../components/chat/ChatMarkdown';
import { FeedItem } from '../components/flow/ChatFeed';
import { ToolApprovalCard } from '../components/chat/ToolApprovalCard';
import RefusalCard from '../components/chat/RefusalCard';
import { upsertApproval } from '../components/chat/toolApprovals';
import ScreenPanel from '../components/assistant/ScreenPanel';
import ChatSessionList from '../components/ChatSessionList';
import { useChatSessions } from '../components/chatSessions';
import { useInlineChatOpen } from '../components/pageChat/pageChat';
import { useWorkspace } from '../components/workspace';
import useVoiceSample from '../components/useVoiceSample';
import useRecorder from '../components/assistant/useRecorder';
import useSpeaker, { browserSpeechAvailable } from '../components/assistant/useSpeaker';
import useBrowserRecognition from '../components/assistant/useBrowserRecognition';
import useHandsFree, { micAllowed } from '../components/assistant/useHandsFree';
import { chime } from '../components/assistant/chime';
import {
  DEFAULT_WAKE, findWake, isEndCommand, isStopCommand, wakePhrases,
} from '../components/assistant/voiceCommands';
import { SentenceStream, firstParagraphSentences, isSpeakable } from '../components/assistant/sentences';
import {
  LISTEN_MODES, applyTurnEvent, delegateOf, earPhase, earTuning, feedFromThread, firstParagraph,
  isScreenLink, lastAnswer, markState, readPrefs, screenToolPath, showModeSwitch, writePrefs,
} from '../components/assistant/assistantState';
import {
  AssistantError, clearAssistant, forgetAssistantConversation, getAssistant, sampleAssistantVoice, stopAssistant,
  streamAssistantTurn, transcribeRecording,
} from '../api/assistant';
import SetupGuidePanel from '../components/setup/SetupGuidePanel';
import useSetupGuide, { dispatchSetupGuideRefresh, setupStepText } from '../components/setup/useSetupGuide';

//: Quiet for this long into a turn, the hub says which step it is on.
const ANNOUNCE_AFTER_MS = 7000;
//: Wide enough for the side column; below it a link leaves the page instead.
const WIDE_QUERY = '(min-width: 1024px)';
//: Called by name (or after an answer in wake mode), it listens this long
//: for something to be said before it waits for its name again.
const AWAKE_MS = 8000;

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
  const location = useLocation();
  const wide = useWide();
  // The page is a conversation already: the page chat's button stands down.
  useInlineChatOpen(true);
  // Turns run in the workspace picked in the header, and voice uses its
  // models (else the home's). One the assistant cannot reach (another
  // person's personal workspace) falls back to the thread's home.
  const { selectedWorkspace } = useWorkspace() || {};
  // The guided setup (docs/assistant.md "Guided setup"): the Setup tab beside
  // the transcript, and what the welcome window or the header's own pill
  // hands this page into (below, "the setup hand over").
  const { guide, act: guideAct } = useSetupGuide();
  const [prefs, setPrefsState] = useState(readPrefs);
  const [mode, setMode] = useState('personal');
  const [meta, setMeta] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [feed, setFeed] = useState([]);
  const [cards, setCards] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [talking, setTalking] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [notice, setNotice] = useState('');
  const [screen, setScreen] = useState(null);
  // A `show_on_screen` tool offered a page while there was no room to show it
  // (a phone): a link under the answer opens it, instead of leaving the
  // conversation mid-turn.
  const [screenOffer, setScreenOffer] = useState(null);
  const [rightTab, setRightTab] = useState('transcript');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sttFallback, setSttFallback] = useState(false);
  const [turn, setTurn] = useState({ tool: '', input: null, thinking: false, text: false });
  // Hands-free: a conversation that is on, the assistant called by name,
  // a stretch of speech being turned into text, the microphone already allowed.
  const [conversing, setConversing] = useState(false);
  const [awake, setAwake] = useState(false);
  const [earTranscribing, setEarTranscribing] = useState(false);
  const [micOk, setMicOk] = useState(false);
  const settingsRef = useRef(null);

  // The settings popover closes on a click outside it or on Escape.
  useEffect(() => {
    if (!settingsOpen) return undefined;
    const onDown = (e) => {
      if (settingsRef.current && !settingsRef.current.contains(e.target)) setSettingsOpen(false);
    };
    const onKey = (e) => { if (e.key === 'Escape') setSettingsOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [settingsOpen]);

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
  const busyRef = useRef(false);        // set at once, before the state catches up
  const awakeTimerRef = useRef(0);
  const earChainRef = useRef(Promise.resolve());
  const earTurnRef = useRef(false);     // the last turn came from the open microphone
  const earRef = useRef({ phase: 'off' }); // the hands-free handlers of the latest render

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
  const workspace = (meta?.workspaces || []).includes(selectedWorkspace) ? selectedWorkspace : (meta?.home || '');
  const pending = cards.some((c) => c.status === 'pending');

  // ── the thread ─────────────────────────────────────────────────────────────
  const [sessionsKey, setSessionsKey] = useState(0);
  const load = useCallback(async () => {
    try {
      // A header workspace this person is not a member of (a stale choice)
      // is refused outright: the thread still loads, with the home's voice.
      const { data } = await getAssistant(mode, selectedWorkspace).catch((e) => {
        if (selectedWorkspace && e?.response?.status === 403) return getAssistant(mode);
        throw e;
      });
      setMeta(data);
      setFeed(feedFromThread(data));
      setLoadError('');
      // A conversation stays in its workspace: loading the page in another
      // one may have filed the live conversation among the past ones.
      setSessionsKey((k) => k + 1);
    } catch (e) {
      setLoadError(e?.response?.data?.detail || e.message || t('assistant.errors.load'));
    }
  }, [mode, selectedWorkspace, t]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => () => abortRef.current?.abort(), []);

  // Past conversations (the History tab): the thread's archive, refreshed
  // when a turn lands or a conversation is started, cleared or switched to.
  const bumpSessions = useCallback(() => setSessionsKey((k) => k + 1), []);
  const pastChats = useChatSessions(meta?.chat_ref || null, sessionsKey);
  // A conversation stays in the workspace it started in (one from before
  // turns were stamped is the home's), so the page lists this workspace's.
  const pastSessions = pastChats.sessions.filter(
    (s) => s.active || (s.workspace || meta?.home) === workspace);
  // The assistant asked to go back to a past conversation: the server swaps
  // it in once the turn ends, and the page reloads it then.
  const switchedRef = useRef(false);
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

  // The assistant's "show on screen": a page opened beside the conversation
  // when there is room for it, or just navigated to on a phone. Declared
  // ahead of `runTurn`, which calls it for the `show_on_screen` tool.
  const openScreen = useCallback((path) => {
    if (!wide) { navigate(path); return; }
    setScreen(path);
    setRightTab('screen');
    if (!prefs.transcriptOpen) setPrefs({ transcriptOpen: true });
  }, [navigate, prefs.transcriptOpen, setPrefs, wide]);

  // ── a turn ─────────────────────────────────────────────────────────────────
  const runTurn = useCallback(async (message, { spoken = false, ear = false, speakReply = false } = {}) => {
    const text = String(message || '').trim();
    if (!text || busy || busyRef.current) return;
    busyRef.current = true;
    earTurnRef.current = ear;
    speaker.cancel();
    setBusy(true);
    setNotice('');
    setCards([]);
    setScreenOffer(null);
    setTurn({ tool: '', input: null, thinking: false, text: false });
    setFeed((f) => [...f, { k: 'user', text }]);
    runIdRef.current = null;
    speakTurnRef.current = !prefs.muted && (spoken || prefs.voiceOnly || speakReply);
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
              setTurn((s) => ({ ...s, tool: ev.tool || '', input: ev.input ?? null, thinking: false }));
              // A page opened for the person: beside the conversation when
              // there is room, offered under the answer otherwise, never a
              // mid-turn navigation away from the assistant.
              if (ev.tool === 'show_on_screen') {
                const path = screenToolPath(ev.input);
                if (path) { if (wide) openScreen(path); else setScreenOffer(path); }
              }
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
              setTurn((s) => ({ ...s, tool: '', input: null }));
              // A setup tool just changed something the guide reports on.
              if (ev.tool === 'setup_guide' || ev.tool === 'setup_step' || ev.tool === 'propose_connection') {
                dispatchSetupGuideRefresh();
              }
              break;
            case 'tool_error':
              setTurn((s) => ({ ...s, tool: '', input: null }));
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
            case 'conversation':
              switchedRef.current = true;
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
        setFeed((f) => [...f, { k: 'error', text: msg, refusal: e instanceof AssistantError ? e.refusal : null }]);
        // Under the button as well: on a phone there is no transcript to read it in.
        setNotice(msg);
        if (speakTurnRef.current || spoken) sayLocal(msg);
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
      setStopping(false);
      setTurn({ tool: '', input: null, thinking: false, text: false });
      abortRef.current = null;
      if (switchedRef.current) {
        switchedRef.current = false;
        setCards([]);
        load();
      }
      bumpSessions();
    }
  }, [busy, bumpSessions, load, mode, openScreen, prefs.muted, prefs.voiceOnly, saySentence, sayLocal, speaker, t, wide, workspace]);

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

  /** The Setup tab's "Do it with the assistant": a turn naming the step. */
  const askStep = useCallback((step) => {
    runTurn(t('assistant.setup.ask', { title: setupStepText(t, step, 'title') }), { speakReply: guide?.mode === 'voice' });
  }, [guide?.mode, runTurn, t]);

  // ── speech in ──────────────────────────────────────────────────────────────
  const heard = useCallback((text) => {
    const said = String(text || '').trim();
    if (!said) { setNotice(t('assistant.notes.nothingHeard')); return; }
    // "Stop" said with the button held stops the turn, even with a card waiting.
    if ((busy || speaker.speaking) && isStopCommand(said, wakePhrases(prefs.wakePhrase))) {
      earRef.current.stop?.();
      return;
    }
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
  }, [answerByVoice, busy, pending, prefs.voiceOnly, prefs.wakePhrase, runTurn, speaker.speaking, t]);

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
        setMicOk(true);
      } catch {
        talkRef.current = null;
        setTalking(false);
        setNotice(t(recorder.error === 'denied' ? 'assistant.errors.micDenied' : 'assistant.errors.mic'));
      }
    } else if (browserRec.available) {
      talkRef.current = 'browser';
      setTalking(true);
      browserRec.start(language);
      setMicOk(true);
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
      const res = await transcribeRecording(take.blob, { mode, workspace, language });
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
  }, [browserRec, heard, language, mode, recorder, sayLocal, t, workspace]);
  useEffect(() => { endTalkRef.current = endTalk; }, [endTalk]);

  // Space is the talk button while nothing else wants the key; hands-free
  // it is a tap on the button (start or end the conversation, call it now).
  // Outside text fields the page owns the key outright: no scroll (also while
  // the page is still loading or the key is held), and the menu link that
  // kept focus after navigation is let go instead of lighting up or firing.
  const tapRef = useRef(null);
  useEffect(() => {
    const down = (e) => {
      if (e.code !== 'Space' || isTyping(e.target)) return;
      e.preventDefault();
      const focused = document.activeElement;
      if (focused && focused !== document.body) focused.blur?.();
      if (e.repeat || !canListen) return;
      if (tapRef.current) { tapRef.current(); return; }
      startTalk();
    };
    const up = (e) => {
      if (e.code !== 'Space' || (isTyping(e.target) && !talkRef.current)) return;
      e.preventDefault();
      if (talkRef.current) endTalk();
    };
    window.addEventListener('keydown', down, true);
    window.addEventListener('keyup', up, true);
    return () => {
      window.removeEventListener('keydown', down, true);
      window.removeEventListener('keyup', up, true);
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

  // ── hands-free ─────────────────────────────────────────────────────────────
  const listenMode = canListen ? prefs.listen : 'hold';
  const handsFree = listenMode !== 'hold';
  const wake = wakePhrases(prefs.wakePhrase);
  const wakeName = prefs.wakePhrase.trim() || DEFAULT_WAKE[language] || DEFAULT_WAKE.en;
  const phase = earPhase({ busy, speaking: speaker.speaking, conversing, listen: listenMode, awake });
  const monitoring = phase === 'monitor';
  const earEngine = serverStt && recorder.supported ? 'server' : 'browser';
  // In hold mode a spoken stop needs the microphone open while the assistant
  // works: only once it is allowed, so a typed question never brings up the
  // browser's prompt.
  const wantEar = Boolean(meta) && canListen && !talking && !transcribing && (
    conversing || listenMode === 'wake' || (prefs.voiceStop && (busy || speaker.speaking) && micOk));
  const voiceStopOn = wantEar && (handsFree || prefs.voiceStop);

  const cancelSpeech = speaker.cancel;
  const sleep = useCallback(() => {
    clearTimeout(awakeTimerRef.current);
    setAwake(false);
  }, []);
  const wakeUp = useCallback(({ sound = true } = {}) => {
    clearTimeout(awakeTimerRef.current);
    if (sound) {
      cancelSpeech();
      chime();
    }
    setAwake(true);
    awakeTimerRef.current = setTimeout(() => setAwake(false), AWAKE_MS);
  }, [cancelSpeech]);
  useEffect(() => () => clearTimeout(awakeTimerRef.current), []);

  const endHandsFree = useCallback((byVoice = false) => {
    // In wake mode a goodbye only stops listening for a follow-up: it still
    // waits for its name, so there is nothing to announce.
    if (byVoice && conversing) setNotice(t('assistant.notes.conversationEnded'));
    setConversing(false);
    sleep();
  }, [conversing, sleep, t]);

  // What the open microphone heard, handled by the render it arrives in.
  const lastStopRef = useRef(0);
  useEffect(() => {
    const stopByVoice = () => {
      // The same "stop" comes in as interim words and then as the settled
      // utterance: acted on once.
      if (Date.now() - lastStopRef.current < 2000) return;
      lastStopRef.current = Date.now();
      if (busyRef.current) {
        onStop();
        setNotice(t('assistant.notes.stoppedByVoice'));
      } else if (speaker.speaking) {
        speaker.cancel();
      } else if (awake) {
        sleep();
      }
    };
    const heardByEar = (text, at) => {
      const said = String(text || '').trim();
      if (!said || at === 'off') return;
      if (isStopCommand(said, wake)) { stopByVoice(); return; }
      if (at === 'monitor') {
        // Hands-free, a short yes or no answers a waiting card (the hub
        // decides which it is); anything else waits for the turn to end.
        if (handsFree && pending && said.split(/\s+/).length <= 4) answerByVoice(said);
        return;
      }
      const hit = findWake(said, wake);
      if (at === 'wake') {
        if (!hit) return;
        if (!hit.rest) { wakeUp(); return; }
        if (isEndCommand(hit.rest)) return;
      } else if (isEndCommand(said, wake)) {
        endHandsFree(true);
        return;
      }
      const command = hit ? hit.rest : said;
      if (!command) { wakeUp({ sound: false }); return; }
      if (busyRef.current) { setNotice(t('assistant.errors.busy')); return; }
      sleep();
      runTurn(command, { spoken: true, ear: true });
    };
    const failed = (e) => {
      if (e instanceof AssistantError && e.code === 'model_not_added') {
        // The ear starts over with the browser's recognition.
        setSttFallback(true);
        setNotice(t('assistant.notes.browserListen'));
      } else if (e instanceof AssistantError && e.code === 'budget') {
        setNotice(t('assistant.errors.budget'));
        sayLocal(t('assistant.errors.budget'));
        endHandsFree();
        if (listenMode === 'wake') setPrefs({ listen: 'hold' });
      } else {
        setNotice(e?.message || t('assistant.errors.transcribe'));
      }
    };
    earRef.current = {
      phase, awake, wake, mode, workspace, language, heard: heardByEar, failed, stop: stopByVoice, wakeUp,
    };
  });

  const onEarSegment = useCallback(({ blob }) => {
    const at = earRef.current.phase;
    if (at === 'off') return;
    const { mode: m, workspace: w, language: lang } = earRef.current;
    if (at === 'command') setEarTranscribing(true);
    // One at a time, in the order they were said.
    earChainRef.current = earChainRef.current.then(async () => {
      try {
        const res = await transcribeRecording(blob, { mode: m, workspace: w, language: lang, purpose: at });
        earRef.current.heard(res.text, at);
      } catch (e) {
        earRef.current.failed(e);
      } finally {
        if (at === 'command') setEarTranscribing(false);
      }
    });
  }, []);
  const onEarUtterance = useCallback((text) => earRef.current.heard(text, earRef.current.phase), []);
  // The browser's recognition gives words as they come: a stop is acted on
  // before the sentence settles, and the name is answered with the chime.
  const onEarInterim = useCallback((text) => {
    const cur = earRef.current;
    if (cur.phase === 'monitor' && isStopCommand(text, cur.wake)) cur.stop();
    else if (cur.phase === 'wake' && !cur.awake && findWake(text, cur.wake)) cur.wakeUp();
  }, []);
  const onEarError = useCallback((code) => {
    setNotice(t(code === 'denied' ? 'assistant.errors.micDenied' : 'assistant.errors.mic'));
    setConversing(false);
  }, [t]);
  const ear = useHandsFree({
    engine: earEngine, language, onSegment: onEarSegment, onUtterance: onEarUtterance,
    onInterim: onEarInterim, onError: onEarError,
  });
  const { start: earStart, stop: earStop, tune: earTune, reset: earReset } = ear;

  useEffect(() => { micAllowed().then((ok) => { if (ok) setMicOk(true); }); }, []);
  useEffect(() => {
    if (!wantEar) { earStop(); return; }
    earStart().then((ok) => { if (ok) setMicOk(true); });
  }, [wantEar, earStart, earStop]);
  // A conversation starts when sound comes in, not on the press: the chime
  // says when to speak, since opening the microphone can take a second.
  const earReady = ear.ready;
  const chimedRef = useRef(false);
  useEffect(() => {
    if (!conversing || !earReady) { chimedRef.current = false; return; }
    if (chimedRef.current) return;
    chimedRef.current = true;
    chime();
  }, [conversing, earReady]);
  useEffect(() => {
    earTune(earTuning(phase, { speaking: speaker.speaking, pending, maxSeconds: Number(voice.max_seconds) || 60 }));
  }, [earTune, pending, phase, speaker.speaking, voice.max_seconds]);

  // Into or out of a turn, what was being heard is dropped: it belonged to
  // the other side. In wake mode an answer is followed by a moment of
  // listening without the name, for a follow-up question.
  const wasMonitoring = useRef(false);
  useEffect(() => {
    earReset();
    if (wasMonitoring.current && !monitoring && listenMode === 'wake' && earTurnRef.current) {
      wakeUp({ sound: false });
    }
    wasMonitoring.current = monitoring;
  }, [earReset, listenMode, monitoring, wakeUp]);

  // Called, it waits while something is being said or turned into text.
  useEffect(() => {
    if (!awake) return undefined;
    clearTimeout(awakeTimerRef.current);
    if (ear.speech || earTranscribing) return undefined;
    awakeTimerRef.current = setTimeout(() => setAwake(false), AWAKE_MS);
    return () => clearTimeout(awakeTimerRef.current);
  }, [awake, ear.speech, earTranscribing]);

  // Called by name on another page (WakeListener): what followed the name is
  // this page's first turn, or it listens for it.
  const wakeHandoff = useRef(location.state?.wake || null);
  // The guided setup's own hand over: the welcome window's buttons
  // (`state.setup`), or the header's pill (`?setup=1`, which names no mode
  // of its own, since it only shows once the guide already picked one).
  const setupHandoff = useRef(location.state?.setup
    || (new URLSearchParams(location.search).get('setup') === '1' ? { fromQuery: true } : null));
  useEffect(() => {
    if (location.state?.wake || location.state?.setup || new URLSearchParams(location.search).get('setup') === '1') {
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location.pathname, location.search, location.state, navigate]);
  useEffect(() => {
    const handoff = wakeHandoff.current;
    if (!handoff || !meta) return;
    wakeHandoff.current = null;
    if (handoff.command) runTurn(handoff.command, { spoken: true, ear: true });
    else wakeUp({ sound: false });
  }, [meta, runTurn, wakeUp]);
  useEffect(() => {
    const handoff = setupHandoff.current;
    if (!handoff || !meta || busyRef.current) return;
    if (handoff.fromQuery && !guide) return; // its mode is the guide's own
    setupHandoff.current = null;
    const voiceMode = (handoff.fromQuery ? guide?.mode : handoff.mode) === 'voice';
    if (voiceMode) {
      speaker.unlock();
      setPrefs({ listen: 'conversation' });
      setConversing(true);
    }
    runTurn(t(handoff.fromQuery ? 'assistant.setup.resume' : 'assistant.setup.kickoff'), { speakReply: voiceMode });
    if (wide) { setPrefs({ transcriptOpen: true }); setRightTab('setup'); }
  }, [guide, meta, runTurn, setPrefs, speaker, t, wide]);

  const chooseListen = (next) => {
    speaker.unlock();
    setPrefs({ listen: next });
    setConversing(false);
    sleep();
    setNotice('');
  };
  const tapHandsFree = () => {
    speaker.unlock();
    setNotice('');
    if (listenMode === 'conversation') {
      if (conversing) endHandsFree();
      else setConversing(true);
    } else if (awake) {
      sleep();
    } else {
      wakeUp();
    }
  };
  useEffect(() => { tapRef.current = handsFree ? tapHandsFree : null; });

  const onNewThread = async () => {
    if (busy) return;
    speaker.cancel();
    try {
      await clearAssistant(mode);
      setFeed([]);
      setCards([]);
      bumpSessions();
    } catch (e) {
      setNotice(e?.response?.data?.detail || e.message);
    }
  };

  // Clear the transcript: unlike a new conversation, this one is not kept
  // among the past ones (its turns stay in Messages).
  const onClearTranscript = async () => {
    if (busy || !feed.length) return;
    if (!window.confirm(t('assistant.clearConfirm'))) return;
    speaker.cancel();
    try {
      await forgetAssistantConversation(mode);
      setFeed([]);
      setCards([]);
      bumpSessions();
    } catch (e) {
      setNotice(e?.response?.data?.detail?.message || e?.response?.data?.detail || e.message);
    }
  };

  // A past conversation picked in the History tab becomes the live one: the
  // next message continues it.
  const onPickConversation = async (session) => {
    if (session.active) { setRightTab('transcript'); return; }
    if (busy) return;
    speaker.cancel();
    const data = await pastChats.activate(session.id);
    if (!data) return;
    setFeed(feedFromThread(data));
    setCards([]);
    setRightTab('transcript');
  };

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

  const earListening = ear.listening && ear.ready && phase === 'command' && !earTranscribing;
  // Held, but the microphone is still opening: what is said now is not heard.
  const micOpening = talking && talkRef.current === 'server' && !recorder.recording;
  const state = markState({
    recording: (talking && !micOpening) || earListening, transcribing: transcribing || earTranscribing,
    speaking: speaker.speaking, busy, waiting: pending,
    tool: turn.tool, input: turn.input, thinking: turn.thinking, text: turn.text,
  });
  // The talk button turns into stop while the assistant works or speaks, so nothing below it moves.
  // A waiting card keeps the microphone: a spoken yes or no answers it.
  const stopInPlace = !talking && !transcribing && (busy ? !pending : speaker.speaking);
  // The ring around the button: the voice while it is held or heard hands-free
  // (the browser's recognition gives no level, only that someone speaks).
  const ring = talking ? recorder.level
    : earListening ? (ear.engine === 'server' ? ear.level : (ear.speech ? 0.5 : 0.1)) : 0;
  const empty = !canListen ? 'assistant.emptyText'
    : listenMode === 'conversation' ? 'assistant.emptyConversation'
      : listenMode === 'wake' ? 'assistant.emptyWake' : 'assistant.emptyVoice';
  const answer = firstParagraph(lastAnswer(feed));
  const voiceNames = voice.speech?.voices || [];
  const voiceLanguages = voice.speech?.languages || {};
  // A line read with the picked voice, in its own language or the page's.
  const sample = useVoiceSample(`${workspace}|${mode}|${voice.speech?.provider}|${voice.speech?.model}|${prefs.voice}`);
  const playSample = () => sample.toggle(
    (signal) => sampleAssistantVoice({ voice: prefs.voice, language, workspace, mode }, { signal }),
    t('assistant.settings.sampleFailed'),
  );
  const rightOpen = wide && prefs.transcriptOpen;
  // The Setup tab: while the guide runs, and for a day after it finishes so
  // the person can still see what it did.
  const guideFinishedRecently = Boolean(guide?.finished_at)
    && Date.now() - new Date(guide.finished_at).getTime() < 24 * 60 * 60 * 1000;
  const showSetupTab = Boolean(guide?.active) || guideFinishedRecently;
  const listenNote = !serverStt && browserRec.available ? t('assistant.notes.browserListen')
    : !canListen ? t('assistant.notes.noListen')
      // Waiting for its name, the workspace's model hears every phrase said near the microphone.
      : listenMode === 'wake' && earEngine === 'server' ? t('assistant.notes.wakeCost') : '';
  const speakNote = !serverSpeech ? (browserSpeechAvailable() ? t('assistant.notes.browserVoice') : t('assistant.notes.noVoice')) : '';

  return (
    <div className="h-full flex min-h-0" data-testid="assistant-page">
      <section className="flex-1 min-w-0 flex flex-col">
        <header className="flex flex-wrap items-center gap-2 px-4 sm:px-6 py-2 min-h-14 border-b border-gray-200 bg-white">
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
          <button type="button" onClick={() => setPrefs({ muted: !prefs.muted })}
            title={prefs.muted ? t('assistant.unmute') : t('assistant.mute')}
            aria-label={prefs.muted ? t('assistant.unmute') : t('assistant.mute')} aria-pressed={prefs.muted}
            className="p-2 rounded-lg text-gray-500 hover:bg-gray-100">
            {prefs.muted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
          </button>
          <div className="relative" ref={settingsRef}>
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
                <label className="flex items-start gap-2">
                  <input type="checkbox" checked={prefs.voiceStop} onChange={(e) => setPrefs({ voiceStop: e.target.checked })} className="mt-1" />
                  <span><span className="font-medium text-gray-800">{t('assistant.settings.voiceStop')}</span>
                    <span className="block text-xs text-gray-500">{t('assistant.settings.voiceStopHint')}</span></span>
                </label>
                <label className="block text-xs text-gray-600">
                  {t('assistant.settings.wakePhrase')}
                  <input type="text" value={prefs.wakePhrase} maxLength={40}
                    onChange={(e) => setPrefs({ wakePhrase: e.target.value })}
                    placeholder={DEFAULT_WAKE[language] || DEFAULT_WAKE.en}
                    className="mt-1 w-full border border-gray-200 rounded px-2 py-1 bg-white text-sm" />
                  <span className="block mt-1 text-gray-400">{t('assistant.settings.wakePhraseHint')}</span>
                </label>
                {serverSpeech && (
                  <div className="text-xs text-gray-600">
                    <label htmlFor="assistant-voice">{t('assistant.settings.voice')}</label>
                    <div className="mt-1 flex items-stretch gap-1.5">
                      <select id="assistant-voice" value={prefs.voice} onChange={(e) => setPrefs({ voice: e.target.value })}
                        className="min-w-0 flex-1 border border-gray-200 rounded px-2 py-1 bg-white text-sm">
                        <option value="">{t('assistant.settings.modelVoice', { voice: voice.speech?.voice || t('assistant.settings.providerDefault') })}</option>
                        {voiceNames.map((v) => (
                          <option key={v} value={v}>{voiceLanguages[v] ? `${v} (${voiceLanguages[v]})` : v}</option>
                        ))}
                      </select>
                      <button type="button" onClick={playSample} data-testid="assistant-voice-sample"
                        title={t('assistant.settings.sampleHint')}
                        aria-label={sample.busy ? t('assistant.settings.sampleStop') : t('assistant.settings.sample')}
                        className="inline-flex shrink-0 items-center gap-1 px-2 rounded border border-gray-200 text-gray-700 hover:border-indigo-300 hover:text-indigo-700">
                        {sample.state.loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          : sample.state.playing ? <Square className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                        {sample.busy ? t('assistant.settings.sampleStop') : t('assistant.settings.sample')}
                      </button>
                    </div>
                    {sample.state.error && <span className="block mt-1 text-red-600">{sample.state.error}</span>}
                    {!sample.state.error && sample.state.text && (
                      <span className="block mt-1 italic text-gray-400">
                        {sample.state.language ? `${sample.state.language}: ` : ''}«{sample.state.text}»
                      </span>
                    )}
                    <span className="block mt-1 text-gray-400">{t('assistant.settings.voiceHint')}</span>
                  </div>
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
              : <p className="text-gray-400 text-base">{t(empty, { phrase: wakeName })}</p>}
          </div>

          {screenOffer && (
            <button type="button" onClick={() => { const path = screenOffer; setScreenOffer(null); openScreen(path); }}
              data-testid="assistant-screen-offer"
              className="text-sm font-medium text-indigo-600 underline hover:text-indigo-800">
              {t('assistant.screen.show')}
            </button>
          )}

          {cards.length > 0 && (
            <div className="w-full max-w-xl space-y-2">
              {cards.map((c) => <ToolApprovalCard key={c.approval_id} approval={c} live={busy} />)}
            </div>
          )}

          <div className="relative flex flex-col items-center gap-2">
            <div className="relative">
              {/* The level of the voice, around the button, while it is held or heard. */}
              <span aria-hidden className="absolute inset-0 rounded-full bg-indigo-400/30 transition-transform duration-75"
                style={{ transform: `scale(${1 + ring * 0.6})` }} />
              {stopInPlace ? (
                <button
                  type="button"
                  data-tour="assistant-talk"
                  onClick={onStop}
                  disabled={stopping}
                  aria-label={t('assistant.stop')}
                  className="relative w-20 h-20 rounded-full flex items-center justify-center text-white shadow-lg select-none bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40"
                >
                  {stopping ? <Loader2 className="w-8 h-8 animate-spin" /> : <Square className="w-7 h-7 fill-current" />}
                </button>
              ) : handsFree ? (
                <button
                  type="button"
                  data-tour="assistant-talk"
                  data-testid="assistant-handsfree"
                  disabled={!canListen}
                  onClick={tapHandsFree}
                  aria-label={listenMode === 'conversation'
                    ? t(conversing ? 'assistant.listen.end' : 'assistant.listen.start')
                    : t(awake ? 'assistant.listen.sleep' : 'assistant.listen.callNow')}
                  aria-pressed={listenMode === 'conversation' ? conversing : awake}
                  className={`relative w-20 h-20 rounded-full flex items-center justify-center text-white shadow-lg select-none transition-colors ${
                    (listenMode === 'conversation' ? conversing : awake) ? 'bg-red-500 hover:bg-red-600' : 'bg-indigo-600 hover:bg-indigo-700'} disabled:opacity-40`}
                >
                  {earTranscribing ? <Loader2 className="w-8 h-8 animate-spin" />
                    : listenMode === 'conversation' ? (conversing ? <PhoneOff className="w-8 h-8" /> : <Phone className="w-8 h-8" />)
                      : <Mic className="w-8 h-8" />}
                </button>
              ) : (
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
                  className={`relative w-20 h-20 rounded-full flex items-center justify-center text-white shadow-lg select-none touch-none [-webkit-touch-callout:none] transition-colors ${
                    talking ? 'bg-red-500' : 'bg-indigo-600 hover:bg-indigo-700'} disabled:opacity-40`}
                >
                  {transcribing ? <Loader2 className="w-8 h-8 animate-spin" /> : <Mic className="w-8 h-8" />}
                </button>
              )}
              {/* While a card waits the button stays a microphone for a spoken yes, so stop sits beside it. */}
              {busy && pending && !stopInPlace && (
                <button type="button" onClick={onStop} disabled={stopping}
                  title={t('assistant.stop')} aria-label={t('assistant.stop')}
                  className="absolute left-full top-1/2 -translate-y-1/2 ml-3 w-9 h-9 rounded-full flex items-center justify-center border border-gray-200 bg-white text-gray-600 hover:bg-gray-50 disabled:opacity-40">
                  {stopping ? <Loader2 className="w-4 h-4 animate-spin" /> : <StopCircle className="w-4 h-4" />}
                </button>
              )}
              {/* A conversation goes on while it answers: ending it sits beside Stop. */}
              {conversing && stopInPlace && (
                <button type="button" onClick={() => endHandsFree()}
                  title={t('assistant.listen.end')} aria-label={t('assistant.listen.end')}
                  className="absolute left-full top-1/2 -translate-y-1/2 ml-3 w-9 h-9 rounded-full flex items-center justify-center border border-gray-200 bg-white text-gray-600 hover:bg-gray-50">
                  <PhoneOff className="w-4 h-4" />
                </button>
              )}
            </div>
            <div className="text-xs text-gray-500 text-center" data-testid="assistant-hint">
              {stopInPlace ? t(stopping ? 'assistant.stopping' : voiceStopOn ? 'assistant.tapOrSayStop' : 'assistant.tapToStop')
                : micOpening || (conversing && !ear.ready) ? t('assistant.micOpening')
                  : talking ? t('assistant.release') : transcribing || earTranscribing ? t('assistant.transcribing')
                  : listenMode === 'conversation' ? t(conversing ? 'assistant.listen.conversing' : 'assistant.listen.tapToStart')
                    : listenMode === 'wake' ? (awake ? t('assistant.listen.awake') : t('assistant.listen.sayName', { phrase: wakeName }))
                      : t('assistant.hold')}
            </div>
            {canListen && (
              <div className="inline-flex rounded-full border border-gray-200 overflow-hidden text-[11px] mt-1" role="radiogroup"
                aria-label={t('assistant.listen.label')} data-testid="assistant-listen-mode">
                {LISTEN_MODES.map((m, i) => (
                  <button key={m} type="button" role="radio" aria-checked={listenMode === m}
                    onClick={() => chooseListen(m)}
                    title={t(`assistant.listen.modes.${m}Hint`, { phrase: wakeName })}
                    className={`px-2.5 py-1 ${i ? 'border-l border-gray-200' : ''} ${listenMode === m
                      ? 'bg-indigo-50 text-indigo-700' : 'text-gray-500 hover:bg-gray-50'}`}>
                    {t(`assistant.listen.modes.${m}`)}
                  </button>
                ))}
              </div>
            )}
            {/* Notes hang below the controls rather than in the column, so one
                appearing (a conversation ended, a voice fell back) does not
                push the centred mark and button up. */}
            {(notice || listenNote || speakNote) && (
              <div className="absolute top-full left-1/2 -translate-x-1/2 mt-3 w-[min(36rem,calc(100vw-2rem))] text-center space-y-1"
                data-testid="assistant-notes">
                {notice && <p className="text-xs text-amber-700">{notice}</p>}
                {!notice && listenNote && <p className="text-[11px] text-gray-400">{listenNote}</p>}
                {!notice && speakNote && !prefs.muted && <p className="text-[11px] text-gray-400">{speakNote}</p>}
              </div>
            )}
          </div>

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
                className="flex-1 resize-none text-sm leading-5 border border-gray-300 rounded-lg px-3 py-2 focus:outline-none disabled:bg-gray-50"
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
          {/* As tall as the page's header beside it (min-h-14 there). */}
          <div className="flex items-center gap-1 px-3 h-14 border-b border-gray-200 shrink-0 text-xs" role="tablist"
            data-testid="assistant-side-tabs">
            {['transcript', 'history', ...(screen ? ['screen'] : []), ...(showSetupTab ? ['setup'] : [])].map((tab) => (
              <button key={tab} type="button" role="tab" aria-selected={rightTab === tab}
                onClick={() => { if (tab === 'history') pastChats.reload(); setRightTab(tab); }}
                className={`px-2.5 py-1 rounded-md ${rightTab === tab ? 'bg-indigo-50 text-indigo-700' : 'text-gray-500 hover:bg-gray-50'}`}>
                {t(`assistant.tabs.${tab}`)}
              </button>
            ))}
            {rightTab === 'transcript' && (
              <button type="button" onClick={onClearTranscript} disabled={busy || !feed.length}
                title={t('assistant.clearTranscript')} aria-label={t('assistant.clearTranscript')}
                className="ml-auto p-2 rounded-lg text-gray-500 hover:bg-gray-100 disabled:opacity-40">
                <Eraser className="w-4 h-4" />
              </button>
            )}
          </div>
          {rightTab === 'screen' && screen ? (
            <ScreenPanel path={screen} onClose={() => { setScreen(null); setRightTab('transcript'); }} />
          ) : rightTab === 'setup' ? (
            <SetupGuidePanel guide={guide} act={guideAct} onAsk={askStep} />
          ) : rightTab === 'history' ? (
            <div className="flex-1 min-h-0 overflow-y-auto p-3" data-testid="assistant-history">
              {pastSessions.length === 0 ? (
                <p className="text-xs text-gray-400">{t('assistant.historyEmpty')}</p>
              ) : (
                <ChatSessionList
                  sessions={pastSessions}
                  working={pastChats.working}
                  error={busy ? t('assistant.historyBusy') : pastChats.error}
                  onPick={onPickConversation}
                  onDelete={(s) => pastChats.remove(s.id)}
                  onClose={() => setRightTab('transcript')}
                />
              )}
            </div>
          ) : (
            <div ref={feedRef} className="flex-1 min-h-0 overflow-y-auto p-3 space-y-2" data-testid="assistant-transcript">
              {feed.length === 0 && <p className="text-xs text-gray-400">{t('assistant.transcriptEmpty')}</p>}
              {feed.map((e, i) => (e.k === 'note'
                ? <div key={i} className="text-[11px] text-center text-gray-500">{e.text}</div>
                : e.refusal
                  ? <RefusalCard key={i} refusal={e.refusal} />
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
