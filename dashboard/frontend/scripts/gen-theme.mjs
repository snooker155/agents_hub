#!/usr/bin/env node
/**
 * Generates src/theme.css — the single source of truth for app colors.
 *
 *  1. Brand remap: indigo / violet / purple / fuchsia utilities are repainted
 *     with the magenta ramp, in BOTH themes (no purple anywhere).
 *  2. Dark theme: every Tailwind color utility used in the app gets a dark
 *     counterpart, so panels share one surface ladder and colored badges
 *     (green / amber / red …) stay readable instead of staying light-on-light.
 *
 * Selectors are wrapped in :where(html.dark) / :where(html) so they keep the
 * specificity of a plain utility class and only win by source order. That way
 * explicit `dark:` variants written in components still override them.
 *
 * Run:  node scripts/gen-theme.mjs
 */
import { readdirSync, readFileSync, writeFileSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = join(ROOT, 'src');

/* ── palettes ───────────────────────────────────────────────────────────── */

// Magenta brand ramp, 600 is the brand colour itself (#DB2777).
// Replaces indigo/violet/purple/fuchsia in light mode.
const BRAND_LIGHT = {
  50: '#fef2f9', 100: '#fce3f0', 200: '#f8c6e0', 300: '#f19cc7', 400: '#ea6ba9',
  500: '#e34292', 600: '#db2777', 700: '#ba2065', 800: '#991854', 900: '#761041',
  950: '#470628',
};
const BRAND_RGB = '219, 39, 119'; // #db2777

// Brand in dark mode: text tiers lighten, tint backgrounds become translucent.
const BRAND_DARK = {
  bg: {
    50: `rgba(${BRAND_RGB}, 0.16)`, 100: `rgba(${BRAND_RGB}, 0.26)`,
    200: `rgba(${BRAND_RGB}, 0.34)`, 300: '#b21e61', 400: '#b21e61',
    500: '#db2777', 600: '#c3226c', 700: '#9e195c', 800: '#7c1249',
    900: '#4a072c', 950: '#290219',
  },
  text: {
    300: '#fbacd5', 400: '#fba1ce', 500: '#fa91c5', 600: '#fa94c7',
    700: '#fbacd5', 800: '#fcc5e2', 900: '#fedbed', 950: '#ffebf5',
  },
  border: {
    50: `rgba(${BRAND_RGB}, 0.22)`, 100: `rgba(${BRAND_RGB}, 0.30)`,
    200: `rgba(${BRAND_RGB}, 0.40)`, 300: `rgba(${BRAND_RGB}, 0.52)`,
    400: '#e9479b', 500: '#db2777', 600: '#c3226c', 700: '#9e195c',
    800: '#7c1249', 900: '#4a072c',
  },
};

// Same ramp inverted, for inline styles that read var(--brand-N) directly.
const BRAND_DARK_VARS = {
  50: '#290219', 100: '#3b0424', 200: '#54093a', 300: '#9e195c', 400: '#c3226c',
  500: '#db2777', 600: '#e9479b', 700: '#fa94c7', 800: '#fcc5e2', 900: '#fedbed',
  950: '#ffebf5',
};

const BRAND_HUES = ['indigo', 'violet', 'purple', 'fuchsia'];
const NEUTRAL_HUES = ['gray', 'slate', 'zinc', 'neutral', 'stone'];

// The general-purpose neutral ramp exposed as --neutral-N (src/lib/palette.js
// reads and overwrites it, then mirrors it into --surface-*/--text-*/--border-*
// itself). Not consumed by the matrix below — the neutral *utility* classes
// (bg-gray-100 and friends) go through their own bg/text/border ladders,
// unchanged in light mode and NEUTRAL_DARK-derived in dark mode, same as
// before this ramp existed. It ships as Tailwind's neutral scale, the same
// grays tailwind.config.js puts behind the gray utilities, so a default
// install and a person who has never touched Settings → Account see the same
// grays either way. The magenta palette has no blue in its grays.
const NEUTRAL_LIGHT = {
  50: '#fafafa', 100: '#f5f5f5', 200: '#e5e5e5', 300: '#d4d4d4', 400: '#a3a3a3',
  500: '#737373', 600: '#525252', 700: '#404040', 800: '#262626', 900: '#171717',
  950: '#0a0a0a',
};

// Pure neutral dark ladder, no tint. Backgrounds go page < sunken < card < raised.
const NEUTRAL_DARK = {
  bg: {
    50: '#121212', 100: '#1e1e1e', 200: '#252525', 300: '#2c2c2c',
    400: '#424242', 500: '#555555', 600: '#666666', 700: '#1a1a1a',
    800: '#161616', 900: '#0d0d0d', 950: '#080808',
  },
  text: {
    300: '#9e9e9e', 400: '#9a9a9a', 500: '#a8a8a8', 600: '#c2c2c2',
    700: '#d6d6d6', 800: '#e6e6e6', 900: '#f2f2f2', 950: '#fafafa',
  },
  border: {
    50: '#1c1c1c', 100: '#222222', 200: '#2c2c2c', 300: '#383838',
    400: '#454545', 500: '#555555', 600: '#3b3b3b', 700: '#313131',
    800: '#272727', 900: '#1f1f1f', 950: '#171717',
  },
};

// Chromatic hues: 500 as rgb triple (for translucent tints) + light text tiers.
const HUES = {
  red:     { rgb: '239, 68, 68',   t400: '#f87171', t300: '#fca5a5' },
  orange:  { rgb: '249, 115, 22',  t400: '#fb923c', t300: '#fdba74' },
  amber:   { rgb: '245, 158, 11',  t400: '#fbbf24', t300: '#fcd34d' },
  yellow:  { rgb: '234, 179, 8',   t400: '#facc15', t300: '#fde047' },
  lime:    { rgb: '132, 204, 22',  t400: '#a3e635', t300: '#bef264' },
  green:   { rgb: '34, 197, 94',   t400: '#4ade80', t300: '#86efac' },
  emerald: { rgb: '16, 185, 129',  t400: '#34d399', t300: '#6ee7b7' },
  teal:    { rgb: '20, 184, 166',  t400: '#2dd4bf', t300: '#5eead4' },
  cyan:    { rgb: '6, 182, 212',   t400: '#22d3ee', t300: '#67e8f9' },
  sky:     { rgb: '14, 165, 233',  t400: '#38bdf8', t300: '#7dd3fc' },
  blue:    { rgb: '59, 130, 246',  t400: '#60a5fa', t300: '#93c5fd' },
  pink:    { rgb: '236, 72, 153',  t400: '#f472b6', t300: '#f9a8d4' },
  rose:    { rgb: '244, 63, 94',   t400: '#fb7185', t300: '#fda4af' },
};

const SHADES = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950];

