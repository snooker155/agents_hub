/**
 * The Help agent's replies, cut into what the panel draws.
 *
 * The agent ends every answer with next steps as Markdown links to dashboard
 * pages (`[Models](/models)`). A plain `<a href="/models">` would leave the app
 * (and miss the demo's base path), so links to a route become in-app
 * navigation, `#tour` starts the welcome tour, and a web link opens in a new
 * tab. `**bold**` is kept because the agent uses it for page names. Everything
 * else stays text. Pure, so it is tested without React.
 */

export const TOUR_TARGET = '#tour';

const TOKEN = /\[([^\]\n]+)\]\(([^)\s]+)\)|\*\*([^*\n]+)\*\*/g;

/** Whether a link target is a route inside this app. */
export function isAppRoute(target) {
  return typeof target === 'string' && target.startsWith('/') && !target.startsWith('//');
}

export function linkSegment(label, target) {
  if (target === TOUR_TARGET) return { type: 'tour', text: label };
  if (isAppRoute(target)) return { type: 'nav', text: label, to: target };
  if (/^https?:\/\//i.test(target)) return { type: 'external', text: label, href: target };
  // Anything else (a relative doc path, a scheme we do not open) reads as text.
  return { type: 'text', text: label };
}

/** Segments of `{type: 'text'|'strong'|'nav'|'tour'|'external', text, to?, href?}`. */
export function parseHelpText(text) {
  const src = String(text ?? '');
  const out = [];
  let last = 0;
  for (const m of src.matchAll(TOKEN)) {
    if (m.index > last) out.push({ type: 'text', text: src.slice(last, m.index) });
    out.push(m[3] != null ? { type: 'strong', text: m[3] } : linkSegment(m[1], m[2]));
    last = m.index + m[0].length;
  }
  if (last < src.length) out.push({ type: 'text', text: src.slice(last) });
  // Neighbouring text runs merge, so a link that fell back to text reads whole.
  return out.reduce((acc, seg) => {
    const prev = acc[acc.length - 1];
    if (seg.type === 'text' && prev?.type === 'text') prev.text += seg.text;
    else acc.push({ ...seg });
    return acc;
  }, []);
}

export default parseHelpText;
