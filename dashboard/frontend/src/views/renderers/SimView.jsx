import React, { useRef, useEffect, useCallback, lazy, Suspense } from 'react';
import { useRuntime } from '../runtimes/useRuntime';
import TimelineBar from '../TimelineBar';
import { publish } from '../viewBus';
import { useI18n } from '../../i18n';

// A simulation view backed by the precise server tier (spec.compute set by
// view_compute) renders streamed/recorded frames instead of a client runtime.
const ComputeView = lazy(() => import('./ComputeView'));

// Simulation renderer. A thin dispatcher (no hooks) picks the tier so hook
// order never depends on the spec: the precise server tier (spec.compute) →
// ComputeView; otherwise the real-time client runtime → ClientSimView.
export default function SimView({ view, theme, onOp }) {
  const { t } = useI18n();
  if (view?.spec?.compute?.runtime) {
    return (
      <Suspense fallback={<div className="text-sm text-gray-400 py-6 text-center">{t('viewSimView.loading')}</div>}>
        <ComputeView view={view} theme={theme} />
      </Suspense>
    );
  }
  return <ClientSimView view={view} theme={theme} onOp={onOp} />;
}

// blue→cyan→yellow→red ramp for a normalized value in [0,1] (matches ComputeView)
function heat(v) {
  const t = Math.max(0, Math.min(1, v));
  const r = Math.round(255 * Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 3))));
  const g = Math.round(255 * Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 2))));
  const b = Math.round(255 * Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 1))));
  return [r, g, b];
}