/* ── mapping ──────────────────────────────────────────────────────────────
 *
 * Every mapper below has two faces: a *literal* one (returns a concrete hex /
 * rgba string, computed from the tables above) and a *var* one (returns a
 * `var(--token)` reference into the tokens block, so a palette applied at
 * runtime — src/lib/palette.js's applyPalette — repaints these utilities
 * without a rebuild). The literal face is still what defines the tokens'
 * *default* values and what a Tailwind opacity modifier (`bg-x-500/50`) is
 * computed from, since baking an arbitrary `/NN` blend into a `var()` chain
 * at build time isn't worth the extra custom property per shade it would
 * take; a `/NN` utility stays fixed to the shipped palette. Plain utilities
 * (no `/NN`) always go through the var face, which is what actually moves
 * when a person or a workspace picks a different palette. */

/** Light-mode literal value for a brand-hue utility (null = leave alone). */
function lightValue(prefix, hue, shade) {
  if (!BRAND_HUES.includes(hue)) return null;
  return BRAND_LIGHT[shade] ?? null;
}

/** Light-mode var() reference for the same utility, or null. */
function lightVarValue(prefix, hue, shade) {
  if (!BRAND_HUES.includes(hue)) return null;
  // `from`/`via`/`to` (gradient stops) fall back to the literal value: their
  // PROP handler computes a same-color-but-transparent stop with fade(), which
  // needs a real hex to compute from, not a var() reference.
  if (prefix === 'from' || prefix === 'via' || prefix === 'to') return null;
  return BRAND_LIGHT[shade] != null ? `var(--brand-${shade})` : null;
}

