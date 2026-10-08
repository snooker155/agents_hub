/**
 * The browser's own speech recognition (Web Speech API), for a workspace
 * with no transcription model. The page says so when it is in use: the audio
 * then goes to the browser's vendor (Google for Chrome, Apple for Safari),
 * not to a model the workspace chose.
 *
 *   const rec = useBrowserRecognition();
 *   rec.start('ru');                // on press
 *   const text = await rec.stop();  // on release: the transcript, '' for silence
 */
import { useCallback, useEffect, useRef, useState } from 'react';

const LOCALES = { en: 'en-US', ru: 'ru-RU', de: 'de-DE' };

function Recognition() {
  if (typeof window === 'undefined') return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

export function browserRecognitionAvailable() {
  return Boolean(Recognition());
}

export default function useBrowserRecognition() {
  const [listening, setListening] = useState(false);
  const session = useRef(null);

  useEffect(() => () => { try { session.current?.rec.abort(); } catch { /* gone */ } }, []);

  const start = useCallback((language = 'en') => {
    const Rec = Recognition();
    if (!Rec || session.current) return false;
    const rec = new Rec();
    rec.lang = LOCALES[language] || language;
    rec.continuous = true;
    rec.interimResults = false;
    const s = { rec, parts: [], ended: null };
    s.ended = new Promise((resolve) => {
      rec.onresult = (e) => {
        for (let i = e.resultIndex; i < e.results.length; i += 1) {
          if (e.results[i].isFinal) s.parts.push(e.results[i][0].transcript);
        }
      };
      rec.onend = () => resolve();
      rec.onerror = () => resolve();
    });
    session.current = s;
    rec.start();
    setListening(true);
    return true;
  }, []);

  const stop = useCallback(async () => {
    const s = session.current;
    if (!s) return '';
    try { s.rec.stop(); } catch { /* already stopped */ }
    await s.ended;
    session.current = null;
    setListening(false);
    return s.parts.join(' ').replace(/\s+/g, ' ').trim();
  }, []);

  const cancel = useCallback(() => {
    const s = session.current;
    session.current = null;
    setListening(false);
    try { s?.rec.abort(); } catch { /* already stopped */ }
  }, []);

  return { start, stop, cancel, listening, available: browserRecognitionAvailable() };
}
