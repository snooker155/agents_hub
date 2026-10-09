/**
 * The assistant inside the first run, from the voice screen on: the same
 * turn, voice and microphone as the Assistant page (api/assistant.js,
 * components/assistant/), in a smaller frame. Each turn names the screen it
 * is asked from (`first_run_screen`), so the assistant knows it is in the
 * setup and answers about that screen (common/first_run.py `prompt_lines`).
 *
 * The answer is read aloud sentence by sentence while it streams, with the
 * hub's speech model, which picks the voice of each sentence's language
 * (providers/speech_languages.py), or the browser's own voice without one.
 * Listening records for the hub's transcription model, or uses the browser's
 * recognition without one.
 *
 * Cards are not shown here: a turn that asks for one is stopped, and the
 * person is told the screen's own buttons do it.
 */
import { useCallback, useRef, useState } from 'react';
import { getAssistant, stopAssistant, streamAssistantTurn, transcribeRecording } from '../../api/assistant';
import useSpeaker from '../assistant/useSpeaker';
import useRecorder from '../assistant/useRecorder';
import useBrowserRecognition from '../assistant/useBrowserRecognition';
import { SentenceStream, firstParagraphSentences, isSpeakable } from '../assistant/sentences';

export default function useWizardAssistant({ language = 'en' } = {}) {
  const [voice, setVoice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState('');
  const [heard, setHeard] = useState('');
  const [error, setError] = useState('');
  const [held, setHeld] = useState(false);
  const [speechFailed, setSpeechFailed] = useState('');
  const [listening, setListening] = useState(false);
  const via = useRef('');
  const runRef = useRef(null);

  const speaker = useSpeaker({
    serverSpeech: Boolean(voice?.speech), language, onFallback: setSpeechFailed,
  });
  const recorder = useRecorder({ maxSeconds: 30 });
  const recognition = useBrowserRecognition();

  /** What the hub would speak and hear with now; call after the voice changes. */
  const refreshVoice = useCallback(async () => {
    try {
      const { data } = await getAssistant();
      setVoice(data?.voice || {});
      setSpeechFailed('');
      return data?.voice || {};
    } catch {
      setVoice({});
      return {};
    }
  }, []);

  // The last answer's spoken sentences, for "say it again".
  const said = useRef([]);
  const say = useCallback((sentence) => {
    if (!isSpeakable(sentence) || !runRef.current) return false;
    const item = { run_id: runRef.current, text: sentence };
    said.current.push(item);
    speaker.say(item);
    return true;
  }, [speaker]);

  const replay = useCallback(() => {
    speaker.cancel();
    said.current.forEach((item) => speaker.say(item));
  }, [speaker]);

  /** One turn. Resolves to the answer's text, '' when there was none. */
  const ask = useCallback(async (message, { spoken = false, screen = '', speak = true } = {}) => {
    const text = String(message || '').trim();
    if (!text) return '';
    speaker.cancel();
    setBusy(true);
    setError('');
    setHeld(false);
    setReply('');
    // A typed question: what was heard before belongs to an older one.
    if (!spoken) setHeard('');
    runRef.current = null;
    said.current = [];
    let answer = '';
    let spoke = false;
    const sentences = new SentenceStream();
    try {
      await streamAssistantTurn({
        body: { message: text, voice: spoken, ...(screen ? { first_run_screen: screen } : {}) },
        onEvent: (ev) => {
          if (ev.type === 'run') runRef.current = ev.run_id;
          else if (ev.type === 'token') {
            answer += ev.token || '';
            setReply(answer);
            if (speak) sentences.push(ev.token || '').forEach((s) => { spoke = say(s) || spoke; });
          } else if (ev.type === 'tool_approval') {
            setHeld(true);
            stopAssistant().catch(() => {});
          } else if (ev.type === 'message') {
            answer = ev.content || answer;
            setReply(answer);
            if (speak) {
              const rest = sentences.end();
              (!spoke && !rest.length ? firstParagraphSentences(answer) : rest).forEach(say);
            }
          }
        },
      });
    } catch (e) {
      if (e?.name !== 'AbortError') setError(e?.message || 'failed');
    } finally {
      setBusy(false);
    }
    return answer;
  }, [say, speaker]);

  /** Call from the press that starts it: the browser plays sound only after one. */
  const unlock = useCallback(() => speaker.unlock(), [speaker]);

  const canListen = Boolean((voice?.transcription && recorder.supported) || recognition.available);

  const startListening = useCallback(async () => {
    setError('');
    setHeard('');
    speaker.cancel();
    if (voice?.transcription && recorder.supported) {
      via.current = 'hub';
      await recorder.start();
    } else if (recognition.available) {
      via.current = 'browser';
      recognition.start(language);
    } else {
      return false;
    }
    setListening(true);
    return true;
  }, [language, recognition, recorder, speaker, voice]);

  /** Resolves to what was said, '' for nothing heard. */
  const stopListening = useCallback(async () => {
    setListening(false);
    let text = '';
    try {
      if (via.current === 'hub') {
        const blob = await recorder.stop();
        if (blob) text = (await transcribeRecording(blob, { language })).text || '';
      } else if (via.current === 'browser') {
        text = await recognition.stop();
      }
    } catch (e) {
      setError(e?.message || 'failed');
    }
    via.current = '';
    text = String(text || '').trim();
    setHeard(text);
    return text;
  }, [language, recognition, recorder]);

  return {
    voice, refreshVoice, ask, replay, unlock, busy, reply, heard, error, held,
    speaking: speaker.speaking, cancelSpeech: speaker.cancel, speechFailed, browserVoice: speaker.browser,
    listening: listening || recorder.recording || recognition.listening, level: recorder.level,
    canListen, startListening, stopListening, micError: recorder.error,
  };
}
