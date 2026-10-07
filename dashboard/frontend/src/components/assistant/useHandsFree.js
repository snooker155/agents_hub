/**
 * The microphone left open: the ear of the hands-free modes (a conversation,
 * the wake phrase) and of a spoken stop while the assistant works.
 *
 * Two engines, chosen the way push to talk chooses (useRecorder.js,
 * useBrowserRecognition.js):
 *
 *  - `server`: the workspace's transcription model listens. The raw samples
 *    go through a VadSegmenter (vad.js); each stretch of speech, with a
 *    moment of what came before it so the first word is whole, becomes a WAV
 *    file for `onSegment({ blob, seconds, startedAt })`. The host sends it to
 *    /api/assistant/transcribe. `tune({ maxSeconds, overflow })` caps a
 *    stretch: `cut` hands over what was heard so far, `drop` throws a long
 *    one away untranscribed (a stop is short; the assistant's own voice from
 *    the speakers or a conversation in the room is not).
 *  - `browser`: the browser's own recognition, continuous, with interim
 *    words: `onInterim(text)` as they come (a stop is acted on before the
 *    sentence ends), `onUtterance(text, { startedAt })` once it settles.
 *    Chrome ends a recognition every minute or so; it is started again.
 *
 *   const ear = useHandsFree({ engine: 'server', onSegment });
 *   await ear.start();   // asks for the microphone once
 *   ear.ready            // true once sound comes in: the moment to start speaking
 *   ear.tune({ silenceMs: 500, maxSeconds: 3, overflow: 'drop', sensitivity: 2 });
 *   ear.stop();
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { WAV_RATE, encodeWav, resample } from './wav';
import { VadSegmenter } from './vad';

const LOCALES = { en: 'en-US', ru: 'ru-RU', de: 'de-DE' };
//: Kept from before speech starts, so its first sound is in the file.
const PRE_ROLL_SECONDS = 0.4;
//: Shorter than this (past the silence that ended it) is a knock, not a word.
const MIN_VOICED_SECONDS = 0.3;

export const TUNE_DEFAULTS = { silenceMs: 900, maxSeconds: 60, overflow: 'cut', sensitivity: 1 };

function Recognition() {
  if (typeof window === 'undefined') return null;
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

/** Root mean square of float samples, stretched like wav.js levelOf. */
function levelOfSamples(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
  return Math.min(1, Math.sqrt(sum / (samples.length || 1)) * 3.5);
}

/** Whether a microphone was already allowed for this site (no prompt needed). */
export async function micAllowed() {
  try {
    const status = await navigator.permissions?.query({ name: 'microphone' });
    return status?.state === 'granted';
  } catch {
    return false;
  }
}

export function handsFreeSupported(engine) {
  if (engine === 'browser') return Boolean(Recognition());
  return typeof navigator !== 'undefined' && Boolean(navigator.mediaDevices?.getUserMedia);
}

