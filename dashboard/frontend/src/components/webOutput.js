/**
 * What a person sees of a web tool result. The model gets fetch_url's page
 * inside an untrusted-content envelope (tools/web.py): a begin marker, the
 * source line, a warning addressed to the model, `---`, the page text and an
 * end marker. For the process view the envelope is noise, so this keeps the
 * source address and the text that was found. The raw output stays available
 * wherever a "Raw" toggle exists.
 */
const BEGIN = '<<<UNTRUSTED_WEB_CONTENT>>>';
const END = '<<<END_UNTRUSTED_WEB_CONTENT>>>';

/** `{source, text}` for an enveloped result, or null for anything else. */
export function parseWebOutput(raw) {
  const s = String(raw ?? '');
  const start = s.indexOf(BEGIN);
  if (start < 0) return null;
  const inner = s.slice(start + BEGIN.length);
  const endAt = inner.indexOf(END);
  const block = endAt >= 0 ? inner.slice(0, endAt) : inner;
  const sep = block.indexOf('\n---\n');
  const head = sep >= 0 ? block.slice(0, sep) : '';
  const body = sep >= 0 ? block.slice(sep + 5) : block;
  const sourceLine = head.split('\n').find((l) => l.startsWith('source:')) || '';
  // "source: fetch:<url> -> <final url>": the address the text came from.
  const hops = sourceLine.replace(/^source:\s*/, '').replace(/^fetch:/, '').split(' -> ');
  const source = (hops[hops.length - 1] || '').trim();
  const text = body.replace(/^\s*\n/, '').replace(/\n{3,}/g, '\n\n').trimEnd();
  return { source, text };
}

/** The output as a person should read it: the found text under its source. */
export function displayToolOutput(raw) {
  const web = parseWebOutput(raw);
  if (!web) return raw;
  return web.source ? `${web.source}\n\n${web.text}` : web.text;
}