function _darkKind(prefix) {
  return (
    prefix === 'bg' ? 'bg' :
    prefix === 'text' || prefix === 'placeholder' || prefix === 'fill' || prefix === 'stroke' ? 'text' :
    prefix === 'border' || prefix === 'divide' || prefix === 'ring' || prefix === 'ring-offset' || prefix === 'outline' || prefix === 'decoration' ? 'border' :
    prefix === 'accent' || prefix === 'caret' ? 'bg' :
    prefix === 'from' || prefix === 'to' || prefix === 'via' ? 'bg' : null
  );
}

/** Dark-mode literal value for any color utility (null = leave alone). */
function darkValue(prefix, hue, shade) {
  const kind = _darkKind(prefix);
  if (!kind) return null;

  if (BRAND_HUES.includes(hue)) return BRAND_DARK[kind][shade] ?? null;

  if (NEUTRAL_HUES.includes(hue)) {
    if (kind === 'bg' && prefix === 'accent') return NEUTRAL_DARK.bg[shade] ?? null;
    return NEUTRAL_DARK[kind][shade] ?? null;
  }

  const h = HUES[hue];
  if (!h) return null;
  if (kind === 'bg') {
    // Tint backgrounds become translucent so they read on any dark surface;
    // solid mid/dark fills are already fine on a dark page.
    if (shade === 50) return `rgba(${h.rgb}, 0.14)`;
    if (shade === 100) return `rgba(${h.rgb}, 0.22)`;
    if (shade === 200) return `rgba(${h.rgb}, 0.30)`;
    return null;
  }
  if (kind === 'text') {
    // Dark-on-light label colors flip to the light tiers of the same hue.
    if (shade >= 700) return h.t300;
    if (shade === 600 || shade === 500) return h.t400;
    return null;
  }
  // borders / rings
  if (shade === 50) return `rgba(${h.rgb}, 0.18)`;
  if (shade === 100) return `rgba(${h.rgb}, 0.26)`;
  if (shade === 200) return `rgba(${h.rgb}, 0.36)`;
  if (shade === 300) return `rgba(${h.rgb}, 0.46)`;
  return null;
}

/** Dark-mode var() reference for the same utility, or null. Mirrors
 * darkValue()'s branches exactly, one level of indirection removed. */
function darkVarValue(prefix, hue, shade) {
  const kind = _darkKind(prefix);
  if (!kind) return null;
  // See lightVarValue(): gradient stops stay literal so fade() still has a
  // real hex to derive a matching transparent stop from.
  if (prefix === 'from' || prefix === 'via' || prefix === 'to') return null;

  if (BRAND_HUES.includes(hue)) {
    return BRAND_DARK[kind][shade] != null ? `var(--brand-${kind}-${shade})` : null;
  }

  if (NEUTRAL_HUES.includes(hue)) {
    const k = (kind === 'bg' && prefix === 'accent') ? 'bg' : kind;
    return NEUTRAL_DARK[k][shade] != null ? `var(--neutral-${k}-${shade})` : null;
  }

  const h = HUES[hue];
  if (!h) return null;
  const rgbVar = `var(--hue-${hue}-rgb)`;
  if (kind === 'bg') {
    if (shade === 50) return `rgba(${rgbVar}, 0.14)`;
    if (shade === 100) return `rgba(${rgbVar}, 0.22)`;
    if (shade === 200) return `rgba(${rgbVar}, 0.30)`;
    return null;
  }
  if (kind === 'text') {
    if (shade >= 700) return `var(--hue-${hue}-300)`;
    if (shade === 600 || shade === 500) return `var(--hue-${hue}-400)`;
    return null;
  }
  if (shade === 50) return `rgba(${rgbVar}, 0.18)`;
  if (shade === 100) return `rgba(${rgbVar}, 0.26)`;
  if (shade === 200) return `rgba(${rgbVar}, 0.36)`;
  if (shade === 300) return `rgba(${rgbVar}, 0.46)`;
  return null;
}

/* ── css emission ───────────────────────────────────────────────────────── */

