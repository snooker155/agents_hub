/**
 * Push to talk: the microphone while the button (or Space) is held.
 *
 * MediaRecorder with webm/opus where the browser has it (Chrome, Firefox),
 * mp4/aac where that is what it has (Safari), and a plain WAV made from the
 * raw samples where it has neither. A level from 0 to 1 drives the meter
 * around the button, and at `maxSeconds` the host is told (`onLimit`) to end
 * it as if the button had been let go.
 *
 *   const rec = useRecorder({ maxSeconds: 60 });
 *   await rec.start();               // on press: asks for the microphone once
 *   const take = await rec.stop();   // on release: { blob, type, seconds } or null
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { WAV_RATE, encodeWav, levelOf, resample } from './wav';

const COMPRESSED = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];

/** The recording format this browser makes: a MediaRecorder type, or 'wav'. */
export function pickFormat(MR = typeof window !== 'undefined' ? window.MediaRecorder : undefined) {
  if (MR && typeof MR.isTypeSupported === 'function') {
    const hit = COMPRESSED.find((type) => MR.isTypeSupported(type));
    if (hit) return hit;
  }
  return 'wav';
}

export function recordingSupported() {
  return typeof navigator !== 'undefined' && Boolean(navigator.mediaDevices?.getUserMedia);
}

/** A press shorter than this is a click, not something said. */
const MIN_SECONDS = 0.35;

export default function useRecorder({ maxSeconds = 60, onLimit = null } = {}) {
  const [recording, setRecording] = useState(false);
  const onLimitRef = useRef(onLimit);
  useEffect(() => { onLimitRef.current = onLimit; }, [onLimit]);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState('');
  const session = useRef(null);

  const teardown = useCallback(() => {
    const s = session.current;
    if (!s) return;
    session.current = null;
    cancelAnimationFrame(s.raf);
    clearTimeout(s.limit);
    try { s.processor?.disconnect(); } catch { /* already gone */ }
    try { s.source?.disconnect(); } catch { /* already gone */ }
    s.stream?.getTracks().forEach((track) => track.stop());
    try { s.context?.close(); } catch { /* already closed */ }
    setRecording(false);
    setLevel(0);
  }, []);

  useEffect(() => teardown, [teardown]);

  const finish = useCallback(async () => {
    const s = session.current;
    if (!s) return null;
    const seconds = (performance.now() - s.started) / 1000;
    let take;
    if (s.recorder) {
      if (s.recorder.state !== 'inactive') {
        await new Promise((resolve) => {
          s.recorder.addEventListener('stop', resolve, { once: true });
          s.recorder.stop();
        });
      }
      const type = (s.recorder.mimeType || s.format).split(';')[0];
      take = { blob: new Blob(s.chunks, { type }), type, seconds };
    } else {
      const samples = resample(s.chunks, s.context?.sampleRate || WAV_RATE);
      take = { blob: new Blob([encodeWav(samples)], { type: 'audio/wav' }), type: 'audio/wav', seconds };
    }
    teardown();
    return seconds < MIN_SECONDS ? null : take;
  }, [teardown]);

  const start = useCallback(async () => {
    if (session.current) return;
    setError('');
    if (!recordingSupported()) {
      setError('unsupported');
      throw new Error('unsupported');
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      });
    } catch (err) {
      setError(err?.name === 'NotAllowedError' ? 'denied' : 'unavailable');
      throw err;
    }
    const format = pickFormat();
    const Ctx = window.AudioContext || window.webkitAudioContext;
    const context = Ctx ? new Ctx() : null;
    const s = { stream, context, format, chunks: [], started: performance.now(), raf: 0, limit: 0 };
    session.current = s;
    if (context) {
      s.source = context.createMediaStreamSource(stream);
      const analyser = context.createAnalyser();
      analyser.fftSize = 512;
      s.source.connect(analyser);
      const frame = new Uint8Array(analyser.fftSize);
      let last = 0;
      const tick = (now) => {
        if (session.current !== s) return;
        if (now - last > 60) {
          analyser.getByteTimeDomainData(frame);
          setLevel(levelOf(frame));
          last = now;
        }
        s.raf = requestAnimationFrame(tick);
      };
      s.raf = requestAnimationFrame(tick);
    }
    if (format !== 'wav') {
      s.recorder = new MediaRecorder(stream, { mimeType: format });
      s.recorder.addEventListener('dataavailable', (e) => { if (e.data?.size) s.chunks.push(e.data); });
      s.recorder.start(250);
    } else if (context) {
      // No MediaRecorder format the server takes: keep the raw samples.
      s.processor = context.createScriptProcessor(4096, 1, 1);
      s.processor.onaudioprocess = (e) => {
        if (!s.capped) s.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
      };
      s.source.connect(s.processor);
      s.processor.connect(context.destination);
    }
    // Held too long: the host ends it as if the button were released.
    s.limit = setTimeout(() => {
      if (session.current !== s) return;
      s.capped = true;
      if (onLimitRef.current) onLimitRef.current();
    }, maxSeconds * 1000);
    setRecording(true);
  }, [maxSeconds]);

  const cancel = useCallback(() => {
    const s = session.current;
    if (s?.recorder && s.recorder.state !== 'inactive') {
      try { s.recorder.stop(); } catch { /* already stopped */ }
    }
    teardown();
  }, [teardown]);

  return { start, stop: finish, cancel, recording, level, error, supported: recordingSupported() };
}
