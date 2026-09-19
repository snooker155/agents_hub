import { useState, useEffect, useRef, useCallback } from 'react';
import { useChannel } from '../../components/stream';
import { getViewClip } from '../../api';

// Consume the *precise* compute tier. Two sources feed one renderer:
//  - useLiveFrames: frames streamed from a running server job over view:<id>.
//  - useClipPlayer: a recorded clip fetched and replayed on a RAF clock.
// Frames are transient (design §14): live ones are held as "latest"; only a
// recorded clip persists, which is what timeline.mode === "recorded" plays.

export function useLiveFrames(viewId) {
  const [frame, setFrame] = useState(null);
  const [computing, setComputing] = useState(false);
  const [done, setDone] = useState(0); // bumps on compute_done so callers can reload a clip

  useChannel(viewId ? `view:${viewId}` : null, useCallback((ev) => {
    if (ev.type === 'frame') { setFrame(ev); setComputing(true); }
    else if (ev.type === 'compute_start') { setComputing(true); }
    else if (ev.type === 'compute_done') { setComputing(false); setDone((d) => d + 1); }
  }, []));

  return { frame, computing, done };
}

export function useClipPlayer(viewId, clipName, reloadKey = 0) {
  const [frames, setFrames] = useState(null);
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const raf = useRef(null);
  const last = useRef(0);
  const acc = useRef(0);

  useEffect(() => {
    let cancelled = false;
    if (!viewId || !clipName) return undefined;
    getViewClip(viewId, clipName)
      .then((r) => { if (!cancelled) { setFrames(r.data?.frames || []); setIdx(0); } })
      .catch(() => { if (!cancelled) setFrames(null); });
    return () => { cancelled = true; };
  }, [viewId, clipName, reloadKey]);

  // With no clip selected there is nothing to play. Derived here rather than
  // cleared with a setState inside the effect above (which cascades renders),
  // and it keeps the previous clip on screen while the next one loads.
  const n = (viewId && clipName && frames?.length) || 0;
  // The loop re-schedules itself through a ref so the callback never names
  // itself (a TDZ read at definition time); the ref always holds this render's
  // tick, and the effect below cancels/reschedules whenever it changes.
  const tickRef = useRef(null);
  const tick = useCallback((now) => {
    const dt = Math.min(0.1, (now - last.current) / 1000 || 0) * speed;
    last.current = now;
    acc.current += dt * 20; // ~20 recorded frames/sec at 1×
    if (acc.current >= 1) { const adv = Math.floor(acc.current); acc.current -= adv; setIdx((i) => (i + adv) % Math.max(1, n)); }
    raf.current = requestAnimationFrame((t) => tickRef.current?.(t));
  }, [speed, n]);

  useEffect(() => { tickRef.current = tick; }, [tick]);

  useEffect(() => {
    if (!playing || !n) return undefined;
    last.current = performance.now();
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [playing, tick, n]);

  const frame = n ? frames[Math.min(n - 1, Math.floor(idx))] : null;
  return {
    frame, ready: n > 0, count: n, index: Math.floor(idx),
    playing, setPlaying, speed, setSpeed,
    seek: (i) => setIdx(Math.max(0, Math.min(n - 1, i))),
    reset: () => { setIdx(0); setPlaying(false); },
  };
}