const PROP = {
  bg: (v) => `background-color: ${v};`,
  text: (v) => `color: ${v};`,
  placeholder: (v) => `color: ${v};`,
  border: (v) => `border-color: ${v};`,
  divide: (v) => `border-color: ${v};`,
  ring: (v) => `--tw-ring-color: ${v};`,
  'ring-offset': (v) => `--tw-ring-offset-color: ${v};`,
  outline: (v) => `outline-color: ${v};`,
  decoration: (v) => `text-decoration-color: ${v};`,
  accent: (v) => `accent-color: ${v};`,
  caret: (v) => `caret-color: ${v};`,
  fill: (v) => `fill: ${v};`,
  stroke: (v) => `stroke: ${v};`,
  from: (v) => `--tw-gradient-from: ${v} var(--tw-gradient-from-position); --tw-gradient-to: ${fade(v)} var(--tw-gradient-to-position); --tw-gradient-stops: var(--tw-gradient-from), var(--tw-gradient-to);`,
  via: (v) => `--tw-gradient-to: ${fade(v)} var(--tw-gradient-to-position); --tw-gradient-stops: var(--tw-gradient-from), ${v} var(--tw-gradient-via-position), var(--tw-gradient-to);`,
  to: (v) => `--tw-gradient-to: ${v} var(--tw-gradient-to-position);`,
};

function fade(v) {
  const rgb = hexToRgb(v);
  return rgb ? `rgba(${rgb}, 0)` : 'transparent';
}

function hexToRgb(hex) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex);
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return `${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}`;
}

function withAlpha(value, pct) {
  const rgb = hexToRgb(value);
  if (rgb) return `rgba(${rgb}, ${(pct / 100).toFixed(2)})`;
  const m = /^rgba\(([^,]+,[^,]+,[^,]+),\s*([0-9.]+)\)$/.exec(value);
  if (m) return `rgba(${m[1]}, ${(Number(m[2]) * pct / 100).toFixed(3)})`;
  return value;
}

const VARIANTS = {
  hover: (s) => `${s}:hover`,
  focus: (s) => `${s}:focus`,
  'focus-within': (s) => `${s}:focus-within`,
  'focus-visible': (s) => `${s}:focus-visible`,
  active: (s) => `${s}:active`,
  disabled: (s) => `${s}:disabled`,
  checked: (s) => `${s}:checked`,
};

const esc = (cls) => '.' + cls.replace(/([:./[\]])/g, '\\$1');

/** Build the selector for a full token like `group-hover:hover:text-indigo-600`. */
function selectorFor(token, prefix) {
  const variants = token.split(':').slice(0, -1);
  let sel = esc(token);
  let ancestor = '';
  let darkOnly = false;
  for (const v of variants) {
    if (v === 'dark') { ancestor = '.dark ' + ancestor; darkOnly = true; }
    else if (v === 'group-hover') ancestor = '.group:hover ';
    else if (v === 'group-focus') ancestor = '.group:focus ';
    else if (v === 'peer-checked') ancestor = '.peer:checked ~ ';
    else if (VARIANTS[v]) sel = VARIANTS[v](sel);
    else return null; // unknown variant — skip rather than emit something wrong
  }
  let out = ancestor + sel;
  if (prefix === 'placeholder') out += '::placeholder';
  if (prefix === 'divide') out += ' > :not([hidden]) ~ :not([hidden])';
  return { sel: out, darkOnly };
}

/** Parse `hover:bg-gray-100/70` → {prefix, hue, shade, alpha}. */
const TOKEN_RE = new RegExp(
  `^((?:[a-z-]+:)*)(bg|text|border|ring-offset|ring|divide|placeholder|accent|caret|outline|decoration|fill|stroke|from|via|to)-(${[...NEUTRAL_HUES, ...BRAND_HUES, ...Object.keys(HUES)].join('|')})-(50|100|200|300|400|500|600|700|800|900|950)(?:/(\\d+))?$`
);

function parse(token) {
  const m = TOKEN_RE.exec(token);
  if (!m) return null;
  return { prefix: m[2], hue: m[3], shade: Number(m[4]), alpha: m[5] ? Number(m[5]) : null };
}

/* ── collect tokens used in the app ─────────────────────────────────────── */

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(jsx?|tsx?|html)$/.test(entry)) out.push(p);
  }
  return out;
}

const SCAN_RE = /(?:[a-z-]+:)*(?:bg|text|border|ring-offset|ring|divide|placeholder|accent|caret|outline|decoration|fill|stroke|from|via|to)-[a-z]+-\d{2,3}(?:\/\d+)?/g;

const used = new Set();
for (const file of walk(SRC)) {
  const text = readFileSync(file, 'utf8');
  for (const m of text.matchAll(SCAN_RE)) used.add(m[0]);
}

