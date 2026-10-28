/**
 * The shape of the dark theme: the hand-tuned dark tables the generated
 * stylesheet ships with (scripts/gen-theme.mjs writes them into src/theme.css
 * under `html.dark`), kept here as plain data so that src/lib/palette.js can
 * read the same shape at run time.
 *
 * A palette applied at run time does not invent its own dark ladder: for
 * every dark custom property it keeps the *lightness* of the entry below and
 * only moves the hue (and a little chroma) to the chosen color. Shade 800 of
 * a neutral is therefore still a deep card surface in dark mode, whatever
 * tint was picked, instead of the mid grey a light-to-dark ramp lands on at
 * that shade. Translucent entries keep their alpha and take the new color's
 * rgb triple.
 *
 * Pure data, no imports: the generator runs it under node, the app under
 * the browser.
 */

export const BRAND_RGB = '63, 102, 216'; // #3f66d8

// Brand in dark mode: text tiers lighten, tint backgrounds become translucent.
export const BRAND_DARK = {
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

// The brand ramp inverted for dark mode, for inline styles that read
// var(--brand-N) directly: 50 is the deepest, 950 the palest.
export const BRAND_DARK_VARS = {
  50: '#0c1730', 100: '#12224a', 200: '#1a3068', 300: '#264aa8', 400: '#3159cc',
  500: '#3f66d8', 600: '#5b82e6', 700: '#8fb2ff', 800: '#bcd2ff', 900: '#d2e0ff',
  950: '#e4ecff',
};

// Navy-tinted neutral ladder. Backgrounds go page < sunken < card < raised
// for the light-surface shades (50..600, the ones light-mode markup paints
// panels with) and deeper again for 700..950 (the shades light-mode markup
// uses as dark fills).
export const NEUTRAL_DARK = {
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

// The semantic surface / border / text tokens of the dark theme, in the
// order the stylesheet declares them.
export const DARK_TOKENS = {
  '--surface-page': '#070d18',
  '--surface-sunken': '#0c1322',
  '--surface-card': '#121a2b',
  '--surface-raised': '#18223a',
  '--surface-hover': '#1e2943',
  '--surface-inverse': '#eef2fa',
  '--border-subtle': '#1a2440',
  '--border-default': '#25314c',
  '--border-strong': '#31405e',
  '--text-primary': '#eef2fa',
  '--text-secondary': '#ccd5e3',
  '--text-muted': '#9aa7bd',
  '--text-faint': '#8b98ae',
  '--text-inverse': '#0d1526',
};
