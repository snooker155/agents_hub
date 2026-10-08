import { useEffect, useRef, useState } from 'react';
import { fmtCompact, fmtDuration, fmtPercent, niceMax } from './cacheStats';

/**
 * The prompt cache card's charts, drawn as plain SVG in the chart tokens of
 * theme.css (--chart-*): thin columns grown from one baseline with a 2px
 * surface gap between stacked parts, a 2px line, hairline grids, and a
 * tooltip on every column or point that repeats what the table view shows.
 */

function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    setWidth(el.clientWidth);
    if (typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

// A column's top part: square at the bottom, 4px rounded at the data end.
function topRounded(x, y, w, h, r = 4) {
  const rr = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

function timeLabel(t, locale) {
  return new Date(t * 1000).toLocaleTimeString(locale || [], { hour: '2-digit', minute: '2-digit' });
}

function Tooltip({ x, width, children }) {
  // Kept inside the plot: flips to the left of the pointer near the right edge.
  const left = x > width - 180 ? x - 172 : x + 12;
  return (
    <div
      className="absolute top-1 z-10 pointer-events-none rounded-lg border border-gray-200 bg-white shadow-md px-2.5 py-1.5 text-xs min-w-[150px]"
      style={{ left: Math.max(0, left) }}
      role="status"
    >
      {children}
    </div>
  );
}

function KeyRow({ color, value, label }) {
  return (
    <div className="flex items-center gap-2">
      <span className="inline-block w-3 h-0.5 rounded" style={{ background: color }} />
      <span className="font-semibold text-gray-800 tabular-nums">{value}</span>
      <span className="text-gray-500">{label}</span>
    </div>
  );
}

export function Legend({ items }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-600">
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1.5">
          <span
            className={it.line ? 'inline-block w-3 h-0.5 rounded' : 'inline-block w-2.5 h-2.5 rounded-sm'}
            style={{ background: it.color, opacity: it.opacity ?? 1 }}
          />
          {it.label}
        </span>
      ))}
    </div>
  );
}

const AXIS_W = 44;
const AXIS_H = 18;
// Room above the plot for the top tick's label.
const PAD_T = 8;

/** Prompt tokens per column: the cached part on the baseline in the accent,
 * the computed part above it in the muted gray. */
export function TokenColumns({ buckets, labels, height = 150 }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const plotW = Math.max(0, width - AXIS_W);
  const max = niceMax(Math.max(...buckets.map((b) => b.cached + b.computed), 0));
  const band = buckets.length ? plotW / buckets.length : 0;
  const colW = Math.max(2, Math.min(24, band * 0.7));
  const y = (v) => PAD_T + height - (v / max) * height;
  const ticks = [0, max / 2, max];
  const base = PAD_T + height;
  const every = Math.max(1, Math.ceil(buckets.length / Math.max(1, Math.floor(plotW / 64))));
  const h = hover !== null ? buckets[hover] : null;

  return (
    <div ref={ref} className="relative w-full" data-testid="cache-token-chart">
      {width > 0 && (
        <svg width={width} height={PAD_T + height + AXIS_H} role="img" aria-label={labels.title}>
          {ticks.map((v) => (
            <g key={v}>
              <line x1={AXIS_W} x2={width} y1={y(v)} y2={y(v)} stroke="var(--chart-grid)" strokeWidth="1" />
              <text x={AXIS_W - 6} y={y(v) + 4} textAnchor="end" className="fill-gray-400" fontSize="10">{fmtCompact(v)}</text>
            </g>
          ))}
          {buckets.map((b, i) => {
            const x = AXIS_W + i * band + (band - colW) / 2;
            const cachedH = (b.cached / max) * height;
            const computedH = (b.computed / max) * height;
            const gap = cachedH > 0 && computedH > 0 ? 2 : 0;
            return (
              <g key={b.t} opacity={hover === null || hover === i ? 1 : 0.55}>
                {cachedH > 0 && (computedH > 0
                  ? <rect x={x} y={base - cachedH} width={colW} height={cachedH} fill="var(--chart-accent)" />
                  : <path d={topRounded(x, base - cachedH, colW, cachedH)} fill="var(--chart-accent)" />)}
                {computedH > 0 && (
                  <path d={topRounded(x, base - cachedH - computedH, colW, Math.max(0, computedH - gap))} fill="var(--chart-muted)" />
                )}
                {i % every === 0 && (
                  <text x={AXIS_W + i * band + band / 2} y={base + 13} textAnchor="middle" className="fill-gray-400" fontSize="10">
                    {timeLabel(b.t, labels.locale)}
                  </text>
                )}
                <rect
                  x={AXIS_W + i * band} y={PAD_T} width={band} height={height} fill="transparent"
                  tabIndex={0} aria-label={`${timeLabel(b.t, labels.locale)}: ${b.cached} / ${b.computed}`}
                  onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}
                  onFocus={() => setHover(i)} onBlur={() => setHover(null)}
                />
              </g>
            );
          })}
        </svg>
      )}
      {h && (
        <Tooltip x={AXIS_W + hover * band + band / 2} width={width}>
          <p className="text-gray-500 mb-1">{timeLabel(h.t, labels.locale)}</p>
          <KeyRow color="var(--chart-accent)" value={fmtCompact(h.cached)} label={labels.cached} />
          <KeyRow color="var(--chart-muted)" value={fmtCompact(h.computed)} label={labels.computed} />
          <p className="mt-1 text-gray-500">{labels.hit}: <span className="font-semibold text-gray-800">{fmtPercent(h.hit)}</span> · {labels.requests}: {h.requests}</p>
        </Tooltip>
      )}
    </div>
  );
}