/* ── generate ───────────────────────────────────────────────────────────── */

const PREFIXES = ['bg', 'text', 'border', 'ring', 'divide', 'placeholder', 'accent', 'fill', 'stroke', 'from', 'via', 'to', 'caret', 'ring-offset', 'outline', 'decoration'];
const ALL_HUES = [...NEUTRAL_HUES, ...BRAND_HUES, ...Object.keys(HUES)];

// Base matrix (no variants) — covers utilities added later without regenerating.
const baseTokens = [];
for (const prefix of ['bg', 'text', 'border', 'ring', 'divide'])
  for (const hue of ALL_HUES)
    for (const shade of SHADES) baseTokens.push(`${prefix}-${hue}-${shade}`);

const tokens = new Set([...baseTokens, ...used]);

function emit(list, mapper, varMapper, label, isDark = false) {
  // Group selectors that end up with the same declaration — the generated
  // sheet is large and near-identical rules compress badly when repeated.
  const groups = new Map();
  for (const token of [...list].sort()) {
    const parsed = parse(token);
    if (!parsed) continue;
    const { prefix, hue, shade, alpha } = parsed;
    const literal = mapper(prefix, hue, shade);
    if (!literal) continue;
    // A plain utility (no `/NN`) goes through the var() face, so applyPalette
    // can repaint it at runtime; a `/NN` opacity modifier is baked from the
    // literal default, since re-theming that blend would need one more
    // custom property per shade for no real benefit (see the comment above
    // the mapper functions).
    const value = alpha != null ? withAlpha(literal, alpha) : (varMapper(prefix, hue, shade) || literal);
    const built = selectorFor(token, prefix);
    if (!built) continue;
    // A `dark:` utility already scopes itself with `.dark`, so it needs the
    // dark value but not a second `html.dark` ancestor.
    if (built.darkOnly && !isDark) continue;
    const decl = PROP[prefix](value);
    if (!groups.has(decl)) groups.set(decl, []);
    groups.get(decl).push((built.darkOnly ? ':where(html) ' : label) + built.sel);
  }
  const lines = [];
  for (const [decl, sels] of groups) {
    lines.push(`${sels.join(',\n')} { ${decl} }`);
  }
  return { lines, count: [...groups.values()].reduce((n, s) => n + s.length, 0) };
}

const light = emit(tokens, lightValue, lightVarValue, ':where(html) ');
const dark = emit(tokens, darkValue, darkVarValue, ':where(html.dark) ', true);


/* Hand-written pieces the matrix cannot express: white/black surfaces,
   elevation, and third-party widgets. */
