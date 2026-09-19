import React, { useRef, useEffect, useMemo } from 'react';
import katex from 'katex';
import 'katex/dist/katex.min.css';
import { compile } from '../runtimes/expr';
import { useI18n } from '../../i18n';

// Math (plot) renderer — evaluates spec.expr with spec.params over spec.domain,
// drawing on a canvas. Three modes (spec.mode):
//   function2d  y = f(x)            — curve over the domain in spec.variable
//   parametric  "x(t), y(t)"        — expr is a comma pair evaluated over t
//   surface3d   z = f(x, y)         — isometric height surface, z heat-colored
// Params render as inline sliders (emitting update ops via onOp) and the KaTeX
// equation shows their live values, so tuning a param moves the plot and the
// equation term together — scenario 5.

function useSize(ref) {
  const [size, setSize] = React.useState({ w: 480, h: 300 });
  useEffect(() => {
    if (!ref.current) return undefined;
    const ro = new ResizeObserver((entries) => {
      const cr = entries[0].contentRect;
      setSize({ w: Math.max(200, cr.width), h: Math.max(160, cr.height) });
    });
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [ref]);
  return size;
}

// split "cos(3*t), sin(2*t)" on the top-level comma (commas inside f(x,y) stay)
function splitTopLevel(src) {
  const parts = [];
  let depth = 0; let cur = '';
  for (const ch of String(src || '')) {
    if (ch === '(') depth++;
    else if (ch === ')') depth--;
    if (ch === ',' && depth === 0) { parts.push(cur); cur = ''; } else cur += ch;
  }
  parts.push(cur);
  return parts.map((s) => s.trim()).filter(Boolean);
}

function heat(v) {
  const t = Math.max(0, Math.min(1, v));
  const r = Math.round(255 * Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 3))));
  const g = Math.round(255 * Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 2))));
  const b = Math.round(255 * Math.min(1, Math.max(0, 1.5 - Math.abs(4 * t - 1))));
  return `rgb(${r},${g},${b})`;
}

function drawAxes(ctx, w, h, sx, sy, x0, x1, ymin, ymax, dark) {
  ctx.strokeStyle = dark ? '#334155' : '#e2e8f0'; ctx.lineWidth = 1;
  if (ymin < 0 && ymax > 0) { ctx.beginPath(); ctx.moveTo(0, sy(0)); ctx.lineTo(w, sy(0)); ctx.stroke(); }
  if (x0 < 0 && x1 > 0) { ctx.beginPath(); ctx.moveTo(sx(0), 0); ctx.lineTo(sx(0), h); ctx.stroke(); }
}

function drawFunction2d(ctx, w, h, evalFns, params, variable, domain, dark) {
  const evalFn = evalFns[0];
  const [x0, x1] = domain;
  const N = Math.max(50, Math.floor(w));
  const xs = []; const ys = [];
  let ymin = Infinity; let ymax = -Infinity;
  for (let i = 0; i <= N; i++) {
    const x = x0 + (x1 - x0) * (i / N);
    const y = evalFn({ ...params, [variable]: x });
    xs.push(x); ys.push(y);
    if (Number.isFinite(y)) { ymin = Math.min(ymin, y); ymax = Math.max(ymax, y); }
  }
  if (!Number.isFinite(ymin) || !Number.isFinite(ymax) || ymin === ymax) { ymin = -1; ymax = 1; }
  const pad = (ymax - ymin) * 0.1; ymin -= pad; ymax += pad;
  const sx = (x) => ((x - x0) / (x1 - x0)) * w;
  const sy = (y) => h - ((y - ymin) / (ymax - ymin)) * h;
  drawAxes(ctx, w, h, sx, sy, x0, x1, ymin, ymax, dark);
  ctx.strokeStyle = '#3f66d8'; ctx.lineWidth = 2; ctx.beginPath();
  let started = false;
  for (let i = 0; i <= N; i++) {
    const y = ys[i];
    if (!Number.isFinite(y)) { started = false; continue; }
    const px = sx(xs[i]); const py = sy(y);
    if (!started) { ctx.moveTo(px, py); started = true; } else ctx.lineTo(px, py);
  }
  ctx.stroke();
}

