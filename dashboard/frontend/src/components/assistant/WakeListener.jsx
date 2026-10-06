/**
 * The wake phrase on every page of the hub, not only on the Assistant's:
 * with "Wake phrase" chosen there, the microphone stays open while the hub is
 * open in this browser, and "Assistant, what failed today?" said on the
 * Tasks page opens /assistant with that request as its turn ("Assistant"
 * alone opens it listening). The Assistant page listens for itself, so
 * nothing is mounted there, nor in a page it shows beside itself.
 *
 * A pill at the bottom says the microphone is on and turns it off.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Mic, MicOff, X } from 'lucide-react';
import { useI18n } from '../../i18n';
import { useWorkspace } from '../workspace';
import { AssistantError, getAssistant, transcribeRecording } from '../../api/assistant';
import useHandsFree, { handsFreeSupported } from './useHandsFree';
import { earTuning, readPrefs, writePrefs } from './assistantState';
import { DEFAULT_WAKE, findWake, wakePhrases } from './voiceCommands';
import { chime } from './chime';

function WakeEar({ prefs, onOff }) {
  const { t, language } = useI18n();
  const navigate = useNavigate();
  const { selectedWorkspace } = useWorkspace() || {};
  const [meta, setMeta] = useState(null);
  const [sttFallback, setSttFallback] = useState(false);
  const [blocked, setBlocked] = useState(false);

  useEffect(() => {
    let live = true;
    getAssistant('personal', selectedWorkspace)
      .catch(() => getAssistant('personal'))
      .then(({ data }) => { if (live) setMeta(data || {}); })
      .catch(() => { if (live) setMeta({}); });
    return () => { live = false; };
  }, [selectedWorkspace]);

  const serverStt = Boolean(meta?.voice?.transcription) && !sttFallback;
  const engine = serverStt && handsFreeSupported('server') ? 'server' : 'browser';
  const workspace = (meta?.workspaces || []).includes(selectedWorkspace) ? selectedWorkspace : (meta?.home || '');
  const phrases = useMemo(() => wakePhrases(prefs.wakePhrase), [prefs.wakePhrase]);
  const latest = useRef({});
  useEffect(() => { latest.current = { phrases, workspace, language, chimed: latest.current.chimed }; });

  const answer = useCallback((text) => {
    const hit = findWake(text, latest.current.phrases);
    if (!hit) return;
    if (!latest.current.chimed) chime();
    latest.current.chimed = false;
    navigate('/assistant', { state: { wake: { command: hit.rest } } });
  }, [navigate]);
  const onInterim = useCallback((text) => {
    if (latest.current.chimed || !findWake(text, latest.current.phrases)) return;
    latest.current.chimed = true;
    chime();
  }, []);
  const onSegment = useCallback(({ blob }) => {
    const { workspace: w, language: lang } = latest.current;
    transcribeRecording(blob, { workspace: w, language: lang, purpose: 'wake' })
      .then((res) => answer(res.text))
      .catch((e) => {
        if (e instanceof AssistantError && e.code === 'model_not_added') setSttFallback(true);
        else if (e instanceof AssistantError && e.code === 'budget') onOff();
      });
  }, [answer, onOff]);
  const onError = useCallback(() => setBlocked(true), []);

  const ear = useHandsFree({ engine, language, onSegment, onUtterance: answer, onInterim, onError });
  const { start, tune } = ear;
  const supported = handsFreeSupported(engine);
  useEffect(() => { tune(earTuning('wake')); }, [tune]);
  useEffect(() => { if (meta && supported) start(); }, [meta, start, supported]);

  if (!meta || !supported) return null;
  const phrase = prefs.wakePhrase.trim() || DEFAULT_WAKE[language] || DEFAULT_WAKE.en;
  return (
    <div className="fixed bottom-3 left-1/2 -translate-x-1/2 z-40 flex items-center gap-1.5 rounded-full border border-gray-200 bg-white/95 shadow-sm pl-3 pr-1 py-1 text-xs text-gray-600"
      data-testid="wake-listener" role="status">
      {blocked ? <MicOff className="w-3.5 h-3.5 text-amber-600" />
        : <Mic className={`w-3.5 h-3.5 text-indigo-600 ${ear.speech ? 'animate-pulse' : ''}`} />}
      <span>{blocked ? t('assistant.errors.micDenied') : t('assistant.wakeListener.listening', { phrase })}</span>
      <button type="button" onClick={onOff} title={t('assistant.wakeListener.off')} aria-label={t('assistant.wakeListener.off')}
        className="p-1 rounded-full text-gray-400 hover:bg-gray-100 hover:text-gray-700">
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

export default function WakeListener() {
  const location = useLocation();
  const [version, setVersion] = useState(0);
  // Read again on every page: the Assistant page is where the mode is chosen.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const prefs = useMemo(() => readPrefs(), [location.pathname, version]);
  const onOff = useCallback(() => {
    writePrefs({ ...readPrefs(), listen: 'hold' });
    setVersion((v) => v + 1);
  }, []);
  if (prefs.listen !== 'wake' || location.pathname.startsWith('/assistant')) return null;
  return <WakeEar prefs={prefs} onOff={onOff} />;
}
