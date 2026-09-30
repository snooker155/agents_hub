/**
 * A user or workspace palette: 2 to 4 base colors (brand, neutral tint,
 * success accent, danger accent) turned into everything the app actually
 * paints with — an 11-shade ramp per color, the semantic surface/text/border
 * tokens, and the handful of custom properties gen-theme.mjs's generated
 * dark-theme utility matrix reads (see scripts/gen-theme.mjs, src/theme.css).
 *
 * The math is a hand-written sRGB <-> OKLab <-> OKLCH conversion (Björn
 * Ottosson's OKLab, no dependency): a ramp is built by keeping a color's own
 * hue and chroma but walking lightness through a fixed set of steps, one per
 * shade, the same steps in light and dark mode. That fixed-lightness rule is
 * what keeps contrast predictable: shade 900 is always dark and shade 50 is
 * always pale, in either theme, regardless of which color was picked.
 *
 * `applyPalette` / `clearPalette` are the only functions that touch the DOM;
 * everything else is pure and safe to call from a test or from the palette
 * editor's live preview without side effects.
 */

// ── sRGB <-> linear <-> OKLab <-> OKLCH ─────────────────────────────────────

function srgbToLinear(c) {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function linearToSrgb(c) {
  const v = c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(Math.max(c, 0), 1 / 2.4) - 0.055;
  return v;
}

/** '#rrggbb' -> [r, g, b] each 0..1. Accepts 3-digit hex too. Returns null on garbage. */
export function hexToRgb01(hex) {
  const s = (hex || '').trim();
  const m6 = /^#?([0-9a-fA-F]{6})$/.exec(s);
  const m3 = /^#?([0-9a-fA-F]{3})$/.exec(s);
  let hex6 = null;
  if (m6) hex6 = m6[1];
  else if (m3) hex6 = m3[1].split('').map((c) => c + c).join('');
  if (!hex6) return null;
  const n = parseInt(hex6, 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

/** [r, g, b] each 0..1 -> '#rrggbb', clamped. */
export function rgb01ToHex([r, g, b]) {
  const byte = (v) => Math.max(0, Math.min(255, Math.round(v * 255)));
  const hex = (v) => byte(v).toString(16).padStart(2, '0');
  return `#${hex(r)}${hex(g)}${hex(b)}`;
}

/** [r, g, b] each 0..1 -> the "R, G, B" text a CSS rgb triple var carries. */
export function rgb01ToTriple([r, g, b]) {
  const byte = (v) => Math.max(0, Math.min(255, Math.round(v * 255)));
  return `${byte(r)}, ${byte(g)}, ${byte(b)}`;
}

function linearSrgbToOklab([r, g, b]) {
  const l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b;
  const m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b;
  const s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b;
  const l_ = Math.cbrt(l);
  const m_ = Math.cbrt(m);
  const s_ = Math.cbrt(s);
  return [
    0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
    1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
    0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
  ];
}

function oklabToLinearSrgb([L, a, b]) {
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.2914855480 * b;
  const l = l_ * l_ * l_;
  const m = m_ * m_ * m_;
  const s = s_ * s_ * s_;
  return [
    +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
  ];
}

function oklabToOklch([L, a, b]) {
  const C = Math.sqrt(a * a + b * b);
  let H = Math.atan2(b, a) * (180 / Math.PI);
  if (H < 0) H += 360;
  return [L, C, H];
}

function oklchToOklab([L, C, H]) {
  const rad = (H * Math.PI) / 180;
  return [L, C * Math.cos(rad), C * Math.sin(rad)];
}

/** '#rrggbb' -> [L, C, H] (L, C in OKLab units, H in degrees). Null on garbage. */
export function hexToOklch(hex) {
  const rgb = hexToRgb01(hex);
  if (!rgb) return null;
  const linear = rgb.map(srgbToLinear);
  return oklabToOklch(linearSrgbToOklab(linear));
}

/** [L, C, H] -> '#rrggbb', reducing chroma until the color is back in the sRGB
 * gamut rather than clipping channels (keeps the hue instead of skewing it). */
export function oklchToHex([L, C, H]) {
  let c = C;
  for (let i = 0; i < 24; i += 1) {
    const linear = oklabToLinearSrgb(oklchToOklab([L, c, H]));
    if (linear.every((v) => v >= -0.0005 && v <= 1.0005)) {
      return rgb01ToHex(linear.map(linearToSrgb));
    }
    c *= 0.92;
  }
  const linear = oklabToLinearSrgb(oklchToOklab([L, 0, H]));
  return rgb01ToHex(linear.map(linearToSrgb));
}

// ── ramps ────────────────────────────────────────────────────────────────

export const SHADES = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];

// Fixed per-shade lightness, the same in both themes: this is the whole
// contrast guarantee. 50 is always pale, 950 is always near-black, whatever
// hue and chroma the base color carries.
const LIGHTNESS_STEPS = {
  50: 0.97, 100: 0.93, 200: 0.86, 300: 0.77, 400: 0.68, 500: 0.60,
  600: 0.51, 700: 0.42, 800: 0.34, 900: 0.27, 950: 0.20,
};

// Chroma tapers off at the extremes (a very pale or very dark shade with full
// chroma looks muddy or neon); dark mode gets a mild boost since a dark page
// desaturates perception.
const CHROMA_SCALE = {
  50: 0.30, 100: 0.45, 200: 0.65, 300: 0.85, 400: 0.95, 500: 1,
  600: 0.95, 700: 0.85, 800: 0.72, 900: 0.60, 950: 0.45,
};
const DARK_CHROMA_BOOST = 1.08;

/**
 * The 11-shade ramp (keys 50..950, hex values) for one base color.
 * `mode` is 'light' or 'dark': same lightness at every shade either way, a
 * little more chroma in 'dark' so the ramp stays punchy on a dark page.
 */
export function rampFromColor(hex, { mode = 'light' } = {}) {
  const oklch = hexToOklch(hex);
  const ramp = {};
  if (!oklch) {
    SHADES.forEach((s) => { ramp[s] = hex; });
    return ramp;
  }
  const [, baseC, H] = oklch;
  const boost = mode === 'dark' ? DARK_CHROMA_BOOST : 1;
  for (const shade of SHADES) {
    const L = LIGHTNESS_STEPS[shade];
    const C = baseC * CHROMA_SCALE[shade] * boost;
    ramp[shade] = oklchToHex([L, C, H]);
  }
  return ramp;
}

// ── the three dark-mode "roles" (bg / text / border) a ramp is split into
//    for the generated utility matrix (scripts/gen-theme.mjs's darkVarValue),
//    the same shape as its hand-tuned BRAND_DARK / NEUTRAL_DARK defaults. ──

const BG_TRANSLUCENT_ALPHA = { 50: 0.16, 100: 0.26, 200: 0.34 };
const BORDER_TRANSLUCENT_ALPHA = { 50: 0.22, 100: 0.30, 200: 0.40, 300: 0.52 };
// text[N] reads from a lighter shade of the same ramp as N grows, mirroring
// the shipped tables (dark-mode text gets paler, not darker, as its "weight"
// goes up): 300 borrows the ramp's 700, 950 borrows its 100.
const TEXT_SOURCE_SHADE = { 300: 700, 400: 600, 500: 500, 600: 500, 700: 400, 800: 300, 900: 200, 950: 100 };

function deriveDarkRoles(hex) {
  const ramp = rampFromColor(hex, { mode: 'dark' });
  const triple = rgb01ToTriple(hexToRgb01(ramp[600]) || [0, 0, 0]);
  const bg = {};
  for (const shade of SHADES) {
    bg[shade] = shade in BG_TRANSLUCENT_ALPHA
      ? `rgba(${triple}, ${BG_TRANSLUCENT_ALPHA[shade]})`
      : ramp[shade];
  }
  const text = {};
  for (const shade of Object.keys(TEXT_SOURCE_SHADE)) {
    text[shade] = ramp[TEXT_SOURCE_SHADE[shade]];
  }
  const border = {};
  for (const shade of SHADES) {
    if (shade === 950) continue; // BRAND_DARK.border has no 950; keep both roles the same shape
    border[shade] = shade in BORDER_TRANSLUCENT_ALPHA
      ? `rgba(${triple}, ${BORDER_TRANSLUCENT_ALPHA[shade]})`
      : ramp[shade];
  }
  return { bg, text, border };
}

// ── mirroring the neutral tint into the semantic tokens ────────────────────

// Which shade of the neutral ramp feeds each semantic token, by mode. Chosen
// to land on the same shade the shipped literal defaults already sit at
// (NEUTRAL_LIGHT is Tailwind's slate scale, and the default --text-primary
// etc. are exactly slate-900, slate-700, ... — see scripts/gen-theme.mjs).
const LIGHT_TOKEN_SHADE = {
  '--surface-page': 50, '--surface-sunken': 50, '--surface-raised': 100,
  '--surface-hover': 100, '--surface-inverse': 900,
  '--text-primary': 900, '--text-secondary': 700, '--text-muted': 500,
  '--text-faint': 400, '--text-inverse': 50,
  '--border-subtle': 50, '--border-default': 200, '--border-strong': 300,
};
const DARK_TOKEN_SHADE = {
  '--surface-page': 950, '--surface-sunken': 900, '--surface-card': 800,
  '--surface-raised': 700, '--surface-hover': 600, '--surface-inverse': 50,
  '--text-primary': 50, '--text-secondary': 200, '--text-muted': 400,
  '--text-faint': 500, '--text-inverse': 900,
  '--border-subtle': 800, '--border-default': 700, '--border-strong': 600,
};

function surfaceTokensFromNeutral(hex, mode) {
  const ramp = rampFromColor(hex, { mode });
  const map = mode === 'dark' ? DARK_TOKEN_SHADE : LIGHT_TOKEN_SHADE;
  const tokens = {};
  for (const [name, shade] of Object.entries(map)) tokens[name] = ramp[shade];
  // Light mode keeps a true white card: only the tint around it moves.
  if (mode !== 'dark') tokens['--surface-card'] = '#ffffff';
  return tokens;
}

// ── ok / danger accents: retarget the green / red hue the app already uses
//    for success and danger states (see HUES in scripts/gen-theme.mjs). ────

function hueTokensFromAccent(hex, mode) {
  const ramp = rampFromColor(hex, { mode: 'dark' });
  const currentRamp = rampFromColor(hex, { mode });
  const triple = rgb01ToTriple(hexToRgb01(ramp[500]) || [0, 0, 0]);
  return {
    rgb: triple,
    t400: ramp[300],
    t300: ramp[200],
    semantic: currentRamp[mode === 'dark' ? 300 : 700],
    surfaceSolid: currentRamp[100],
  };
}

// ── presets ──────────────────────────────────────────────────────────────

// Four starting points plus "Custom" (any set of colors that isn't one of
// these, including a partial one — the palette editor decides that by
// comparison, this module just ships the four). Navy matches the shipped
// default almost exactly, so picking it back after customizing is a safe
// "undo".
export const PRESET_ORDER = ['navy', 'forest', 'slate', 'sunset'];
export const PRESETS = {
  navy: { brand: '#3f66d8', neutral: '#64748b', ok: '#16a34a', danger: '#dc2626' },
  forest: { brand: '#166534', neutral: '#57534e', ok: '#65a30d', danger: '#b91c1c' },
  slate: { brand: '#334155', neutral: '#64748b', ok: '#059669', danger: '#e11d48' },
  sunset: { brand: '#c2410c', neutral: '#78716c', ok: '#16a34a', danger: '#be123c' },
};

/** Whether `palette` matches one of PRESETS exactly (all four keys, same
 * hex, case-insensitively) — 'custom' when it does not, or when it's empty. */
export function matchPreset(palette) {
  if (!palette) return null;
  const keys = ['brand', 'neutral', 'ok', 'danger'];
  for (const name of PRESET_ORDER) {
    const preset = PRESETS[name];
    if (keys.every((k) => (palette[k] || '').toLowerCase() === preset[k].toLowerCase())) {
      return name;
    }
  }
  return null;
}

// ── applying / clearing ─────────────────────────────────────────────────────

export const PALETTE_EVENT = 'agents-hub-palette';

function setVar(root, name, value) {
  if (value == null) root.style.removeProperty(name);
  else root.style.setProperty(name, value);
}

/** Every custom property applyPalette() may set — clearPalette() removes
 * exactly these, so an unmentioned base color's override never lingers. */
function allPropertyNames() {
  const names = [];
  for (const s of SHADES) names.push(`--brand-${s}`, `--neutral-${s}`);
  names.push('--brand', '--brand-rgb');
  for (const s of SHADES) names.push(`--brand-bg-${s}`, `--neutral-bg-${s}`);
  for (const s of Object.keys(TEXT_SOURCE_SHADE)) names.push(`--brand-text-${s}`, `--neutral-text-${s}`);
  for (const s of SHADES) if (s !== 950) names.push(`--brand-border-${s}`, `--neutral-border-${s}`);
  for (const name of Object.keys(LIGHT_TOKEN_SHADE)) names.push(name);
  names.push('--surface-card');
  for (const hue of ['green', 'red']) names.push(`--hue-${hue}-rgb`, `--hue-${hue}-400`, `--hue-${hue}-300`);
  names.push('--ok', '--ok-surface', '--danger', '--danger-surface');
  return names;
}

/**
 * Set every custom property a `{brand, neutral, ok, danger}` palette
 * computes to, for the given `mode` ('light' | 'dark'). Missing keys are
 * left alone (their built-in default keeps showing). Dispatches
 * `PALETTE_EVENT` on `window` so anything reading colors through
 * `src/lib/themeColors.js` re-reads them.
 */
export function applyPalette(palette, mode = 'light') {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  const { brand, neutral, ok, danger } = palette || {};

  if (brand) {
    const ramp = rampFromColor(brand, { mode });
    for (const s of SHADES) setVar(root, `--brand-${s}`, ramp[s]);
    const accentShade = mode === 'dark' ? 500 : 600;
    setVar(root, '--brand', ramp[accentShade]);
    setVar(root, '--brand-rgb', rgb01ToTriple(hexToRgb01(ramp[accentShade]) || [0, 0, 0]));
    const roles = deriveDarkRoles(brand);
    for (const s of SHADES) setVar(root, `--brand-bg-${s}`, roles.bg[s]);
    for (const s of Object.keys(TEXT_SOURCE_SHADE)) setVar(root, `--brand-text-${s}`, roles.text[s]);
    for (const s of SHADES) if (s !== 950) setVar(root, `--brand-border-${s}`, roles.border[s]);
  }

  if (neutral) {
    const ramp = rampFromColor(neutral, { mode });
    for (const s of SHADES) setVar(root, `--neutral-${s}`, ramp[s]);
    const roles = deriveDarkRoles(neutral);
    for (const s of SHADES) setVar(root, `--neutral-bg-${s}`, roles.bg[s]);
    for (const s of Object.keys(TEXT_SOURCE_SHADE)) setVar(root, `--neutral-text-${s}`, roles.text[s]);
    for (const s of SHADES) if (s !== 950) setVar(root, `--neutral-border-${s}`, roles.border[s]);
    const tokens = surfaceTokensFromNeutral(neutral, mode);
    for (const [name, value] of Object.entries(tokens)) setVar(root, name, value);
  }

  if (ok) {
    const h = hueTokensFromAccent(ok, mode);
    setVar(root, '--hue-green-rgb', h.rgb);
    setVar(root, '--hue-green-400', h.t400);
    setVar(root, '--hue-green-300', h.t300);
    setVar(root, '--ok', h.semantic);
    setVar(root, '--ok-surface', mode === 'dark' ? `rgba(${h.rgb}, 0.24)` : h.surfaceSolid);
  }

  if (danger) {
    const h = hueTokensFromAccent(danger, mode);
    setVar(root, '--hue-red-rgb', h.rgb);
    setVar(root, '--hue-red-400', h.t400);
    setVar(root, '--hue-red-300', h.t300);
    setVar(root, '--danger', h.semantic);
    setVar(root, '--danger-surface', mode === 'dark' ? `rgba(${h.rgb}, 0.24)` : h.surfaceSolid);
  }

  try {
    window.dispatchEvent(new CustomEvent(PALETTE_EVENT));
  } catch {
    // A test DOM without CustomEvent support: harmless, nothing reacts.
  }
}

/** Remove every property applyPalette() might have set, so the generated
 * defaults in src/theme.css show through again. */
export function clearPalette() {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  for (const name of allPropertyNames()) root.style.removeProperty(name);
  try {
    window.dispatchEvent(new CustomEvent(PALETTE_EVENT));
  } catch {
    // See applyPalette().
  }
}

// ── contrast (WCAG 2.x) ──────────────────────────────────────────────────

function relativeLuminance(hex) {
  const rgb = hexToRgb01(hex);
  if (!rgb) return 0;
  const [r, g, b] = rgb.map(srgbToLinear);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG 2.x contrast ratio between two colors, from 1 (no contrast) to 21. */
export function contrastRatio(hexA, hexB) {
  const l1 = relativeLuminance(hexA);
  const l2 = relativeLuminance(hexB);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}

const DEFAULT_SURFACE = {
  light: { page: '#f4f6fb', text: '#0f172a' },
  dark: { page: '#070d18', text: '#eef2fa' },
};

/**
 * Warnings for any of the palette's load-bearing color pairs that fall
 * under WCAG AA's 4.5:1: the picked brand color read as text or as a
 * button fill, and body text against the page surface. Returns `[]` when
 * everything clears the bar.
 *
 * The brand pair checks the color a person actually typed, not the
 * ramp-corrected `--brand-600` it turns into: `rampFromColor`'s fixed
 * lightness step for 600 already keeps *that* safe on its own (by design —
 * see its own docstring), so checking the ramp output would never fire and
 * the warning would go stale. Checking the raw pick instead tells someone
 * who chose a very pale or very light color that their choice is risky,
 * before it is saved, the same moment a ramp's worth of auto-correction
 * would otherwise have hidden the problem from them.
 */
export function checkPalette(palette, mode = 'light') {
  const { brand, neutral } = palette || {};
  const surface = neutral
    ? surfaceTokensFromNeutral(neutral, mode)
    : null;
  const page = surface ? surface['--surface-page'] : DEFAULT_SURFACE[mode].page;
  const text = surface ? surface['--text-primary'] : DEFAULT_SURFACE[mode].text;

  const pairs = [];
  if (brand && hexToRgb01(brand)) {
    pairs.push({ label: 'brand-600 on white', a: brand, b: '#ffffff' });
    pairs.push({ label: 'white on brand-600', a: '#ffffff', b: brand });
  }
  pairs.push({ label: 'text on page surface', a: text, b: page });

  return pairs
    .map((pair) => ({ ...pair, ratio: contrastRatio(pair.a, pair.b) }))
    .filter((pair) => pair.ratio < 4.5);
}
