/**
 * Who is who, and how a turn reads — shared by every surface that draws a run.
 *
 * The transcript, the stage and anything else that names an agent have to give
 * "Old Tam" the same colour and the same initials, or following one character
 * across two panes means reading names letter by letter. So the palette and the
 * few readings of an action that more than one pane needs live here rather than
 * inside whichever component happened to need them first.
 */

/**
 * One rule decides every colour in a turn: **colour is what the agent said;
 * everything else is machinery and is grey.**
 *
 * It did not start that way and the result was upside down. The mail it was
 * handed, its thought and the call it made were each a tinted, bordered card
 * — blue, violet, amber — while the reply itself was white on a white card,
 * the palest thing in the turn. The eye went to the inputs and the plumbing
 * and had to hunt for the one line the agent actually contributed.
 *
 * So the supporting cards lost their fills: a thin grey border, a small grey
 * label, and one coloured icon each to stay recognisable at a glance. Incoming
 * mail — the quietest of them, since it is somebody else's words repeated back
 * — lost its box entirely and is now a rule in the margin. The spoken line
 * gets the only filled surface in the turn, in the agent's own tone, so it
 * reads as the figure and matches the avatar that said it.
 *
 * Two exceptions keep their colour, because both are news rather than
 * machinery: a refused action and a failed turn.
 *
 * Tailwind cannot resolve interpolated class names, so each tone is literal.
 *
 * `avatar` / `name` / `bubble` are the transcript's three uses; `line`, `dot`
 * and `ring` are for the stage, where an agent is drawn rather than written —
 * an SVG stroke picks `line` up through `currentColor`, which keeps the theme
 * remap working on a canvas.
 */
export const TONES = [
  { avatar: 'bg-indigo-100 text-indigo-700', name: 'text-indigo-900', bubble: 'bg-indigo-50 border-indigo-100', line: 'text-indigo-500', dot: 'bg-indigo-500', ring: 'ring-indigo-300' },
  { avatar: 'bg-emerald-100 text-emerald-700', name: 'text-emerald-900', bubble: 'bg-emerald-50 border-emerald-100', line: 'text-emerald-500', dot: 'bg-emerald-500', ring: 'ring-emerald-300' },
  { avatar: 'bg-amber-100 text-amber-700', name: 'text-amber-900', bubble: 'bg-amber-50 border-amber-100', line: 'text-amber-500', dot: 'bg-amber-500', ring: 'ring-amber-300' },
  { avatar: 'bg-sky-100 text-sky-700', name: 'text-sky-900', bubble: 'bg-sky-50 border-sky-100', line: 'text-sky-500', dot: 'bg-sky-500', ring: 'ring-sky-300' },
  { avatar: 'bg-rose-100 text-rose-700', name: 'text-rose-900', bubble: 'bg-rose-50 border-rose-100', line: 'text-rose-500', dot: 'bg-rose-500', ring: 'ring-rose-300' },
  { avatar: 'bg-violet-100 text-violet-700', name: 'text-violet-900', bubble: 'bg-violet-50 border-violet-100', line: 'text-violet-500', dot: 'bg-violet-500', ring: 'ring-violet-300' },
  { avatar: 'bg-teal-100 text-teal-700', name: 'text-teal-900', bubble: 'bg-teal-50 border-teal-100', line: 'text-teal-500', dot: 'bg-teal-500', ring: 'ring-teal-300' },
  { avatar: 'bg-orange-100 text-orange-700', name: 'text-orange-900', bubble: 'bg-orange-50 border-orange-100', line: 'text-orange-500', dot: 'bg-orange-500', ring: 'ring-orange-300' },
];

/** Stable per-name colour, so an agent looks the same on every tick. */
export function toneFor(name) {
  let h = 0;
  for (let i = 0; i < String(name).length; i += 1) {
    h = (h * 31 + String(name).charCodeAt(i)) >>> 0;
  }
  return TONES[h % TONES.length];
}

export function initials(name) {
  return String(name || '?')
    .split(/[\s_\-.]+/).filter(Boolean).slice(0, 2)
    .map((w) => w[0].toUpperCase()).join('') || '?';
}

// Which argument carries something an agent *said*. An action with one of these
// is speech first and a tool call second, so its text becomes the bubble and
// the call itself collapses into a card underneath.
export const SPEECH_ARGS = ['text', 'message', 'content', 'say'];
export const ADDRESSEE_ARGS = ['agent', 'to', 'target', 'recipient'];

export function speechOf(action) {
  const args = action?.args || {};
  const key = SPEECH_ARGS.find((k) => typeof args[k] === 'string' && args[k].trim());
  if (!key) return null;
  const toKey = ADDRESSEE_ARGS.find((k) => typeof args[k] === 'string' && args[k].trim());
  return { text: String(args[key]), to: toKey ? String(args[toKey]) : '' };
}

/** Who an action is aimed at, whether or not it carries any speech. */
export function addresseeOf(action) {
  const args = action?.args || {};
  const key = ADDRESSEE_ARGS.find((k) => typeof args[k] === 'string' && args[k].trim());
  return key ? String(args[key]).trim() : '';
}

export function shortText(value, max = 160) {
  const s = String(value ?? '');
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

/** `side=buy, price=101.5` — an action's arguments as one readable line. */
export function formatArgs(args) {
  return Object.entries(args || {})
    .map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`)
    .join(', ');
}
