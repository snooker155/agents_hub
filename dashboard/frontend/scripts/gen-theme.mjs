#!/usr/bin/env node
/**
 * Generates src/theme.css — the single source of truth for app colors.
 *
 *  1. Brand remap: indigo / violet / purple / fuchsia utilities are repainted
 *     with a deep-navy ramp, in BOTH themes (no purple anywhere).
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

// Deep navy brand ramp — replaces indigo/violet/purple/fuchsia in light mode.
const BRAND_LIGHT = {
  50: '#eef3ff', 100: '#dbe6ff', 200: '#bdd0fb', 300: '#93b0f5', 400: '#6389ea',
  500: '#3f66d8', 600: '#2a4fbd', 700: '#21409c', 800: '#1d3680', 900: '#1a2c63',
  950: '#101c40',
};
const BRAND_RGB = '63, 102, 216'; // #3f66d8

// Brand in dark mode: text tiers lighten, tint backgrounds become translucent.
const BRAND_DARK = {
  bg: {
    50: `rgba(${BRAND_RGB}, 0.16)`, 100: `rgba(${BRAND_RGB}, 0.26)`,
    200: `rgba(${BRAND_RGB}, 0.34)`, 300: '#3a5cc4', 400: '#3a5cc4',
    500: '#3f66d8', 600: '#3159cc', 700: '#264aa8', 800: '#1e3c85',
    900: '#16294f', 950: '#0c1730',
  },
  text: {
    300: '#a8c4ff', 400: '#9dbaff', 500: '#8cb0ff', 600: '#8fb2ff',
    700: '#a8c4ff', 800: '#bcd2ff', 900: '#d2e0ff', 950: '#e4ecff',
  },
  border: {
    50: `rgba(${BRAND_RGB}, 0.22)`, 100: `rgba(${BRAND_RGB}, 0.30)`,
    200: `rgba(${BRAND_RGB}, 0.40)`, 300: `rgba(${BRAND_RGB}, 0.52)`,
    400: '#4a72e6', 500: '#3f66d8', 600: '#3159cc', 700: '#264aa8',
    800: '#1e3c85', 900: '#16294f',
  },
};

// Same ramp inverted, for inline styles that read var(--brand-N) directly.
const BRAND_DARK_VARS = {
  50: '#0c1730', 100: '#12224a', 200: '#1a3068', 300: '#264aa8', 400: '#3159cc',
  500: '#3f66d8', 600: '#5b82e6', 700: '#8fb2ff', 800: '#bcd2ff', 900: '#d2e0ff',
  950: '#e4ecff',
};

const BRAND_HUES = ['indigo', 'violet', 'purple', 'fuchsia'];
const NEUTRAL_HUES = ['gray', 'slate', 'zinc', 'neutral', 'stone'];

// Navy-tinted neutral ladder. Backgrounds go page < sunken < card < raised.
const NEUTRAL_DARK = {
  bg: {
    50: '#0c1322', 100: '#18223a', 200: '#1e2943', 300: '#25314c',
    400: '#3a4867', 500: '#4c5b7c', 600: '#5d6c8d', 700: '#16203a',
    800: '#101828', 900: '#080f1c', 950: '#050a14',
  },
  text: {
    300: '#8e9bb1', 400: '#8b98ae', 500: '#9aa7bd', 600: '#b6c1d4',
    700: '#ccd5e3', 800: '#dfe6f0', 900: '#eef2fa', 950: '#f8fafc',
  },
  border: {
    50: '#151e33', 100: '#1a2440', 200: '#25314c', 300: '#31405e',
    400: '#3d4d6e', 500: '#4a5a7d', 600: '#33415f', 700: '#2a3654',
    800: '#202b45', 900: '#18213a', 950: '#111a2e',
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

/* ── mapping ────────────────────────────────────────────────────────────── */

/** Light-mode value for a brand-hue utility (null = leave alone). */
function lightValue(prefix, hue, shade) {
  if (!BRAND_HUES.includes(hue)) return null;
  return BRAND_LIGHT[shade] ?? null;
}

