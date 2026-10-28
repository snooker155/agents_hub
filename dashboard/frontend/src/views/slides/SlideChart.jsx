import React from 'react';

// The chart of a `chart` slide (views/models.py SlideChart): bar, line, area or
// pie over categories, drawn as plain SVG with inline attributes so it prints
// into the PDF and scales with the stage like the rest of the slide. The .pptx
// export draws the same chart as a native PowerPoint chart
// (views/slides_pptx.py), in the same series colours.

import { colourAt, formatValue, isNum, niceTicks, seriesColours } from './chartMath';

// The value range the axis has to show: stacked bars and areas add up per
// category (positives and negatives apart), the rest take each value.
function valueRange(chart) {
  const series = chart.series || [];
  const n = (chart.categories || []).length;
  let lo = 0;
  let hi = 0;
  if (chart.stacked) {
    for (let i = 0; i < n; i += 1) {
      let pos = 0;
      let neg = 0;
      for (const s of series) {
        const v = s.values?.[i];
        if (!isNum(v)) continue;
        if (v >= 0) pos += v; else neg += v;
      }
      hi = Math.max(hi, pos);
      lo = Math.min(lo, neg);
    }
  } else {
    for (const s of series) {
      for (const v of s.values || []) {
        if (!isNum(v)) continue;
        hi = Math.max(hi, v);
        lo = Math.min(lo, v);
      }
    }
  }
  return [lo, hi];
}

function Legend({ items, x, y, width, t }) {
  // Laid out by estimate (a character is about 0.55 em): SVG text has no flow.
  const size = 15;
  const widths = items.map((it) => 26 + Math.min(220, String(it.name).length * size * 0.55) + 22);
  const total = widths.reduce((a, b) => a + b, 0);
  const startX = x + Math.max(0, (width - total) / 2);
  const offsets = widths.map((_, i) => startX + widths.slice(0, i).reduce((a, b) => a + b, 0));
  return (
    <g>
      {items.map((it, i) => {
        const at = offsets[i];
        return (
          <g key={i}>
            <rect x={at} y={y - 11} width={16} height={16} rx={3} fill={it.colour} />
            <text x={at + 24} y={y + 2} fontSize={size} fill={t.text}>{it.name}</text>
          </g>
        );
      })}
    </g>
  );
}

function PieChart({ chart, width, height, t, colours }) {
  const values = (chart.series?.[0]?.values || []).map((v) => (isNum(v) && v > 0 ? v : 0));
  const cats = chart.categories || [];
  const total = values.reduce((a, b) => a + b, 0);
  const legendH = 40;
  const r = Math.max(10, Math.min(width / 2, (height - legendH) / 2) - 16);
  const cx = width / 2;
  const cy = (height - legendH) / 2;
  const sweeps = values.map((v) => (total ? (v / total) * Math.PI * 2 : 0));
  const slices = values.map((v, i) => {
    const a0 = -Math.PI / 2 + sweeps.slice(0, i).reduce((a, b) => a + b, 0);
    return { v, i, a0, a1: a0 + sweeps[i], sweep: sweeps[i] };
  });
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: 'block', overflow: 'visible' }}>
      {total === 0 && <circle cx={cx} cy={cy} r={r} fill={t.surface} />}
      {slices.map(({ v, i, a0, a1, sweep }) => {
        if (!v) return null;
        const colour = colourAt(colours, i);
        const full = sweep >= Math.PI * 2 - 1e-6;
        const p0 = [cx + r * Math.cos(a0), cy + r * Math.sin(a0)];
        const p1 = [cx + r * Math.cos(a1), cy + r * Math.sin(a1)];
        const mid = (a0 + a1) / 2;
        const share = v / total;
        return (
          <g key={i}>
            {full
              ? <circle cx={cx} cy={cy} r={r} fill={colour} />
              : <path d={`M ${cx} ${cy} L ${p0[0]} ${p0[1]} A ${r} ${r} 0 ${sweep > Math.PI ? 1 : 0} 1 ${p1[0]} ${p1[1]} Z`}
                  fill={colour} stroke={t.bg} strokeWidth={2} />}
            {share >= 0.06 && (
              <text x={cx + r * 0.62 * Math.cos(mid)} y={cy + r * 0.62 * Math.sin(mid) + 6} textAnchor="middle"
                fontSize={18} fontWeight={700} fill="#ffffff">
                {Math.round(share * 100)}%
              </text>
            )}
          </g>
        );
      })}
      <Legend items={cats.map((name, i) => ({ name, colour: colourAt(colours, i) }))} x={0} y={height - 14} width={width} t={t} />
    </svg>
  );
}

