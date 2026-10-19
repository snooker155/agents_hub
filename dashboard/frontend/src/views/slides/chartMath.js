// Numbers behind a chart slide (SlideChart.jsx): the series colours, which the
// .pptx export repeats (views/slides_pptx.py), the axis ticks and the labels.

const EXTRA_COLOURS = ['#10b981', '#ef4444', '#8b5cf6', '#64748b'];

/** Series colours: the slide accent, the theme's second accent, then a fixed set. */
export function seriesColours(t, accent) {
  return [accent || t.accent, t.accent2, ...EXTRA_COLOURS];
}

export const colourAt = (list, i) => list[i % list.length];

export const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

/** Round tick steps (1, 2, 2.5, 5 x 10^n) covering lo..hi in about `count` steps. */
export function niceTicks(lo, hi, count = 5) {
  if (!(hi > lo)) {
    const v = isNum(hi) ? hi : 0;
    return v === 0 ? [0, 1] : v > 0 ? [0, v] : [v, 0];
  }
  const raw = (hi - lo) / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || 10 * mag;
  const start = Math.floor(lo / step) * step;
  const end = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = start; v <= end + step / 2; v += step) ticks.push(Number(v.toFixed(10)));
  return ticks;
}

const TIGHT_UNITS = new Set(['%', '°', '‰']);
const fmt = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });
export function formatValue(v, unit = '') {
  if (!isNum(v)) return '';
  // Percent and degrees hug the number; a currency or a word sits after a space.
  return `${fmt.format(v)}${unit ? (TIGHT_UNITS.has(unit) ? unit : ` ${unit}`) : ''}`;
}