export default function useHandsFree({
  engine = 'server', language = 'en', onSegment = null, onUtterance = null, onInterim = null, onError = null,
} = {}) {
  const [listening, setListening] = useState(false);
  const [level, setLevel] = useState(0);
  const [speech, setSpeech] = useState(false);
  // Sound is coming in. Opening the microphone takes from a moment to over a
  // second (a Bluetooth headset switches its profile); what is said before
  // this is not heard, so the host shows it and starts the conversation here.
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const session = useRef(null);
  // A start waiting for the microphone, and whether the host still wants
  // it: a stop while the browser opens the microphone wins over that start,
  // and a start right after the stop (React's double effects) keeps it.
  const starting = useRef(null);
  const wanted = useRef(false);
  const tuning = useRef({ ...TUNE_DEFAULTS });
  const handlers = useRef({});
  useEffect(() => {
    handlers.current = { onSegment, onUtterance, onInterim, onError, language };
  }, [onSegment, onUtterance, onInterim, onError, language]);

  const stop = useCallback(() => {
    wanted.current = false;
    const s = session.current;
    if (!s) return;
    session.current = null;
    s.active = false;
    clearTimeout(s.timer);
    clearTimeout(s.restart);
    try { s.rec?.abort(); } catch { /* already stopped */ }
    try { s.processor?.disconnect(); } catch { /* already gone */ }
    try { s.source?.disconnect(); } catch { /* already gone */ }
    s.stream?.getTracks().forEach((track) => track.stop());
    try { s.context?.close(); } catch { /* already closed */ }
    setListening(false);
    setReady(false);
    setLevel(0);
    setSpeech(false);
  }, []);

  useEffect(() => stop, [stop]);

  const tune = useCallback((patch) => {
    const next = { ...tuning.current, ...patch };
    const changed = Object.keys(next).some((k) => next[k] !== tuning.current[k]);
    tuning.current = next;
    const s = session.current;
    if (!changed || !s) return;
    if (s.vad) {
      s.vad.set({ silenceMs: next.silenceMs });
      s.vad.sensitivity = next.sensitivity;
    }
  }, []);

  /** Forget the stretch being heard: what was said before a phase change is not for the next one. */
  const reset = useCallback(() => {
    const s = session.current;
    if (!s) return;
    if (s.vad) {
      s.vad.reset();
      // A stretch still going on keeps its last moment as the pre-roll: when
      // the person spoke as the phase changed (the answer ended), their first
      // word is in the next stretch instead of thrown away with this one.
      if (s.segment) {
        s.pre = s.segment.chunks;
        s.preSamples = s.segment.samples;
      }
      s.segment = null;
      s.skip = false;
    } else {
      clearTimeout(s.timer);
      s.finals = [];
      s.startedAt = null;
    }
    setSpeech(false);
  }, []);

  const startServer = useCallback(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    if (!wanted.current || session.current) {
      stream.getTracks().forEach((track) => track.stop());
      return false;
    }
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const context = new Ctx();
    if (context.state === 'suspended') await context.resume().catch(() => {});
    const rate = context.sampleRate || WAV_RATE;
    const vad = new VadSegmenter({ silenceMs: tuning.current.silenceMs });
    vad.sensitivity = tuning.current.sensitivity;
    const s = {
      active: true, stream, context, vad, pre: [], preSamples: 0, segment: null, skip: false, lastMeter: 0,
    };
    session.current = s;
    s.source = context.createMediaStreamSource(stream);
    s.processor = context.createScriptProcessor(2048, 1, 1);

    const emit = (segment) => {
      const silence = tuning.current.silenceMs / 1000;
      // Off the audio thread: resampling a long stretch takes a moment.
      setTimeout(() => {
        if (!s.active) return;
        const samples = resample(segment.chunks, rate);
        const seconds = samples.length / WAV_RATE;
        if (seconds - (segment.ended ? silence : 0) < MIN_VOICED_SECONDS) return;
        const blob = new Blob([encodeWav(samples)], { type: 'audio/wav' });
        handlers.current.onSegment?.({ blob, seconds, startedAt: segment.startedAt });
      }, 0);
    };

    s.processor.onaudioprocess = (e) => {
      if (!s.active) return;
      if (!s.ready) {
        s.ready = true;
        setReady(true);
      }
      const data = new Float32Array(e.inputBuffer.getChannelData(0));
      const level = levelOfSamples(data);
      const now = performance.now();
      if (now - s.lastMeter > 60) {
        setLevel(level);
        s.lastMeter = now;
      }
      const event = vad.push(level, now);
      if (s.segment) {
        s.segment.chunks.push(data);
        s.segment.samples += data.length;
        const { maxSeconds, overflow } = tuning.current;
        if (s.segment.samples / rate > maxSeconds) {
          if (overflow === 'cut') emit(s.segment);
          // The rest of this stretch is not heard; the next one is.
          s.segment = null;
          s.skip = true;
        }
      } else {
        s.pre.push(data);
        s.preSamples += data.length;
        while (s.pre.length > 1 && s.preSamples - s.pre[0].length >= PRE_ROLL_SECONDS * rate) {
          s.preSamples -= s.pre.shift().length;
        }
      }
      if (event === 'start') {
        setSpeech(true);
        if (!s.skip) {
          s.segment = {
            chunks: s.pre, samples: s.preSamples, startedAt: now - (s.preSamples / rate) * 1000,
          };
        }
        s.pre = [];
        s.preSamples = 0;
      } else if (event === 'end') {
        setSpeech(false);
        if (s.segment) emit({ ...s.segment, ended: true });
        s.segment = null;
        s.skip = false;
      }
    };
    s.source.connect(s.processor);
    s.processor.connect(context.destination);
    return true;
  }, []);

  const startBrowser = useCallback(() => {
    const Rec = Recognition();
    const s = { active: true, finals: [], interim: '', startedAt: null, timer: 0, restart: 0 };
    session.current = s;
    const settle = () => {
      const text = [...s.finals, s.interim].join(' ').replace(/\s+/g, ' ').trim();
      const startedAt = s.startedAt;
      s.finals = [];
      s.interim = '';
      s.startedAt = null;
      setSpeech(false);
      if (text && s.active) handlers.current.onUtterance?.(text, { startedAt });
    };
    const make = () => {
      if (!s.active || session.current !== s) return;
      const rec = new Rec();
      rec.lang = LOCALES[handlers.current.language] || handlers.current.language || 'en-US';
      rec.continuous = true;
      rec.interimResults = true;
      rec.onresult = (e) => {
        let interim = '';
        for (let i = e.resultIndex; i < e.results.length; i += 1) {
          const result = e.results[i];
          if (result.isFinal) s.finals.push(result[0].transcript);
          else interim += result[0].transcript;
        }
        s.interim = interim.trim();
        if (s.startedAt === null) s.startedAt = performance.now();
        if (!s.ready) {
          s.ready = true;
          setReady(true);
        }
        setSpeech(true);
        const current = [...s.finals, s.interim].join(' ').replace(/\s+/g, ' ').trim();
        if (current) handlers.current.onInterim?.(current);
        clearTimeout(s.timer);
        // Settled when nothing new came for the silence the host asked for;
        // words still being guessed get longer before they are taken as said.
        const wait = tuning.current.silenceMs * (s.interim ? 2.5 : 0.8);
        s.timer = setTimeout(settle, wait);
      };
      // The browser's recognition says when it hears; it gives no frames.
      rec.onaudiostart = () => {
        if (!s.ready && s.active) {
          s.ready = true;
          setReady(true);
        }
      };
      rec.onerror = (e) => {
        if (e.error === 'not-allowed' || e.error === 'service-not-allowed') {
          s.active = false;
          setError('denied');
          setListening(false);
          handlers.current.onError?.('denied');
        }
      };
      rec.onend = () => {
        if (s.active && session.current === s) s.restart = setTimeout(make, 250);
      };
      s.rec = rec;
      try { rec.start(); } catch { s.restart = setTimeout(make, 1000); }
    };
    make();
    return true;
  }, []);

  const start = useCallback(() => {
    wanted.current = true;
    if (session.current) return Promise.resolve(true);
    // Asked again while the microphone is still being opened: the same start.
    if (starting.current) return starting.current.promise;
    setError('');
    if (!handsFreeSupported(engine)) {
      setError('unsupported');
      return Promise.resolve(false);
    }
    const entry = {};
    starting.current = entry;
    entry.promise = (async () => {
      try {
        const ok = engine === 'browser' ? startBrowser() : await startServer();
        if (ok) setListening(true);
        return ok;
      } catch (err) {
        session.current = null;
        const code = err?.name === 'NotAllowedError' ? 'denied' : 'unavailable';
        setError(code);
        handlers.current.onError?.(code);
        return false;
      } finally {
        if (starting.current === entry) starting.current = null;
      }
    })();
    return entry.promise;
  }, [engine, startBrowser, startServer]);

  // A new engine (the transcription model went away) starts over with it.
  useEffect(() => {
    if (session.current) {
      stop();
      start();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine]);

  return { start, stop, tune, reset, listening, ready, level, speech, error, engine };
}
