import THEMES from '../slideThemes.json';

// The stage the .pptx export draws on (views/slides_pptx.py) and the palette
// it paints with (views/slide_themes.json, copied here as slideThemes.json and
// kept equal by a backend test).

export const STAGE_W = 1280;
export const STAGE_H = 720;
export const FONT = "'Helvetica Neue', Arial, Helvetica, sans-serif";

export function deckTheme(spec) {
  const t = { ...(THEMES[spec?.theme] || THEMES.light) };
  if (spec?.accent) t.accent = spec.accent;
  return t;
}

export function orderedSlides(coll) {
  const list = Array.isArray(coll)
    ? coll.map((s, i) => ({ ...(s || {}), id: s?.id ?? String(i) }))
    : Object.entries(coll || {}).map(([id, s]) => ({ ...(s || {}), id }));
  return list.sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
}
