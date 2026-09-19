import React, { useRef, useEffect } from 'react';
import { useLiveFrames, useClipPlayer } from '../runtimes/useFrames';
import TimelineBar from '../TimelineBar';
import { publish } from '../viewBus';
import { useI18n } from '../../i18n';

// Precise server-compute renderer (Phase 6). Draws frames streamed live from a
// running job, then replays the recorded clip. Understands three channel shapes:
//   positions [[x,y,z],…] → points (N-body)
//   values + shape [H,W]  → 2D heatmap (wave field)
//   values + shape [N]    → 1D density curve + potential overlay (Schrödinger)

function heat(v) {
  // simple blue→cyan→yellow→red ramp for a normalized value in [0,1]
  const t = Math.max(0, Math.min(1, v));
  const r = Math.round(255 * Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 3))));
  const g = Math.round(255 * Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 2))));
  const b = Math.round(255 * Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 1))));
  return [r, g, b];
}

function draw(ctx, W, H, channels, dark) {
  ctx.fillStyle = dark ? '#0b1120' : '#0f172a';
  ctx.fillRect(0, 0, W, H);
  if (!channels) return;

  if (channels.positions) {
    const pts = channels.positions;
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const p of pts) { minX = Math.min(minX, p[0]); maxX = Math.max(maxX, p[0]); minY = Math.min(minY, p[1]); maxY = Math.max(maxY, p[1]); }
    const span = Math.max(maxX - minX, maxY - minY, 1e-6);
    const s = Math.min(W, H) * 0.85 / span;
    const cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    for (let i = 0; i < pts.length; i++) {
      const p = pts[i];
      const px = W / 2 + (p[0] - cx) * s;
      const py = H / 2 - (p[1] - cy) * s;
      const z = p[2] || 0;
      ctx.fillStyle = i === 0 ? '#fbbf24' : `hsl(${210 + Math.max(-40, Math.min(40, z * 40))},80%,${i === 0 ? 60 : 65}%)`;
      ctx.beginPath(); ctx.arc(px, py, i === 0 ? 4 : 1.6, 0, Math.PI * 2); ctx.fill();
    }
    return;
  }

  if (channels.values && Array.isArray(channels.shape)) {
    const vals = channels.values;
    const [rng0, rng1] = channels.range || [Math.min(...vals), Math.max(...vals)];
    const norm = (v) => (rng1 - rng0 ? (v - rng0) / (rng1 - rng0) : 0.5);
    if (channels.shape.length === 2) {
      const [rows, cols] = channels.shape;
      const img = ctx.createImageData(cols, rows);
      for (let i = 0; i < vals.length; i++) {
        const [r, g, b] = heat(norm(vals[i]));
        img.data[i * 4] = r; img.data[i * 4 + 1] = g; img.data[i * 4 + 2] = b; img.data[i * 4 + 3] = 255;
      }
      // scale the small field up to the canvas
      const tmp = document.createElement('canvas'); tmp.width = cols; tmp.height = rows;
      tmp.getContext('2d').putImageData(img, 0, 0);
      ctx.imageSmoothingEnabled = true;
      const side = Math.min(W, H);
      ctx.drawImage(tmp, (W - side) / 2, (H - side) / 2, side, side);
    } else {
      // 1D density curve + potential overlay
      const N = vals.length;
      const pot = channels.potential;
      ctx.strokeStyle = '#475569'; ctx.lineWidth = 1;
      if (pot) {
        ctx.beginPath();
        for (let i = 0; i < pot.length; i++) { const x = (i / (pot.length - 1)) * W; const y = H / 2 - pot[i] * H * 0.35; i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
        ctx.stroke();
      }
      ctx.fillStyle = 'rgba(63,102,216,0.55)'; ctx.strokeStyle = '#7ea6ff'; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(0, H);
      for (let i = 0; i < N; i++) { const x = (i / (N - 1)) * W; const y = H - norm(vals[i]) * H * 0.9; ctx.lineTo(x, y); }
      ctx.lineTo(W, H); ctx.closePath(); ctx.fill();
    }
  }
}

export default function ComputeView({ view, theme }) {
  const { t } = useI18n();
  const viewId = view?.view_id;
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);
  const dark = theme === 'dark';
  const { frame: liveFrame, computing, done } = useLiveFrames(viewId);
  const clipName = view?.spec?.compute?.clip || view?.timeline?.clip;
  const clip = useClipPlayer(viewId, clipName, done);

  // prefer live frames while a job is streaming; otherwise replay the clip
  const active = computing && liveFrame ? liveFrame : clip.frame;
  const channels = active?.channels;

  // linked views: the precise tier drives the shared timebase as well
  const timebase = view?.link?.timebase;
  useEffect(() => {
    if (timebase && active) publish(`timebase:${timebase}`, { t: active.t || 0, aggregates: channels?.aggregates || {} });
  }, [timebase, active, channels]);

  useEffect(() => {
    const cv = canvasRef.current; const wrap = wrapRef.current;
    if (!cv || !wrap) return;
    const W = wrap.clientWidth || 480; const H = wrap.clientHeight || 360;
    const dpr = window.devicePixelRatio || 1;
    cv.width = W * dpr; cv.height = H * dpr;
    const ctx = cv.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw(ctx, W, H, channels, dark);
  }, [channels, dark]);

  const agg = channels?.aggregates || {};

  return (
    <div className="flex flex-col h-full">
      <div ref={wrapRef} className="flex-1 min-h-[260px] relative">
        <canvas ref={canvasRef} style={{ width: '100%', height: '100%' }} />
        <div className="absolute top-2 left-2 text-[11px] font-mono text-gray-300 space-x-3">
          {computing && <span className="text-amber-400">{t('viewComputeView.computing')}</span>}
          {Object.entries(agg).map(([k, v]) => <span key={k}>{k}={typeof v === 'number' ? v.toFixed(3) : String(v)}</span>)}
        </div>
        {!channels && (
          <div className="absolute inset-0 grid place-items-center text-sm text-gray-400">
            {clipName ? 'Loading…' : t('viewComputeView.runComputation')}
          </div>
        )}
      </div>
      {clip.ready && !computing && (
        <TimelineBar t={clip.index} playing={clip.playing} onToggle={() => clip.setPlaying((p) => !p)}
          onStep={() => clip.seek(clip.index + 1)} onReset={clip.reset}
          speed={clip.speed} onSpeed={clip.setSpeed} range={[0, clip.count - 1]} />
      )}
    </div>
  );
}