const EXTRAS = `
/* ── white / black surfaces ───────────────────────────────────────────── */
:where(html.dark) .bg-white,
:where(html.dark) .hover\\:bg-white:hover,
:where(html.dark) .focus\\:bg-white:focus { background-color: var(--surface-card); }

:where(html.dark) .bg-white\\/95 { background-color: rgba(27, 27, 27, 0.95); }
:where(html.dark) .bg-white\\/80 { background-color: rgba(27, 27, 27, 0.80); }
:where(html.dark) .bg-white\\/70 { background-color: rgba(27, 27, 27, 0.70); }
:where(html.dark) .bg-white\\/60 { background-color: rgba(27, 27, 27, 0.60); }
:where(html.dark) .bg-white\\/20,
:where(html.dark) .hover\\:bg-white\\/20:hover { background-color: rgba(255, 255, 255, 0.12); }
:where(html.dark) .bg-white\\/10 { background-color: rgba(255, 255, 255, 0.07); }

:where(html.dark) .border-white { border-color: rgba(255, 255, 255, 0.14); }

/* Backdrops sit over an already-dark page, so they need to go deeper. */
:where(html.dark) .bg-black\\/40 { background-color: rgba(0, 0, 0, 0.65); }
:where(html.dark) .bg-black\\/45 { background-color: rgba(0, 0, 0, 0.68); }
:where(html.dark) .bg-black\\/50 { background-color: rgba(0, 0, 0, 0.72); }
:where(html.dark) .bg-black\\/60 { background-color: rgba(0, 0, 0, 0.78); }

/* ── elevation ────────────────────────────────────────────────────────── */
:where(html.dark) .shadow-sm  { box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.45); }
:where(html.dark) .shadow     { box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.5), 0 1px 2px -1px rgba(0, 0, 0, 0.5); }
:where(html.dark) .shadow-md  { box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.55), 0 2px 4px -2px rgba(0, 0, 0, 0.5); }
:where(html.dark) .shadow-lg  { box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.6), 0 4px 6px -4px rgba(0, 0, 0, 0.5); }
:where(html.dark) .shadow-xl  { box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.65), 0 8px 10px -6px rgba(0, 0, 0, 0.5); }
:where(html.dark) .shadow-2xl { box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.8); }

/* ── form controls ────────────────────────────────────────────────────── */
:where(html.dark) input:not([type="range"]):not([type="checkbox"]):not([type="radio"]):not([type="color"]),
:where(html.dark) select,
:where(html.dark) textarea {
  background-color: var(--surface-raised);
  color: var(--text-primary);
  border-color: var(--border-default);
}
/* A control drawn inside a styled wrapper (an icon plus an input in one
 * bordered box, a composer's textarea) declares bg-transparent so the wrapper
 * is the surface. Painting it above would show a second box inside the first,
 * so it keeps its transparency and only takes the text colour. The input
 * selector repeats the type exclusions above: without them that rule wins on
 * specificity and the field shows as a lighter patch inside its wrapper. */
:where(html.dark) input.bg-transparent:not([type="range"]):not([type="checkbox"]):not([type="radio"]):not([type="color"]),
:where(html.dark) select.bg-transparent,
:where(html.dark) textarea.bg-transparent {
  background-color: transparent;
  border-color: transparent;
}
:where(html.dark) input::placeholder,
:where(html.dark) textarea::placeholder { color: var(--text-faint); }
:where(html.dark) option { background-color: var(--surface-raised); color: var(--text-primary); }
`;

const header = `/* ============================================================================
 * GENERATED FILE — do not edit by hand.
 * Regenerate with:  node scripts/gen-theme.mjs
 *
 * What lives here:
 *   1. :root / html.dark design tokens (surfaces, text tiers, brand ramp).
 *   2. Brand remap  — indigo/violet/purple/fuchsia utilities repainted magenta.
 *   3. Dark theme   — a dark counterpart for every color utility, so panels
 *      sit on one surface ladder and colored badges stay readable.
 *
 * Selectors are wrapped in :where(...) so they carry plain-utility specificity
 * and win only by source order; explicit \`dark:\` variants still override them.
 * ========================================================================== */
`;

