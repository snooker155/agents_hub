import { useCallback, useEffect, useRef, useState } from 'react';
import { AssistantError, getAssistantVoice, transcribeRecording } from '../../api/assistant';
import useRecorder from '../assistant/useRecorder';
import useBrowserRecognition from '../assistant/useBrowserRecognition';

/**
 * A voice message from the Chat page's composer: hold the microphone (or
 * Space) and speak, let go and what was said is sent as the message.
 *
 * The same two ears as the Assistant page: the workspace's transcription
 * model when it has one (`/api/assistant/transcribe`), else the browser's own
 * recognition. Which one is asked once per workspace through
 * `/api/assistant/voice`, which leaves the Assistant's live conversation
 * where it is.
 *
 *   const voice = useChatVoice({ workspace, language, onText: (text) => send(text) });
 *   voice.available   // a microphone this page can use
 *   voice.start()     // on press
 *   voice.end()       // on release: transcribes and calls onText
 */
export default function useChatVoice({ workspace, language, mode, onText, onError }) {
  const [serverStt, setServerStt] = useState(false);
  const [maxSeconds, setMaxSeconds] = useState(60);
  const [talking, setTalking] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const how = useRef(null);
  const endRef = useRef(() => {});
  const onTextRef = useRef(onText);
  const onErrorRef = useRef(onError);
  useEffect(() => { onTextRef.current = onText; onErrorRef.current = onError; }, [onText, onError]);

  const recorder = useRecorder({ maxSeconds, onLimit: () => endRef.current() });
  const browserRec = useBrowserRecognition();

  useEffect(() => {
    let live = true;
    getAssistantVoice(workspace, mode)
      .then(({ data }) => {
        if (!live) return;
        setServerStt(Boolean(data?.voice?.transcription));
        setMaxSeconds(Math.min(60, Number(data?.voice?.max_seconds) || 60));
      })
      .catch(() => { if (live) setServerStt(false); });
    return () => { live = false; };
  }, [workspace, mode]);

  const server = serverStt && recorder.supported;
  const available = server || browserRec.available;

  const heard = useCallback((text) => {
    const said = String(text || '').trim();
    if (said) onTextRef.current?.(said);
    else onErrorRef.current?.('nothing');
  }, []);

  const start = useCallback(async () => {
    if (how.current || transcribing) return;
    if (server) {
      how.current = 'server';
      setTalking(true);
      try {
        await recorder.start();
        // Let go while the browser was still opening the microphone (its
        // first permission prompt): nothing was said, nothing is kept.
        if (how.current !== 'server') recorder.cancel();
      } catch {
        how.current = null;
        setTalking(false);
        onErrorRef.current?.(recorder.error === 'denied' ? 'denied' : 'mic');
      }
    } else if (browserRec.available) {
      how.current = 'browser';
      setTalking(true);
      browserRec.start(language);
    } else {
      onErrorRef.current?.('unavailable');
    }
  }, [browserRec, language, recorder, server, transcribing]);

  const end = useCallback(async () => {
    const was = how.current;
    if (!was) return;
    how.current = null;
    setTalking(false);
    if (was === 'browser') { heard(await browserRec.stop()); return; }
    const take = await recorder.stop();
    if (!take) {  // a click, not something said, or nothing but silence
      if (recorder.wasSilent?.()) heard('');
      return;
    }
    setTranscribing(true);
    try {
      const res = await transcribeRecording(take.blob, { mode, workspace, language });
      heard(res?.text);
    } catch (e) {
      if (e instanceof AssistantError && e.code === 'model_not_added') {
        // The model went away: the browser listens from the next press.
        setServerStt(false);
        onErrorRef.current?.('fallback');
      } else {
        onErrorRef.current?.(e?.message || 'transcribe');
      }
    } finally {
      setTranscribing(false);
    }
  }, [browserRec, heard, language, mode, recorder, workspace]);
  useEffect(() => { endRef.current = end; }, [end]);

  return {
    available, talking, transcribing, level: recorder.level, start, end,
    active: () => Boolean(how.current),
  };
}
