/**
 * The assistant's voice on the page: a SpeechQueue bound to the hub's speech
 * route and one <audio> element, with the browser's own voice
 * (speechSynthesis) as the fallback when the workspace has no speech model or
 * the model fails.
 *
 * An item is what /api/assistant/speak takes: `{run_id, text}` for a sentence
 * of the answer, `{run_id, approval_id}` for a waiting card, `{run_id, tool,
 * agent}` for a long step. The last two carry `fallback`, the phrase the page
 * says itself when the browser speaks, since only the hub knows how to word
 * them otherwise; `{local: true, fallback}` is a phrase of the page's own.
 *
 * Browsers only play sound after a gesture, so `unlock()` is called from the
 * talk button's press: it starts the shared element on a silent clip, and
 * every later answer plays through that same element.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AssistantError, speakAssistant } from '../../api/assistant';
import { SpeechQueue } from './speechQueue';
import { plainSpeech } from './sentences';
import textLanguage from './textLanguage';

const SILENCE = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQAAAAA=';
const LOCALES = { en: 'en-US', ru: 'ru-RU', de: 'de-DE', uk: 'uk-UA' };
//: How often a sentence the server does not know yet (the turn runs on
//: another replica) is asked for again, and how far apart.
const NOT_READY_TRIES = 4;
const NOT_READY_WAIT_MS = 900;

export function browserSpeechAvailable() {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
    && typeof window.SpeechSynthesisUtterance === 'function';
}

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  const timer = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => { clearTimeout(timer); reject(new DOMException('aborted', 'AbortError')); });
});

export default function useSpeaker({ serverSpeech = true, language = 'en', voice = '', onFallback = null } = {}) {
  const [speaking, setSpeaking] = useState(false);
  const [useBrowser, setUseBrowser] = useState(!serverSpeech);
  const audioRef = useRef(null);
  const settings = useRef({ language, voice, useBrowser, onFallback });
  // One answer, one voice: the language a turn is read in, told from its
  // first sentence and kept for the rest of it (as the hub does).
  const turnLanguages = useRef(new Map());
  useEffect(() => {
    settings.current = { language, voice, useBrowser, onFallback };
  }, [language, voice, useBrowser, onFallback]);

  useEffect(() => { setUseBrowser(!serverSpeech); }, [serverSpeech]);

  const audio = useCallback(() => {
    if (!audioRef.current && typeof Audio === 'function') audioRef.current = new Audio();
    return audioRef.current;
  }, []);

  const queue = useMemo(() => new SpeechQueue({
    synthesize: async (item, signal) => {
      const browserClip = () => {
        const text = item.text ? plainSpeech(item.text) : item.fallback;
        if (!text) return null;
        const page = settings.current.language;
        let lang = textLanguage(text, page) || page;
        if (item.run_id && item.text) {
          const turns = turnLanguages.current;
          if (!turns.has(item.run_id)) {
            turns.set(item.run_id, lang);
            if (turns.size > 64) turns.delete(turns.keys().next().value);
          }
          lang = turns.get(item.run_id);
        }
        return { kind: 'browser', text, lang };
      };
      // A phrase of the page's own (a refusal said aloud) has no turn to
      // read it from: always the browser's voice.
      if (item.local || settings.current.useBrowser) return browserSpeechAvailable() ? browserClip() : null;
      const body = { ...item, language: settings.current.language, voice: settings.current.voice || '' };
      delete body.fallback;
      for (let attempt = 0; ; attempt += 1) {
        try {
          const blob = await speakAssistant(body, { signal });
          return blob ? { kind: 'audio', url: URL.createObjectURL(blob) } : null;
        } catch (err) {
          if (signal.aborted) throw err;
          const code = err instanceof AssistantError ? err.code : '';
          if (code === 'not_ready' && attempt < NOT_READY_TRIES) {
            await sleep(NOT_READY_WAIT_MS, signal);
            continue;
          }
          if (code === 'model_not_added' || code === 'provider_error' || !(err instanceof AssistantError)) {
            // The hub cannot speak here: the browser's voice for the rest of the visit.
            setUseBrowser(true);
            settings.current.useBrowser = true;
            if (settings.current.onFallback) settings.current.onFallback(code || 'network');
            return browserSpeechAvailable() ? browserClip() : null;
          }
          return null;   // refused (not this turn's words, budget): skip it
        }
      }
    },
    play: (clip, signal) => new Promise((resolve, reject) => {
      if (clip.kind === 'browser') {
        const utterance = new window.SpeechSynthesisUtterance(clip.text);
        // The answer's language picks the browser's voice, as the hub's does.
        const lang = clip.lang || settings.current.language;
        utterance.lang = LOCALES[lang] || lang;
        utterance.onend = () => resolve();
        utterance.onerror = (e) => (e.error === 'interrupted' || e.error === 'canceled' ? resolve() : reject(e));
        signal.addEventListener('abort', () => { window.speechSynthesis.cancel(); resolve(); });
        window.speechSynthesis.speak(utterance);
        return;
      }
      const el = audio();
      if (!el) { resolve(); return; }
      const done = () => { el.onended = null; el.onerror = null; resolve(); };
      el.onended = done;
      el.onerror = () => { el.onended = null; el.onerror = null; reject(new Error('playback failed')); };
      signal.addEventListener('abort', () => { el.pause(); done(); });
      el.src = clip.url;
      const started = el.play();
      if (started && typeof started.catch === 'function') started.catch(reject);
    }),
    release: (clip) => { if (clip?.kind === 'audio') URL.revokeObjectURL(clip.url); },
    onState: setSpeaking,
  }), [audio]);

  useEffect(() => () => queue.cancel(), [queue]);

  const say = useCallback((item) => queue.enqueue(item), [queue]);
  const cancel = useCallback(() => {
    queue.cancel();
    if (browserSpeechAvailable()) window.speechSynthesis.cancel();
  }, [queue]);

  /** Call from a click or key press: lets later answers play without one. */
  const unlock = useCallback(() => {
    const el = audio();
    if (el && el.paused && !queue.busy) {
      el.src = SILENCE;
      const p = el.play();
      if (p && typeof p.catch === 'function') p.catch(() => {});
    }
  }, [audio, queue]);

  return { say, cancel, unlock, speaking, browser: useBrowser };
}