function drawParametric(ctx, w, h, evalFns, params, variable, domain, dark, hint) {
  const [fx, fy] = evalFns;
  if (!fy) {
    ctx.fillStyle = dark ? '#94a3b8' : '#64748b'; ctx.font = '13px sans-serif';
    ctx.fillText(hint, 12, 24);
    return;
  }
  const [t0, t1] = domain;
  const N = 800;
  const pts = [];
  let xmin = Infinity; let xmax = -Infinity; let ymin = Infinity; let ymax = -Infinity;
  for (let i = 0; i <= N; i++) {
    const t = t0 + (t1 - t0) * (i / N);
    const b = { ...params, [variable]: t, t };
    const x = fx(b); const y = fy(b);
    pts.push([x, y]);
    if (Number.isFinite(x) && Number.isFinite(y)) {
      xmin = Math.min(xmin, x); xmax = Math.max(xmax, x);
      ymin = Math.min(ymin, y); ymax = Math.max(ymax, y);
    }
  }
  if (!Number.isFinite(xmin) || xmin === xmax) { xmin = -1; xmax = 1; }
  if (!Number.isFinite(ymin) || ymin === ymax) { ymin = -1; ymax = 1; }
  const padX = (xmax - xmin) * 0.1; const padY = (ymax - ymin) * 0.1;
  xmin -= padX; xmax += padX; ymin -= padY; ymax += padY;
  const sx = (x) => ((x - xmin) / (xmax - xmin)) * w;
  const sy = (y) => h - ((y - ymin) / (ymax - ymin)) * h;
  drawAxes(ctx, w, h, sx, sy, xmin, xmax, ymin, ymax, dark);
  ctx.strokeStyle = '#3f66d8'; ctx.lineWidth = 2; ctx.beginPath();
  let started = false;
  for (const [x, y] of pts) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) { started = false; continue; }
    if (!started) { ctx.moveTo(sx(x), sy(y)); started = true; } else ctx.lineTo(sx(x), sy(y));
  }
  ctx.stroke();
}

function drawSurface3d(ctx, w, h, evalFns, params, domain, domain2, dark) {
  const f = evalFns[0];
  const [x0, x1] = domain;
  const [y0, y1] = domain2;
  const N = 44;
  // sample the grid + z range
  const z = [];
  let zmin = Infinity; let zmax = -Infinity;
  for (let j = 0; j <= N; j++) {
    const row = [];
    for (let i = 0; i <= N; i++) {
      const x = x0 + (x1 - x0) * (i / N);
      const y = y0 + (y1 - y0) * (j / N);
      const v = f({ ...params, x, y });
      row.push(v);
      if (Number.isFinite(v)) { zmin = Math.min(zmin, v); zmax = Math.max(zmax, v); }
    }
    z.push(row);
  }
  if (!Number.isFinite(zmin) || zmin === zmax) { zmin = -1; zmax = 1; }
  const zn = (v) => (Number.isFinite(v) ? (v - zmin) / (zmax - zmin) : 0.5);
  // isometric projection of grid coords (u,v in 0..1) + normalized height
  const iso = (u, v, zz) => {
    const px = (u - v) * 0.72;
    const py = (u + v) * 0.38 - zz * 0.55;
    return [w / 2 + px * w * 0.55, h * 0.72 + py * h * 0.55 - h * 0.28];
  };
  // painter's order: far cells first
  for (let j = N - 1; j >= 0; j--) {
    for (let i = N - 1; i >= 0; i--) {
      const q = [
        iso(i / N, j / N, zn(z[j][i])),
        iso((i + 1) / N, j / N, zn(z[j][i + 1])),
        iso((i + 1) / N, (j + 1) / N, zn(z[j + 1][i + 1])),
        iso(i / N, (j + 1) / N, zn(z[j + 1][i])),
      ];
      const zc = (zn(z[j][i]) + zn(z[j][i + 1]) + zn(z[j + 1][i + 1]) + zn(z[j + 1][i])) / 4;
      ctx.fillStyle = heat(zc);
      ctx.strokeStyle = dark ? 'rgba(15,23,42,0.35)' : 'rgba(255,255,255,0.35)';
      ctx.lineWidth = 0.5;
      ctx.beginPath();
      ctx.moveTo(q[0][0], q[0][1]);
      for (let k = 1; k < 4; k++) ctx.lineTo(q[k][0], q[k][1]);
      ctx.closePath(); ctx.fill(); ctx.stroke();
    }
  }
  ctx.fillStyle = dark ? '#94a3b8' : '#64748b'; ctx.font = '11px monospace';
  ctx.fillText(`z ∈ [${zmin.toFixed(2)}, ${zmax.toFixed(2)}]`, 8, h - 8);
}