const tokensBlock = `
:root {
  color-scheme: light;

  --surface-page:    #f4f4f4;
  --surface-sunken:  #fafafa;
  --surface-card:    #ffffff;
  --surface-raised:  #f2f2f2;
  --surface-hover:   #ededed;
  --surface-inverse: #1b1b1b;

  --border-subtle:   #ebebeb;
  --border-default:  #e0e0e0;
  --border-strong:   #cccccc;

  --text-primary:    #1b1b1b;
  --text-secondary:  #3d3d3d;
  --text-muted:      #6b6b6b;
  --text-faint:      #9b9b9b;
  --text-inverse:    #fafafa;

${SHADES.map((s) => `  --brand-${s}: ${BRAND_LIGHT[s]};`).join('\n')}
  --brand:           ${BRAND_LIGHT[600]};
  --brand-contrast:  #ffffff;
  --brand-rgb:       ${BRAND_RGB};

${SHADES.map((s) => `  --neutral-${s}: ${NEUTRAL_LIGHT[s]};`).join('\n')}

${Object.entries(HUES).map(([name, h]) => `  --hue-${name}-rgb: ${h.rgb};\n  --hue-${name}-400: ${h.t400};\n  --hue-${name}-300: ${h.t300};`).join('\n')}

  --ok:      #15803d;
  --warn:    #b45309;
  --danger:  #b91c1c;
  --info:    ${BRAND_LIGHT[600]};

  --ok-surface:     #dcfce7;
  --warn-surface:   #fef08a;
  --danger-surface: #fee2e2;
  --info-surface:   ${BRAND_LIGHT[100]};

  /* Chart marks (validated with the dataviz palette checks for this surface):
     the accent series, the de-emphasised rest, three categorical slots,
     gridlines and the unfilled track of a meter. */
  --chart-accent:   #db2777;
  --chart-muted:    #d4d4d4;
  --chart-cat-1:    #db2777;
  --chart-cat-2:    #2f6fd0;
  --chart-cat-3:    #1baf7a;
  --chart-grid:     #ebebeb;
  --chart-track:    #fce3f0;
}

html.dark {
  color-scheme: dark;

  --surface-page:    #0a0a0a;
  --surface-sunken:  #121212;
  --surface-card:    #1b1b1b;
  --surface-raised:  #232323;
  --surface-hover:   #2a2a2a;
  --surface-inverse: #f2f2f2;

  --border-subtle:   #262626;
  --border-default:  #2f2f2f;
  --border-strong:   #3d3d3d;

  --text-primary:    #f2f2f2;
  --text-secondary:  #d6d6d6;
  --text-muted:      #a8a8a8;
  --text-faint:      #9a9a9a;
  --text-inverse:    #1b1b1b;

  --chart-accent:   #e9479b;
  --chart-muted:    #4a4a4a;
  --chart-cat-1:    #e9479b;
  --chart-cat-2:    #5b9bf0;
  --chart-cat-3:    #199e70;
  --chart-grid:     #262626;
  --chart-track:    #54093a;

${Object.entries(BRAND_DARK_VARS).map(([s, v]) => `  --brand-${s}: ${v};`).join('\n')}
  --brand:           #db2777;
  --brand-contrast:  #ffffff;
  --brand-rgb:       ${BRAND_RGB};

  /* The three brand/neutral roles the dark-theme matrix below paints with:
     bg (tint fills), text (labels on a dark page) and border (rings, dividers).
     Defaults are exactly BRAND_DARK / NEUTRAL_DARK above — this just gives
     each entry a custom property so applyPalette() can move it at runtime. */
${SHADES.filter((s) => BRAND_DARK.bg[s] != null).map((s) => `  --brand-bg-${s}: ${BRAND_DARK.bg[s]};`).join('\n')}
${[300, 400, 500, 600, 700, 800, 900, 950].map((s) => `  --brand-text-${s}: ${BRAND_DARK.text[s]};`).join('\n')}
${[50, 100, 200, 300, 400, 500, 600, 700, 800, 900].map((s) => `  --brand-border-${s}: ${BRAND_DARK.border[s]};`).join('\n')}

${SHADES.map((s) => `  --neutral-bg-${s}: ${NEUTRAL_DARK.bg[s]};`).join('\n')}
${[300, 400, 500, 600, 700, 800, 900, 950].map((s) => `  --neutral-text-${s}: ${NEUTRAL_DARK.text[s]};`).join('\n')}
${SHADES.filter((s) => NEUTRAL_DARK.border[s] != null).map((s) => `  --neutral-border-${s}: ${NEUTRAL_DARK.border[s]};`).join('\n')}

${SHADES.map((s) => `  --neutral-${s}: ${NEUTRAL_LIGHT[s]};`).join('\n')}

${Object.entries(HUES).map(([name, h]) => `  --hue-${name}-rgb: ${h.rgb};\n  --hue-${name}-400: ${h.t400};\n  --hue-${name}-300: ${h.t300};`).join('\n')}

  --ok:      #6ee7b7;
  --warn:    #fcd34d;
  --danger:  #fca5a5;
  --info:    #fa94c7;

  --ok-surface:     rgba(34, 197, 94, 0.24);
  --warn-surface:   rgba(234, 179, 8, 0.28);
  --danger-surface: rgba(239, 68, 68, 0.24);
  --info-surface:   rgba(219, 39, 119, 0.28);
}
`;


/* ── 4. magenta style ──────────────────────────────────────────────────────
 * The workspace palette preset "magenta" (src/lib/palette.js) puts
 * `style-magenta` on <html> instead of computing ramps. Light: magenta
 * buttons on a white page. Dark: the shipped dark theme, a dark page with
 * magenta fills, so nothing is overridden there. */
const W = (a) => `rgba(255, 255, 255, ${a})`;
const varLines = (prefix, obj) => Object.entries(obj).map(([k, v]) => `  --${prefix}-${k}: ${v};`).join('\n');
const MAGENTA_STYLE = `
/* ── 4. magenta style (palette preset "magenta") ─────────────────────── */
html.style-magenta:not(.dark) {
  --surface-page:    #ffffff;
  --surface-sunken:  #fafafa;
  --surface-raised:  #f5f5f5;
  --surface-hover:   #f0f0f0;
  --border-subtle:   #ececec;
  --border-default:  #e0e0e0;
}
`;

