// Web-Worker host for client runtimes (§14): the compute loop lives here so a
// pegged simulation degrades the *frame rate of frames*, never the UI thread.
// Protocol (main → worker): init {name, spec, params} · params {params} ·
// playing {playing} · speed {speed} · step. Worker → main: frame {frame, t}.
// The worker steps on a fixed wall clock and posts at most one frame per tick;
// the hook applies frames on its own RAF, so a slow runtime just drops ticks.
import { createRuntime } from './index';

let rt = null;
let params = {};
let playing = false;
let speed = 1;
let t = 0;
let last = 0;
let timer = null;

const TICK_MS = 33;              // ~30 steps/s
const MAX_DT = 1 / 20;           // clamp stalls so integrators don't blow up

function emit() {
  if (!rt) return;
  try { postMessage({ type: 'frame', frame: rt.output(), t }); } catch { /* skip frame */ }
}

function loop() {
  if (!rt || !playing) { last = 0; return; }
  const now = Date.now();
  const dt = Math.min(MAX_DT, last ? (now - last) / 1000 : TICK_MS / 1000) * speed;
  last = now;
  try { rt.step(dt, params); t += dt; emit(); } catch { /* keep last frame */ }
}

self.onmessage = (e) => {
  const m = e.data || {};
  if (m.type === 'init') {
    rt = m.name ? createRuntime(m.name, m.spec) : null;
    params = m.params || {};
    t = 0; last = 0;
    if (!timer) timer = setInterval(loop, TICK_MS);
    emit();
  } else if (m.type === 'params') {
    params = m.params || {};
  } else if (m.type === 'playing') {
    playing = !!m.playing;
    last = 0;
  } else if (m.type === 'speed') {
    speed = m.speed || 1;
  } else if (m.type === 'step') {
    if (rt) {
      const dt = (1 / 30) * speed;
      try { rt.step(dt, params); t += dt; emit(); } catch { /* noop */ }
    }
  }
};