/** Average time to the first written word per column, a 2px line over a
 * light wash; columns without streamed calls leave a gap. */
export function TtftLine({ buckets, labels, height = 90 }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const plotW = Math.max(0, width - AXIS_W);
  const max = niceMax(Math.max(...buckets.map((b) => b.ttft || 0), 0));
  const band = buckets.length ? plotW / buckets.length : 0;
  const x = (i) => AXIS_W + i * band + band / 2;
  const y = (v) => PAD_T + height - (v / max) * height;
  const base = PAD_T + height;
  const dur = labels.duration || fmtDuration;
  const runs = [];
  let cur = [];
  buckets.forEach((b, i) => {
    if (b.ttft === null) { if (cur.length) runs.push(cur); cur = []; } else cur.push(i);
  });
  if (cur.length) runs.push(cur);
  const h = hover !== null ? buckets[hover] : null;

  return (
    <div ref={ref} className="relative w-full" data-testid="cache-ttft-chart">
      {width > 0 && (
        <svg
          width={width} height={PAD_T + height + 4} role="img" aria-label={labels.title}
          onPointerMove={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            const i = Math.floor((e.clientX - r.left - AXIS_W) / (band || 1));
            setHover(i >= 0 && i < buckets.length ? i : null);
          }}
          onPointerLeave={() => setHover(null)}
        >
          {[0, max].map((v) => (
            <g key={v}>
              <line x1={AXIS_W} x2={width} y1={y(v)} y2={y(v)} stroke="var(--chart-grid)" strokeWidth="1" />
              <text x={AXIS_W - 6} y={y(v) + 4} textAnchor="end" className="fill-gray-400" fontSize="10">{dur(v)}</text>
            </g>
          ))}
          {runs.map((run) => {
            const pts = run.map((i) => `${x(i)},${y(buckets[i].ttft)}`).join(' ');
            const area = `M${x(run[0])},${base} L${pts.replaceAll(' ', ' L')} L${x(run[run.length - 1])},${base} Z`;
            return (
              <g key={run[0]}>
                <path d={area} fill="var(--chart-accent)" opacity="0.1" />
                {run.length > 1
                  ? <polyline points={pts} fill="none" stroke="var(--chart-accent)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
                  : <circle cx={x(run[0])} cy={y(buckets[run[0]].ttft)} r="4" fill="var(--chart-accent)" stroke="var(--surface-card)" strokeWidth="2" />}
              </g>
            );
          })}
          {h && (
            <>
              <line x1={x(hover)} x2={x(hover)} y1={PAD_T} y2={base} stroke="var(--border-strong)" strokeWidth="1" />
              {h.ttft !== null && (
                <circle cx={x(hover)} cy={y(h.ttft)} r="4" fill="var(--chart-accent)" stroke="var(--surface-card)" strokeWidth="2" />
              )}
            </>
          )}
        </svg>
      )}
      {h && (
        <Tooltip x={x(hover)} width={width}>
          <p className="text-gray-500 mb-1">{timeLabel(h.t, labels.locale)}</p>
          <KeyRow color="var(--chart-accent)" value={dur(h.ttft)} label={labels.ttft} />
          <p className="mt-1 text-gray-500">{labels.streamed}: {h.ttftN}</p>
        </Tooltip>
      )}
    </div>
  );
}

/** One horizontal bar of parts against a whole (memory, disk): 2px surface
 * gaps between the parts, the rest of the whole as the track. */
export function PartsBar({ parts, total, label }) {
  const [hover, setHover] = useState(null);
  const shown = parts.filter((p) => p.value > 0);
  const sum = shown.reduce((n, p) => n + p.value, 0);
  const whole = Math.max(total || 0, sum) || 1;
  return (
    <div className="relative" data-testid="cache-parts-bar">
      <div className="flex h-3 w-full rounded overflow-hidden gap-[2px]" role="img" aria-label={label}>
        {shown.map((p, i) => (
          <div
            key={p.label}
            className="h-full shrink-0"
            style={{ width: `${(p.value / whole) * 100}%`, background: p.color, opacity: p.opacity ?? (hover === null || hover === i ? 1 : 0.6) }}
            onPointerEnter={() => setHover(i)} onPointerLeave={() => setHover(null)}
            title={`${p.label}: ${p.text}`}
          />
        ))}
        {sum < whole && <div className="h-full flex-1 min-w-0" style={{ background: 'var(--chart-track)' }} />}
      </div>
    </div>
  );
}

/** A 0..1 ratio as a short meter: the accent fill on a lighter track. */
export function Meter({ value, width = 64 }) {
  const v = value === null || value === undefined ? 0 : Math.max(0, Math.min(1, value));
  return (
    <span className="inline-flex items-center gap-2" data-testid="cache-meter">
      <span className="inline-block h-1.5 rounded-full overflow-hidden" style={{ width, background: 'var(--chart-track)' }}>
        <span className="block h-full rounded-full" style={{ width: `${v * 100}%`, background: 'var(--chart-accent)' }} />
      </span>
      <span className="tabular-nums text-gray-700">{fmtPercent(value)}</span>
    </span>
  );
}
