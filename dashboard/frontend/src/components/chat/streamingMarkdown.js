/**
 * Markdown that is still being written, made safe to render as it is.
 *
 * A reply is parsed again on every token, so between tokens the text is
 * usually cut in the middle of something: `**bold` whose closing stars have
 * not arrived, a half-typed `[link](http://ex`, a lone `-` under a line (which
 * markdown reads as a heading underline). Rendered raw, each of those shows
 * its markup for a moment and then jumps into shape. This closes or drops
 * such a tail so every intermediate render already looks like its final one.
 *
 * Only the tail is touched: inline markup cannot span a blank line, so
 * everything before the last one is already settled. An open code fence is
 * left alone; markdown already renders an unclosed fence as a code block
 * running to the end, which is exactly right mid-stream.
 */

const FENCE_RE = /^ {0,3}(```|~~~)/;

function insideOpenFence(text) {
  let open = null;
  for (const line of text.split('\n')) {
    const m = FENCE_RE.exec(line);
    if (!m) continue;
    if (!open) open = m[1];
    else if (m[1] === open) open = null;
  }
  return open !== null;
}

// Blanks out inline code spans and formulas so markers inside them (`a*b`,
// `$a*b$`) are not counted; the length is kept so positions still line up
// with the original tail.
const MASKED_RE = /`[^`\n]*`|\$\$[^$]*\$\$|\$[^$\n]*\$|\\\([^\n]*?\\\)|\\\[[\s\S]*?\\\]/g;

function maskCode(tail) {
  return tail.replace(MASKED_RE, (span) => ' '.repeat(span.length));
}

// The position of the last opener of `marker` left without a partner, or -1.
function unmatchedOpener(masked, marker) {
  const positions = [];
  let i = masked.indexOf(marker);
  while (i !== -1) {
    positions.push(i);
    i = masked.indexOf(marker, i + marker.length);
  }
  return positions.length % 2 === 1 ? positions[positions.length - 1] : -1;
}

// Single `*` emphasis: stars that are neither part of `**` nor a list bullet.
function unmatchedStar(masked) {
  const positions = [];
  for (let i = 0; i < masked.length; i += 1) {
    if (masked[i] !== '*') continue;
    if (masked[i - 1] === '*' || masked[i + 1] === '*') continue;
    const lineStart = masked.lastIndexOf('\n', i - 1) + 1;
    if (/^\s*$/.test(masked.slice(lineStart, i)) && masked[i + 1] === ' ') continue;
    positions.push(i);
  }
  return positions.length % 2 === 1 ? positions[positions.length - 1] : -1;
}

export function closeOpenMarkdown(text) {
  const source = String(text || '');
  if (!source || insideOpenFence(source)) return source;

  const cut = source.lastIndexOf('\n\n');
  const head = cut === -1 ? '' : source.slice(0, cut + 2);
  let tail = cut === -1 ? source : source.slice(cut + 2);

  // A line of only `-` or `=` under text turns that text into a heading until
  // the rest of the line (a list item, a rule) arrives.
  tail = tail.replace(/\n[ \t]*[-=]+[ \t]*$/, '');

  // A link still being typed: show its text, not the brackets and the URL.
  tail = tail.replace(/\[([^\]\n]*)\]\([^)\s]*$/, '$1');

  // An inline code span with no closing backtick yet.
  const ticks = (tail.replace(/```/g, '').match(/`/g) || []).length;
  if (ticks % 2 === 1) {
    tail = tail.endsWith('`') ? tail.slice(0, -1) : `${tail}\``;
  }

  const masked = maskCode(tail);
  const openers = [
    { marker: '**', at: unmatchedOpener(masked, '**') },
    { marker: '~~', at: unmatchedOpener(masked, '~~') },
    { marker: '*', at: unmatchedStar(masked) },
  ].filter((o) => o.at !== -1);
  // Close the innermost first: the opener that came last closes first.
  openers.sort((a, b) => b.at - a.at);
  for (const { marker, at } of openers) {
    if (!tail.slice(at + marker.length).trim()) {
      // Nothing after the marker yet: closing it would render an empty span
      // (or, for `****` alone on a line, a horizontal rule). Drop it instead.
      tail = tail.slice(0, at) + tail.slice(at + marker.length);
    } else {
      tail = `${tail.replace(/\s+$/, '')}${marker}`;
    }
  }
  return head + tail;
}

export default closeOpenMarkdown;