function AxisChart({ chart, width, height, t, colours }) {
  const type = chart.type || 'bar';
  const horizontal = type === 'bar' && !!chart.horizontal;
  const stacked = !!chart.stacked && type !== 'line';
  const cats = chart.categories || [];
  const series = chart.series || [];
  const unit = chart.unit || '';
  const n = Math.max(1, cats.length);
  const showLegend = series.length > 1;

  const [lo, hi] = valueRange({ ...chart, stacked });
  const ticks = niceTicks(lo, hi);
  const vMin = ticks[0];
  const vMax = ticks[ticks.length - 1];

  const tickSize = 14;
  const catSize = n > 12 ? 12 : 15;
  const rotate = !horizontal && n > 8;
  const longestTick = Math.max(...ticks.map((v) => formatValue(v, unit).length));
  const longestCat = Math.max(1, ...cats.map((c) => String(c).length));

  // Plot box inside the svg: room for tick labels, the axis titles, the
  // category labels and the legend.
  // x_label names the categories, y_label the values, whichever way they run.
  const leftTitle = horizontal ? chart.x_label : chart.y_label;
  const bottomTitle = horizontal ? chart.y_label : chart.x_label;
  const left = (leftTitle ? 30 : 0) + (horizontal
    ? Math.min(220, longestCat * catSize * 0.56) + 14
    : longestTick * tickSize * 0.6 + 14);
  const bottomLabels = horizontal ? tickSize + 12 : rotate ? Math.min(110, longestCat * catSize * 0.45) + 16 : catSize + 14;
  const bottom = bottomLabels + (bottomTitle ? 28 : 0) + (showLegend ? 38 : 0);
  const top = 16;
  const right = 16;
  const pw = Math.max(10, width - left - right);
  const ph = Math.max(10, height - top - bottom);

  // Value to pixel along the value axis, category band along the other one.
  const span = vMax - vMin || 1;
  const valPos = (v) => (horizontal ? left + ((v - vMin) / span) * pw : top + ph - ((v - vMin) / span) * ph);
  const bandLen = (horizontal ? ph : pw) / n;
  const bandStart = (i) => (horizontal ? top : left) + i * bandLen;
  const zero = valPos(Math.max(vMin, Math.min(0, vMax)));

  const grid = ticks.map((v) => {
    const p = valPos(v);
    return horizontal
      ? <g key={v}><line x1={p} y1={top} x2={p} y2={top + ph} stroke={t.border} strokeWidth={1} />
          <text x={p} y={top + ph + tickSize + 6} textAnchor="middle" fontSize={tickSize} fill={t.muted}>{formatValue(v, unit)}</text></g>
      : <g key={v}><line x1={left} y1={p} x2={left + pw} y2={p} stroke={t.border} strokeWidth={1} />
          <text x={left - 10} y={p + 5} textAnchor="end" fontSize={tickSize} fill={t.muted}>{formatValue(v, unit)}</text></g>;
  });

  const catLabels = cats.map((c, i) => {
    const mid = bandStart(i) + bandLen / 2;
    if (horizontal) {
      return <text key={i} x={left - 10} y={mid + 5} textAnchor="end" fontSize={catSize} fill={t.text}>{c}</text>;
    }
    return rotate
      ? <text key={i} x={mid} y={top + ph + 14} textAnchor="end" fontSize={catSize} fill={t.text}
          transform={`rotate(-35 ${mid} ${top + ph + 14})`}>{c}</text>
      : <text key={i} x={mid} y={top + ph + catSize + 8} textAnchor="middle" fontSize={catSize} fill={t.text}>{c}</text>;
  });

  let marks;
  if (type === 'bar') {
    const groups = stacked ? 1 : series.length || 1;
    const inner = bandLen * 0.72;
    const barLen = inner / groups;
    const labelled = !stacked && n * series.length <= 16;
    const pos = new Array(n).fill(0);
    const neg = new Array(n).fill(0);
    marks = series.map((s, si) => (s.values || []).map((v, i) => {
      if (!isNum(v)) return null;
      let from = 0;
      if (stacked) {
        from = v >= 0 ? pos[i] : neg[i];
        if (v >= 0) pos[i] += v; else neg[i] += v;
      }
      const a = valPos(from);
      const b = valPos(from + v);
      const off = bandStart(i) + (bandLen - inner) / 2 + (stacked ? 0 : si * barLen);
      const colour = colourAt(colours, si);
      const rect = horizontal
        ? { x: Math.min(a, b), y: off, width: Math.abs(b - a), height: barLen * 0.92 }
        : { x: off, y: Math.min(a, b), width: barLen * 0.92, height: Math.abs(b - a) };
      return (
        <g key={`${si}-${i}`}>
          <rect {...rect} fill={colour} rx={2} />
          {labelled && (horizontal
            ? <text x={b + (v >= 0 ? 6 : -6)} y={rect.y + rect.height / 2 + 5} textAnchor={v >= 0 ? 'start' : 'end'} fontSize={13} fill={t.text}>{formatValue(v, unit)}</text>
            : <text x={rect.x + rect.width / 2} y={v >= 0 ? b - 6 : b + 16} textAnchor="middle" fontSize={13} fill={t.text}>{formatValue(v, unit)}</text>)}
        </g>
      );
    }));
  } else {
    // line and area: a point per category at the band's middle; a null breaks
    // the line. Stacked areas sit on the sum of the series below them.
    const acc = new Array(n).fill(0);
    const layers = series.map((s) => {
      const base = acc.slice();
      const pts = (s.values || []).map((v, i) => {
        if (!isNum(v)) return null;
        const y0 = stacked ? base[i] : 0;
        const y1 = y0 + v;
        if (stacked) acc[i] = y1;
        return { x: bandStart(i) + bandLen / 2, y: valPos(y1), y0: valPos(y0) };
      });
      return pts;
    });
    marks = layers.map((pts, si) => {
      const colour = colourAt(colours, si);
      const runs = [];
      let run = [];
      for (const p of pts) {
        if (p) run.push(p);
        else if (run.length) { runs.push(run); run = []; }
      }
      if (run.length) runs.push(run);
      return (
        <g key={si}>
          {type === 'area' && runs.map((r, ri) => (
            <path key={`a${ri}`} fill={colour} fillOpacity={0.35} stroke="none"
              d={`M ${r.map((p) => `${p.x} ${p.y}`).join(' L ')} L ${r.slice().reverse().map((p) => `${p.x} ${stacked ? p.y0 : zero}`).join(' L ')} Z`} />
          ))}
          {runs.map((r, ri) => (
            <polyline key={`l${ri}`} points={r.map((p) => `${p.x},${p.y}`).join(' ')} fill="none" stroke={colour} strokeWidth={4}
              strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {type === 'line' && pts.map((p, i) => (p ? <circle key={i} cx={p.x} cy={p.y} r={6} fill={t.bg} stroke={colour} strokeWidth={3} /> : null))}
        </g>
      );
    });
  }

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{ display: 'block', overflow: 'visible' }}>
      {grid}
      {marks}
      {horizontal
        ? <line x1={zero} y1={top} x2={zero} y2={top + ph} stroke={t.muted} strokeWidth={1.5} />
        : <line x1={left} y1={zero} x2={left + pw} y2={zero} stroke={t.muted} strokeWidth={1.5} />}
      {catLabels}
      {leftTitle && (
        <text x={14} y={top + ph / 2} textAnchor="middle" fontSize={15} fill={t.muted} transform={`rotate(-90 14 ${top + ph / 2})`}>
          {leftTitle}
        </text>
      )}
      {bottomTitle && (
        <text x={left + pw / 2} y={top + ph + bottomLabels + 20} textAnchor="middle" fontSize={15} fill={t.muted}>
          {bottomTitle}
        </text>
      )}
      {showLegend && (
        <Legend items={series.map((s, i) => ({ name: s.name || `#${i + 1}`, colour: colourAt(colours, i) }))}
          x={left} y={height - 14} width={pw} t={t} />
      )}
    </svg>
  );
}

/** A slide chart in a box of `width` x `height` stage pixels. */
export default function SlideChart({ chart, width, height, t, accent }) {
  if (!chart || !(chart.series || []).length || !(chart.categories || []).length) {
    return (
      <div style={{ width, height, background: t.surface, borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'center', color: t.muted, fontSize: 40 }}>
        📊
      </div>
    );
  }
  const colours = seriesColours(t, accent);
  const Chart = chart.type === 'pie' ? PieChart : AxisChart;
  return <Chart chart={chart} width={width} height={height} t={t} colours={colours} />;
}
