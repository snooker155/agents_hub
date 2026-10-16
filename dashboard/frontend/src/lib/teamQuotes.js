/**
 * Split a team member's message into its own text and the outside text it
 * quoted between a `<<<QUOTE source>>>` line and a `<<<END_QUOTE>>>` line
 * (teams/prompts.py). A quote that is never closed runs to the end, as on
 * the backend (common/untrusted.py).
 */

const OPEN_RE = /<<<QUOTE\b([^\n]*?)>>>/;
const CLOSE = '<<<END_QUOTE>>>';

export function splitQuotes(text) {
  const parts = [];
  let rest = text || '';
  for (;;) {
    const open = OPEN_RE.exec(rest);
    if (!open) break;
    if (open.index > 0) parts.push({ type: 'text', text: rest.slice(0, open.index) });
    const after = rest.slice(open.index + open[0].length);
    const end = after.indexOf(CLOSE);
    const body = end === -1 ? after : after.slice(0, end);
    parts.push({ type: 'quote', source: open[1].trim(), text: body.trim() });
    rest = end === -1 ? '' : after.slice(end + CLOSE.length);
  }
  if (rest) parts.push({ type: 'text', text: rest });
  return parts;
}
