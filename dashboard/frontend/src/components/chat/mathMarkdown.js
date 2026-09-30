/**
 * Math in a chat reply, brought to the one syntax remark-math reads.
 *
 * Models write formulas four ways: `$x$` and `$$x$$` (Claude, most
 * markdown), `\(x\)` and `\[x\]` (GPT, LaTeX habit). remark-math only knows
 * dollars, and its single-dollar rule would turn "from $5 to $10" into a
 * formula. So ChatMarkdown switches single dollars off and this rewrites every
 * formula it recognises to double dollars:
 *
 *   \[x\], and $$x$$ standing alone on its lines  ->  a `$$` block (display)
 *   \(x\), and $x$ by pandoc's rule               ->  `$$x$$` in the line (inline)
 *
 * Pandoc's rule for `$x$`: no space right after the opening dollar, none right
 * before the closing one, and no digit right after it. Prices fail it ("$5 and
 * $10" closes on a space, "$5-$10" before a digit), formulas pass it.
 *
 * Code is left exactly as written: fenced blocks (a ```math fence is rendered
 * as a formula by rehype-katex itself) and inline code spans.
 */

const CODE_RE = /^ {0,3}(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^ {0,3}\1[ \t]*$|$(?![\s\S]))|(`+)[^`\n]*?\2/gm;

const BRACKET_DISPLAY_RE = /(?<!\\)\\\[([\s\S]+?)(?<!\\)\\\]/g;
const PAREN_INLINE_RE = /(?<!\\)\\\(([^\n]+?)(?<!\\)\\\)/g;
// `$$x$$` taking up whole lines on its own; `$$` alone on a line is already a block.
const DOLLAR_DISPLAY_RE = /^[ \t]*\$\$(?![ \t]*$)((?:(?!\$\$)[\s\S])+?)\$\$[ \t]*$/gm;
const SINGLE_DOLLAR_RE = /(?<![\\$\w])\$(?=[^\s$])([^$\n]*?[^\s$\\])\$(?![\d$])/g;

const block = (body) => `\n$$\n${body.trim()}\n$$\n`;

function rewriteProse(prose) {
  return prose
    .replace(BRACKET_DISPLAY_RE, (_, body) => block(body))
    .replace(DOLLAR_DISPLAY_RE, (_, body) => block(body))
    .replace(PAREN_INLINE_RE, (_, body) => `$$${body.trim()}$$`)
    .replace(SINGLE_DOLLAR_RE, (_, body) => `$$${body}$$`);
}

// Applies `fn` to the text between code, never to the code itself.
function outsideCode(text, fn) {
  let out = '';
  let last = 0;
  for (const m of text.matchAll(CODE_RE)) {
    out += fn(text.slice(last, m.index)) + m[0];
    last = m.index + m[0].length;
  }
  return out + fn(text.slice(last));
}

export function normalizeMath(text) {
  const source = String(text || '');
  if (!/[$\\]/.test(source)) return source;
  return outsideCode(source, rewriteProse);
}

// The prose of `text` with code blanked out, same length, so an index found in
// it is an index into `text`.
function maskCode(text) {
  let out = '';
  let last = 0;
  for (const m of text.matchAll(CODE_RE)) {
    out += text.slice(last, m.index) + ' '.repeat(m[0].length);
    last = m.index + m[0].length;
  }
  return out + text.slice(last);
}

/**
 * A reply still arriving, cut before a formula that has not closed yet.
 *
 * Half a formula is not LaTeX: KaTeX would flash it red on every token until
 * the closing delimiter arrives. Holding it back makes the formula appear
 * whole, the way a code block's language does. A lone `$` is never held back,
 * it is as likely a price as a formula.
 */
export function holdOpenMath(text) {
  const source = String(text || '');
  if (!/[$\\]/.test(source)) return source;
  const masked = maskCode(source);
  let cut = source.length;

  const dollars = [...masked.matchAll(/(?<!\\)\$\$/g)];
  if (dollars.length % 2 === 1) cut = Math.min(cut, dollars[dollars.length - 1].index);

  for (const [open, close] of [['\\[', '\\]'], ['\\(', '\\)']]) {
    const at = masked.lastIndexOf(open);
    if (at !== -1 && masked.indexOf(close, at) === -1 && masked[at - 1] !== '\\') {
      cut = Math.min(cut, at);
    }
  }
  return cut === source.length ? source : source.slice(0, cut).replace(/\s+$/, '');
}

// True when the normalised text has anything for KaTeX to render.
export function hasMath(normalized) {
  return normalized.includes('$$') || /^ {0,3}(`{3,}|~{3,})\s*math\b/m.test(normalized);
}
