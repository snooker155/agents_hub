import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { createRuntime } from './index';

// Drives a client runtime, preferring a Web Worker (runtimeWorker.js) so the
// compute loop never blocks the UI thread (§14's guardrail); where workers are
// unavailable (or construction fails) it falls back to the original
// main-thread requestAnimationFrame clock. Either way: tunable params are read
// live (control changes take effect mid-run), and the runtime is re-created
// only when its *structural* inputs change (name, population, bounds) — not on
// every param tweak, so tuning a slider doesn't reset the simulation.

const MAX_DT = 1 / 20; // clamp long frames (tab was backgrounded) to avoid blowups

function makeWorker() {
  try {
    if (typeof Worker === 'undefined') return null;
    return new Worker(new URL('./runtimeWorker.js', import.meta.url), { type: 'module' });
  } catch {
    return null;
  }
}

export function useRuntime({ runtimeName, spec, getParams, timeline }) {
  const [frame, setFrame] = useState(null);
  const [t, setT] = useState(timeline?.t || 0);
  // always start paused — even timeline.mode 'live' runs only after an explicit
  // Play, so reloading a page never kicks off simulations on its own
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(timeline?.speed || 1);

  const workerRef = useRef(undefined);   // undefined = not tried, null = fallback
  const rtRef = useRef(null);            // main-thread fallback instance
  const rafRef = useRef(null);
  const lastRef = useRef(0);
  const paramsRef = useRef(getParams);
  paramsRef.current = getParams;

  const count = spec?.params?.count;
  const structureKey = `${runtimeName}|${count}|${JSON.stringify(spec?.bounds || [])}|${JSON.stringify(spec?.entities || {})}`;
  const paramsJson = useMemo(
    () => JSON.stringify(getParams ? getParams() : {}),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [spec?.params],
  );

  // (Re)create the runtime on structural change — in the worker when possible.
  useEffect(() => {
    if (workerRef.current === undefined) {
      const w = makeWorker();
      workerRef.current = w;
      if (w) {
        w.onmessage = (e) => {
          if (e.data?.type === 'frame') { setFrame(e.data.frame); setT(e.data.t || 0); }
        };
        w.onerror = () => {           // dead worker (e.g. blocked) → fall back
          try { w.terminate(); } catch { /* noop */ }
          workerRef.current = null;
          rtRef.current = runtimeName ? createRuntime(runtimeName, spec) : null;
          if (rtRef.current) setFrame(rtRef.current.output());
        };
      }
    }
    const w = workerRef.current;
    if (w) {
      w.postMessage({ type: 'init', name: runtimeName || '', spec,
        params: paramsRef.current ? paramsRef.current() : {} });
      w.postMessage({ type: 'playing', playing });
      w.postMessage({ type: 'speed', speed });
    } else {
      rtRef.current = runtimeName ? createRuntime(runtimeName, spec) : null;
      if (rtRef.current) setFrame(rtRef.current.output());
    }
    setT(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structureKey]);

  // terminate the worker with the component; reset the ref so a remount (e.g.
  // StrictMode's mount→unmount→mount in dev) creates a fresh worker instead of
  // posting to the terminated one
  useEffect(() => () => {
    if (workerRef.current) { try { workerRef.current.terminate(); } catch { /* noop */ } }
    workerRef.current = undefined;
  }, []);

  // live param flow: worker gets a message; fallback reads paramsRef each tick
  useEffect(() => {
    if (workerRef.current) workerRef.current.postMessage({ type: 'params', params: JSON.parse(paramsJson) });
  }, [paramsJson]);

  useEffect(() => {
    if (workerRef.current) workerRef.current.postMessage({ type: 'playing', playing });
  }, [playing]);

  useEffect(() => {
    if (workerRef.current) workerRef.current.postMessage({ type: 'speed', speed });
  }, [speed]);

  // ── main-thread fallback clock (only when no worker) ──────────────────────
  const tick = useCallback((now) => {
    const rt = rtRef.current;
    if (rt) {
      const dt = Math.min(MAX_DT, (now - lastRef.current) / 1000 || 0) * speed;
      lastRef.current = now;
      try { rt.step(dt, paramsRef.current ? paramsRef.current() : {}); setFrame(rt.output()); } catch { /* keep last */ }
      setT((prev) => prev + dt);
    }
    rafRef.current = requestAnimationFrame(tick);
  }, [speed]);

  useEffect(() => {
    if (!playing || workerRef.current) return undefined;
    lastRef.current = performance.now();
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, [playing, tick]);

  const stepOnce = useCallback(() => {
    if (workerRef.current) { workerRef.current.postMessage({ type: 'step' }); return; }
    const rt = rtRef.current;
    if (!rt) return;
    const dt = (1 / 30) * speed;
    try { rt.step(dt, paramsRef.current ? paramsRef.current() : {}); setFrame(rt.output()); } catch { /* noop */ }
    setT((p) => p + dt);
  }, [speed]);

  const reset = useCallback(() => {
    if (workerRef.current) {
      workerRef.current.postMessage({ type: 'init', name: runtimeName || '', spec,
        params: paramsRef.current ? paramsRef.current() : {} });
      workerRef.current.postMessage({ type: 'playing', playing });
      workerRef.current.postMessage({ type: 'speed', speed });
    } else {
      rtRef.current = runtimeName ? createRuntime(runtimeName, spec) : null;
      setFrame(rtRef.current ? rtRef.current.output() : null);
    }
    setT(0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [structureKey, playing, speed]);

  return { frame, t, playing, setPlaying, speed, setSpeed, stepOnce, reset };
}