export default function MathView({ view, theme, onOp }) {
  const { t } = useI18n();
  // Everything derived from `spec` is memoised on the view: without that these
  // are fresh objects on every render, which is what the `JSON.stringify(...)`
  // entries in the redraw dependencies used to work around.
  const spec = useMemo(() => view?.spec || {}, [view]);
  const params = useMemo(() => spec.params || {}, [spec]);
  const variable = spec.variable || 'x';
  const paramVariable = spec.variable || 't';   // parametric curves default to t
  const mode = spec.mode || 'function2d';
  const domain = useMemo(
    () => (Array.isArray(spec.domain) && spec.domain.length === 2 ? spec.domain : [-10, 10]),
    [spec],
  );
  const domain2 = useMemo(
    () => (Array.isArray(spec.domain2) && spec.domain2.length === 2 ? spec.domain2 : domain),
    [spec, domain],
  );
  const wrapRef = useRef(null);
  const canvasRef = useRef(null);
  const { w, h } = useSize(wrapRef);
  const dark = theme === 'dark';

  const evalFns = useMemo(() => {
    const parts = mode === 'parametric' ? splitTopLevel(spec.expr) : [spec.expr || '0'];
    return parts.map((p) => { try { return compile(p); } catch { return () => NaN; } });
  }, [spec.expr, mode]);

  useEffect(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const dpr = window.devicePixelRatio || 1;
    cv.width = w * dpr; cv.height = h * dpr;
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (mode === 'parametric') drawParametric(ctx, w, h, evalFns, params, paramVariable, domain, dark, t('viewMathView.parametricHint'));
    else if (mode === 'surface3d') drawSurface3d(ctx, w, h, evalFns, params, domain, domain2, dark);
    else drawFunction2d(ctx, w, h, evalFns, params, variable, domain, dark);
  }, [w, h, evalFns, mode, params, domain, domain2, variable, paramVariable, dark, t]);

  const eqHtml = useMemo(() => {
    if (!spec.latex) return null;
    try { return katex.renderToString(spec.latex, { throwOnError: false, displayMode: true }); } catch { return null; }
  }, [spec.latex]);

  if (!spec.expr) return <div className="text-sm text-gray-500 p-4">{t('viewMathView.noExpressionYetTheAgent')}</div>;

  const paramKeys = Object.keys(params);
  return (
    <div className="flex flex-col h-full">
      {eqHtml && (
        <div className="px-3 pt-2 text-gray-900 dark:text-gray-100 overflow-x-auto" dangerouslySetInnerHTML={{ __html: eqHtml }} />
      )}
      <div ref={wrapRef} className="flex-1 min-h-[200px] relative">
        <canvas ref={canvasRef} style={{ width: '100%', height: '100%' }} />
      </div>
      {paramKeys.length > 0 && (
        <div className="px-3 py-2 border-t border-gray-200 dark:border-gray-700 grid grid-cols-2 gap-x-4 gap-y-1">
          {paramKeys.map((k) => (
            <label key={k} className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
              <span className="font-mono w-6">{k}</span>
              <input type="range" min={-10} max={10} step={0.1} value={params[k]}
                onChange={(e) => onOp && onOp({ op: 'update', path: `spec.params.${k}`, value: Number(e.target.value) })}
                className="flex-1 accent-indigo-600" />
              <span className="font-mono tabular-nums w-10 text-right">{Number(params[k]).toFixed(1)}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
