/**
 * Pure helpers of the Widgets page: the form's text and number conversions,
 * the accent swatches, and the preview page the live preview's iframe loads.
 */

// Swatches for the accent picker, as theme utility classes (theme.css maps
// them for both themes). The widget itself draws each accent from its own
// palette inside its shadow root (dashboard/frontend/widget/widget.js); these
// only have to look like the same family.
export const ACCENT_SWATCHES = {
  navy: 'bg-indigo-700',
  blue: 'bg-blue-600',
  teal: 'bg-teal-600',
  green: 'bg-green-700',
  amber: 'bg-amber-600',
  rose: 'bg-rose-600',
  slate: 'bg-slate-600',
};

export const FALLBACK_OPTIONS = {
  accents: Object.keys(ACCENT_SWATCHES),
  languages: ['auto', 'en', 'ru', 'de'],
  limits: {
    messages_per_minute: { min: 1, max: 120, default: 6 },
    attachment_max_bytes: { min: 0, max: 5 * 1024 * 1024, default: 2 * 1024 * 1024 },
    max_attachments: { min: 0, max: 5, default: 3 },
    tokens_per_day: { min: 0, max: 100000000, default: 200000 },
  },
  wildcard_allowed: false,
};

/** One origin per line (commas accepted too), blanks dropped. */
export function originsFromText(text) {
  return String(text || '')
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

export function originsToText(origins) {
  return (origins || []).join('\n');
}

export const bytesToKb = (bytes) => Math.round((Number(bytes) || 0) / 1024);
export const kbToBytes = (kb) => Math.max(0, Math.round(Number(kb) || 0)) * 1024;

/** A number input's value clamped to a limit's bounds. */
export function clampLimit(value, bounds) {
  const n = Math.round(Number(value));
  if (!Number.isFinite(n)) return bounds?.default ?? 0;
  const min = bounds?.min ?? 0;
  const max = bounds?.max ?? n;
  return Math.min(max, Math.max(min, n));
}

/** The short form of a visitor id for the conversation list. */
export const shortVisitor = (visitorId) => String(visitorId || '').replace(/^vis_/, '').slice(0, 6);

const escapeAttr = (value) => String(value ?? '')
  .replace(/&/g, '&amp;')
  .replace(/"/g, '&quot;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;');

const escapeText = (value) => String(value ?? '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;');

/**
 * The preview page: an empty document that embeds the widget exactly as a
 * site would, plus the preview ticket and the hub's own origin (a srcdoc
 * frame runs on the dashboard's origin, which is not in the widget's list;
 * the ticket is what lets it in). Every value is escaped: none of them
 * should ever need it, and the page must not depend on that.
 */
export function previewDocument({ hub, widgetId, publicKey, ticket, scriptPath, text, lang }) {
  const src = `${hub}${scriptPath || '/api/widgets/public/widget.js'}`;
  return [
    '<!doctype html>',
    `<html lang="${escapeAttr(lang || 'en')}"><head><meta charset="utf-8">`,
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
    // System colours only: the preview page follows the viewer's light or
    // dark setting without carrying a palette of its own.
    '<style>html{color-scheme:light dark}body{margin:0;min-height:100vh;font:14px system-ui,sans-serif;',
    'background:Canvas;color:GrayText;display:flex;align-items:center;justify-content:center}</style>',
    '</head><body>',
    `<p>${escapeText(text)}</p>`,
    `<script src="${escapeAttr(src)}" data-widget="${escapeAttr(widgetId)}" data-key="${escapeAttr(publicKey)}"`,
    ` data-hub="${escapeAttr(hub)}" data-preview="${escapeAttr(ticket)}" data-open="true" async></script>`,
    '</body></html>',
  ].join('');
}