/** Dark-mode value for any color utility (null = leave alone). */
function darkValue(prefix, hue, shade) {
  const kind =
    prefix === 'bg' ? 'bg' :
    prefix === 'text' || prefix === 'placeholder' || prefix === 'fill' || prefix === 'stroke' ? 'text' :
    prefix === 'border' || prefix === 'divide' || prefix === 'ring' || prefix === 'ring-offset' || prefix === 'outline' || prefix === 'decoration' ? 'border' :
    prefix === 'accent' || prefix === 'caret' ? 'bg' :
    prefix === 'from' || prefix === 'to' || prefix === 'via' ? 'bg' : null;
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

function emit(list, mapper, label, isDark = false) {
  // Group selectors that end up with the same declaration — the generated
  // sheet is large and near-identical rules compress badly when repeated.
  const groups = new Map();
  for (const token of [...list].sort()) {
    const parsed = parse(token);
    if (!parsed) continue;
    const { prefix, hue, shade, alpha } = parsed;
    let value = mapper(prefix, hue, shade);
    if (!value) continue;
    if (alpha != null) value = withAlpha(value, alpha);
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

const light = emit(tokens, lightValue, ':where(html) ');
const dark = emit(tokens, darkValue, ':where(html.dark) ', true);


/* Hand-written pieces the matrix cannot express: white/black surfaces,
   elevation, and third-party widgets. */
const EXTRAS = `
/* ── white / black surfaces ───────────────────────────────────────────── */
:where(html.dark) .bg-white,
:where(html.dark) .hover\\:bg-white:hover,
:where(html.dark) .focus\\:bg-white:focus { background-color: var(--surface-card); }

:where(html.dark) .bg-white\\/95 { background-color: rgba(18, 26, 43, 0.95); }
:where(html.dark) .bg-white\\/80 { background-color: rgba(18, 26, 43, 0.80); }
:where(html.dark) .bg-white\\/70 { background-color: rgba(18, 26, 43, 0.70); }
:where(html.dark) .bg-white\\/60 { background-color: rgba(18, 26, 43, 0.60); }
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
 *   2. Brand remap  — indigo/violet/purple/fuchsia utilities repainted navy.
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

  --surface-page:    #f4f6fb;
  --surface-sunken:  #f8fafc;
  --surface-card:    #ffffff;
  --surface-raised:  #f1f5f9;
  --surface-hover:   #eef2f7;
  --surface-inverse: #0d1526;

  --border-subtle:   #e8edf4;
  --border-default:  #e2e8f0;
  --border-strong:   #cbd5e1;

  --text-primary:    #0f172a;
  --text-secondary:  #334155;
  --text-muted:      #64748b;
  --text-faint:      #94a3b8;
  --text-inverse:    #f8fafc;

${SHADES.map((s) => `  --brand-${s}: ${BRAND_LIGHT[s]};`).join('\n')}
  --brand:           ${BRAND_LIGHT[600]};
  --brand-contrast:  #ffffff;
  --brand-rgb:       ${BRAND_RGB};

  --ok:      #15803d;
  --warn:    #b45309;
  --danger:  #b91c1c;
  --info:    ${BRAND_LIGHT[600]};

  --ok-surface:     #dcfce7;
  --warn-surface:   #fef08a;
  --danger-surface: #fee2e2;
  --info-surface:   ${BRAND_LIGHT[100]};
}

html.dark {
  color-scheme: dark;

  --surface-page:    #070d18;
  --surface-sunken:  #0c1322;
  --surface-card:    #121a2b;
  --surface-raised:  #18223a;
  --surface-hover:   #1e2943;
  --surface-inverse: #eef2fa;

  --border-subtle:   #1a2440;
  --border-default:  #25314c;
  --border-strong:   #31405e;

  --text-primary:    #eef2fa;
  --text-secondary:  #ccd5e3;
  --text-muted:      #9aa7bd;
  --text-faint:      #8b98ae;
  --text-inverse:    #0d1526;

${Object.entries(BRAND_DARK_VARS).map(([s, v]) => `  --brand-${s}: ${v};`).join('\n')}
  --brand:           #3f66d8;
  --brand-contrast:  #ffffff;

  --ok:      #6ee7b7;
  --warn:    #fcd34d;
  --danger:  #fca5a5;
  --info:    #8fb2ff;

  --ok-surface:     rgba(34, 197, 94, 0.24);
  --warn-surface:   rgba(234, 179, 8, 0.28);
  --danger-surface: rgba(239, 68, 68, 0.24);
  --info-surface:   rgba(63, 102, 216, 0.28);
}
`;

const out = [
  header,
  tokensBlock,
  '\n/* ── 2. brand remap (both themes) ─────────────────────────────────────── */\n',
  light.lines.join('\n'),
  '\n\n/* ── 3. dark theme ────────────────────────────────────────────────────── */\n',
  dark.lines.join('\n'),
  EXTRAS,
  '\n',
].join('\n');

writeFileSync(join(SRC, 'theme.css'), out);
console.log(`theme.css: ${light.count} brand utilities, ${dark.count} dark utilities, ${used.size} tokens found in src/`);