// Client runtime renderer — steps a runtime (particles/boids/nbody/sph2d/
// agents/wave/…) in a Web Worker and paints each frame on a 2D canvas.
// Positions render as speed-colored dots (heading → triangles); field frames
// (values+shape, the wave runtime) render as a heatmap; agents environments
// draw their obstacles and goals. Params are live-tunable (inline sliders emit
// update ops) and the timeline gives play/pause/step/speed — scenario 6 (and
// approximate 1/3).
function ClientSimView({ view, theme, onOp }) {
  // `t` below is the simulation time from useRuntime — alias the translator.
  const { t: translateSim } = useI18n();
  const spec = view?.spec || {};
  const runtimeName = spec.runtime;
  const canvasRef = useRef(null);
  const wrapRef = useRef(null);
  const dark = theme === 'dark';

  const getParams = useCallback(() => view?.spec?.params || {}, [view]);
  const { frame, t, playing, setPlaying, speed, setSpeed, stepOnce, reset } = useRuntime({
    runtimeName, spec, getParams, timeline: view?.timeline,
  });

  // linked views: drive the shared timebase with this sim's clock + aggregates
  const timebase = view?.link?.timebase;
  useEffect(() => {
    if (timebase && frame) publish(`timebase:${timebase}`, { t, aggregates: frame.aggregates || {} });
  }, [timebase, frame, t]);

  useEffect(() => {
    const cv = canvasRef.current; const wrap = wrapRef.current;
    if (!cv || !wrap || !frame) return;
    const W = wrap.clientWidth || 480; const H = wrap.clientHeight || 320;
    const dpr = window.devicePixelRatio || 1;
    cv.width = W * dpr; cv.height = H * dpr;
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = dark ? '#0b1120' : '#f8fafc';
    ctx.fillRect(0, 0, W, H);

    // field frame (wave runtime): heatmap the values grid
    if (frame.values && Array.isArray(frame.shape) && frame.shape.length === 2) {
      const [rows, cols] = frame.shape;
      const [r0, r1] = frame.range || [-1, 1];
      const img = ctx.createImageData(cols, rows);
      for (let i = 0; i < frame.values.length; i++) {
        const [r, g, b] = heat(r1 - r0 ? (frame.values[i] - r0) / (r1 - r0) : 0.5);
        img.data[i * 4] = r; img.data[i * 4 + 1] = g; img.data[i * 4 + 2] = b; img.data[i * 4 + 3] = 255;
      }
      const tmp = document.createElement('canvas'); tmp.width = cols; tmp.height = rows;
      tmp.getContext('2d').putImageData(img, 0, 0);
      ctx.imageSmoothingEnabled = true;
      const side = Math.min(W, H);
      ctx.drawImage(tmp, (W - side) / 2, (H - side) / 2, side, side);
      return;
    }

    const [bw, bh] = frame.bounds || [100, 100];
    const s = Math.min(W / bw, H / bh);
    const ox = (W - bw * s) / 2; const oy = (H - bh * s) / 2;
    const positions = frame.positions || [];

    // environment first: obstacles as filled circles, goals as rings (agents)
    if (frame.obstacles) {
      ctx.fillStyle = dark ? '#334155' : '#cbd5e1';
      for (const [x, y, r] of frame.obstacles) {
        ctx.beginPath(); ctx.arc(ox + x * s, oy + y * s, r * s, 0, Math.PI * 2); ctx.fill();
      }
    }
    if (frame.goals) {
      ctx.strokeStyle = '#22c55e'; ctx.lineWidth = 1.5;
      for (const [x, y] of frame.goals) {
        ctx.beginPath(); ctx.arc(ox + x * s, oy + y * s, 4, 0, Math.PI * 2); ctx.stroke();
      }
    }

    if (frame.heading) {
      // boids/agents — triangles pointing along heading
      ctx.fillStyle = '#3f66d8';
      for (const [x, y, a] of positions) {
        const px = ox + x * s; const py = oy + y * s;
        ctx.save(); ctx.translate(px, py); ctx.rotate(a);
        ctx.beginPath(); ctx.moveTo(4, 0); ctx.lineTo(-3, 2.2); ctx.lineTo(-3, -2.2); ctx.closePath(); ctx.fill();
        ctx.restore();
      }
    } else {
      // particles — dots colored by speed
      for (const [x, y, sp] of positions) {
        const px = ox + x * s; const py = oy + y * s;
        const hue = Math.max(210 - Math.min(sp, 60) * 3, 0);
        ctx.fillStyle = `hsl(${hue}, 80%, 60%)`;
        ctx.beginPath(); ctx.arc(px, py, 2, 0, Math.PI * 2); ctx.fill();
      }
    }
  }, [frame, dark]);

  if (!runtimeName) {
    return <div className="text-sm text-gray-500 p-4">{translateSim('viewSimView.noRuntime')}</div>;
  }

  const agg = frame?.aggregates || {};
  const paramKeys = Object.keys(spec.params || {})
    .filter((k) => k !== 'count' && typeof spec.params[k] === 'number');

  return (
    <div className="flex flex-col h-full">
      <div ref={wrapRef} className="flex-1 min-h-[240px] relative">
        <canvas ref={canvasRef} style={{ width: '100%', height: '100%' }} />
        <div className="absolute top-2 left-2 text-[11px] font-mono text-gray-400 space-x-3">
          {Object.entries(agg).map(([k, v]) => (
            <span key={k}>{k}={typeof v === 'number' ? v.toFixed(2) : String(v)}</span>
          ))}
        </div>
      </div>
      {paramKeys.length > 0 && (
        <div className="px-3 py-1.5 border-t border-gray-200 dark:border-gray-700 grid grid-cols-2 gap-x-4 gap-y-1">
          {paramKeys.map((k) => (
            <label key={k} className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
              <span className="font-mono truncate w-16">{k}</span>
              <input type="range" min={0} max={typeof spec.params[k] === 'number' ? Math.max(2, spec.params[k] * 3) : 2}
                step={0.01} value={spec.params[k]}
                onChange={(e) => onOp && onOp({ op: 'update', path: `spec.params.${k}`, value: Number(e.target.value) })}
                className="flex-1 accent-indigo-600" />
            </label>
          ))}
        </div>
      )}
      <TimelineBar t={t} playing={playing} onToggle={() => setPlaying((p) => !p)} onStep={stepOnce}
        onReset={reset} speed={speed} onSpeed={setSpeed} range={view?.timeline?.range} />
    </div>
  );
}