/* ── 5. neutral style ──────────────────────────────────────────────────────
 * The palette preset "neutral" puts `style-neutral` on <html>: the same
 * neutral surfaces with no brand color at all. Light: graphite buttons and
 * accents. Dark: light buttons with dark labels. */
const NEUTRAL_STYLE_DARK = {
  brandVars: { 50: '#171717', 100: '#262626', 200: '#404040', 300: '#737373', 400: '#a3a3a3',
    500: '#d4d4d4', 600: '#e5e5e5', 700: '#f5f5f5', 800: '#fafafa', 900: '#fafafa', 950: '#ffffff' },
  brandBg: { 50: W(0.08), 100: W(0.12), 200: W(0.18), 300: '#a3a3a3', 400: '#d4d4d4', 500: '#e5e5e5',
    600: '#f5f5f5', 700: '#d4d4d4', 800: '#a3a3a3', 900: W(0.20), 950: W(0.28) },
  brandText: { 300: '#d4d4d4', 400: '#e5e5e5', 500: '#f5f5f5', 600: '#f5f5f5', 700: '#fafafa',
    800: '#fafafa', 900: '#ffffff', 950: '#ffffff' },
  brandBorder: { 50: W(0.14), 100: W(0.20), 200: W(0.28), 300: W(0.40), 400: '#d4d4d4', 500: '#e5e5e5',
    600: '#e5e5e5', 700: '#e5e5e5', 800: '#e5e5e5', 900: '#e5e5e5' },
};
const NEUTRAL_LIGHT_BRAND = { 50: '#fafafa', 100: '#f0f0f0', 200: '#e5e5e5', 300: '#d4d4d4', 400: '#a3a3a3',
  500: '#525252', 600: '#262626', 700: '#171717', 800: '#0a0a0a', 900: '#0a0a0a', 950: '#000000' };
const neutralButtonSelectors = [];
for (const hue of BRAND_HUES) {
  for (const shade of [500, 600, 700, 800]) {
    neutralButtonSelectors.push(`:where(html.style-neutral.dark) .bg-${hue}-${shade}.text-white`);
  }
}
const NEUTRAL_STYLE = `
/* ── 5. neutral style (palette preset "neutral") ─────────────────────── */
html.style-neutral {
${varLines('brand', NEUTRAL_LIGHT_BRAND)}
  --brand:           #262626;
  --brand-contrast:  #ffffff;
  --brand-rgb:       38, 38, 38;
  --info:            #262626;
  --info-surface:    #f0f0f0;
  --chart-accent:    #262626;
  --chart-cat-1:     #262626;
  --chart-track:     #e5e5e5;
}

html.style-neutral.dark {
${varLines('brand', NEUTRAL_STYLE_DARK.brandVars)}
  --brand:           #f5f5f5;
  --brand-contrast:  #171717;
  --brand-rgb:       245, 245, 245;
${varLines('brand-bg', NEUTRAL_STYLE_DARK.brandBg)}
${varLines('brand-text', NEUTRAL_STYLE_DARK.brandText)}
${varLines('brand-border', NEUTRAL_STYLE_DARK.brandBorder)}
  --info:            #f5f5f5;
  --info-surface:    ${W(0.12)};
  --chart-accent:    #f5f5f5;
  --chart-cat-1:     #f5f5f5;
  --chart-track:     ${W(0.16)};
}

/* A brand fill is light here, so the label on it turns dark. */
${neutralButtonSelectors.join(',\n')} { color: var(--brand-contrast); }
`;

const out = [
  header,
  tokensBlock,
  '\n/* ── 2. brand remap (both themes) ─────────────────────────────────────── */\n',
  light.lines.join('\n'),
  '\n\n/* ── 3. dark theme ────────────────────────────────────────────────────── */\n',
  dark.lines.join('\n'),
  EXTRAS,
  MAGENTA_STYLE,
  NEUTRAL_STYLE,
  '\n',
].join('\n');

writeFileSync(join(SRC, 'theme.css'), out);
console.log(`theme.css: ${light.count} brand utilities, ${dark.count} dark utilities, ${used.size} tokens found in src/`);
