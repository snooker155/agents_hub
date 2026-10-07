/**
 * Cutting the assistant's streamed answer into sentences to read aloud.
 *
 * The answer arrives token by token. Only its first paragraph is spoken
 * (docs/assistant.md "Voice"): the agent writes it to stand on its own read
 * aloud, and everything after it (lists, ids, links) is for the screen. So
 * the splitter is fed the stream and hands out each sentence of the first
 * paragraph as soon as it is complete, which is what lets the voice start
 * while the model is still writing.
 *
 * A full stop is not always the end of a sentence: "e.g.", "Dr.", "т. е.",
 * "z. B.", a decimal ("3.5"), a version ("v1.2"), an ordinal ("1.") or an
 * ellipsis inside a sentence. Those are kept together. Pure: tested without a
 * browser (__tests__/sentences.test.js).
 */

//: Abbreviations (lower case, without their final dot) after which a full
//: stop does not end the sentence, in the dashboard's three languages.
const ABBREVIATIONS = new Set([
  // English
  'e.g', 'i.e', 'etc', 'vs', 'mr', 'mrs', 'ms', 'dr', 'prof', 'st', 'approx', 'min', 'max',
  'jan', 'feb', 'mar', 'apr', 'jun', 'jul', 'aug', 'sep', 'sept', 'oct', 'nov', 'dec', 'inc', 'ltd',
  // Russian
  'т.е', 'т.к', 'т.д', 'т.п', 'и т.д', 'и т.п', 'др', 'пр', 'см', 'стр', 'руб', 'коп', 'млн', 'млрд',
  'тыс', 'г', 'гг', 'ул', 'д', 'им', 'проф', 'напр', 'т', 'е', 'п', 'к',
  // German
  'z.b', 'd.h', 'u.a', 'usw', 'bzw', 'ca', 'evtl', 'ggf', 'inkl', 'nr', 'str', 'vgl', 'z', 'b', 'h', 'u',
]);

//: Shortest text worth a request of its own; a shorter one waits to be joined
//: to the next sentence ("Ok." "Done.").
const MIN_CHARS = 12;

function endsWithAbbreviation(text) {
  // The word (or dotted run like "e.g" / "т.е") right before the final dot.
  const m = text.match(/([\p{L}.]+)\.$/u);
  if (!m) return false;
  const word = m[1].toLowerCase().replace(/^\.+/, '');
  if (ABBREVIATIONS.has(word)) return true;
  // "т. е." / "z. B." written with spaces: the last one or two letters alone.
  const spaced = text.match(/(?:^|\s)(\p{L})\.\s?(\p{L})\.$/u);
  if (spaced && ABBREVIATIONS.has(`${spaced[1]}.${spaced[2]}`.toLowerCase())) return true;
  return false;
}

/**
 * Where the first complete sentence of `text` ends (the index just past its
 * punctuation and any closing quote or bracket), or -1 when none is complete
 * yet. A sentence is complete only once the character after its punctuation
 * has arrived: "3." may still become "3.5".
 */
export function sentenceEnd(text) {
  const re = /[.!?…]+["'»”)\]]*/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const end = m.index + m[0].length;
    if (end >= text.length) return -1;          // the next character is not here yet
    const next = text[end];
    if (!/\s/.test(next)) continue;             // "3.5", "v1.2", "example.com"
    const head = text.slice(0, m.index + 1);
    if (m[0][0] === '.' && m[0].length === 1) {
      if (endsWithAbbreviation(head)) continue;
      // "1." at the start of a line is a list marker, not a sentence.
      if (/(^|\n)\s*\d{1,3}\.$/.test(head)) continue;
      // A digit, a dot and a lower case word: "в 5. мая" is rare; "page 5. Next"
      // is an end. Only a lone initial ("A. Smith") is kept together.
      if (/(^|\s)\p{Lu}\.$/u.test(head)) continue;
    }
    return end;
  }
  return -1;
}

/**
 * Feeds a token stream and yields sentences of its first paragraph.
 *
 *   const s = new SentenceStream();
 *   s.push('Hello there. How'); // ['Hello there.']
 *   s.push(' are you?\n\nMore'); // ['How are you?'], and it is done
 *   s.end();                     // whatever is left of the first paragraph
 */
export class SentenceStream {
  constructor({ minChars = MIN_CHARS } = {}) {
    this.buffer = '';
    this.pending = '';
    this.done = false;
    this.minChars = minChars;
  }

  /** Add streamed text; returns the sentences it completed. */
  push(chunk) {
    if (this.done || !chunk) return [];
    this.buffer += chunk;
    // The leading blank lines are not the paragraph break.
    this.buffer = this.buffer.replace(/^\s+/, '');
    const out = [];
    const brk = this.buffer.search(/\n\s*\n/);
    let paragraph = brk === -1 ? this.buffer : this.buffer.slice(0, brk);
    for (;;) {
      const end = sentenceEnd(paragraph);
      if (end === -1) break;
      this._emit(paragraph.slice(0, end), out);
      paragraph = paragraph.slice(end).replace(/^\s+/, '');
    }
    if (brk !== -1) {
      // The first paragraph is over: what is left of it is a sentence too.
      this._emit(paragraph, out, true);
      this.done = true;
      this.buffer = '';
      return out;
    }
    this.buffer = paragraph;
    return out;
  }

  /** The stream ended: the rest of the first paragraph, if any. */
  end() {
    if (this.done) return [];
    const out = [];
    this._emit(this.buffer, out, true);
    this.buffer = '';
    this.done = true;
    return out;
  }

  _emit(sentence, out, final = false) {
    const text = `${this.pending}${this.pending ? ' ' : ''}${String(sentence || '').trim()}`.trim();
    if (!text) return;
    if (!final && text.length < this.minChars) {
      this.pending = text;
      return;
    }
    this.pending = '';
    out.push(text);
  }
}

/** Every sentence of the first paragraph of a finished text. */
export function firstParagraphSentences(text) {
  const s = new SentenceStream();
  return [...s.push(String(text || '')), ...s.end()];
}

/**
 * Whether a sentence is worth reading aloud at all: a line that is only a
 * code fence, a table row or a heading marker is left to the screen.
 */
export function isSpeakable(sentence) {
  const t = String(sentence || '').trim();
  if (!t) return false;
  if (/^```/.test(t) || /^\|.*\|$/.test(t)) return false;
  return /[\p{L}\p{N}]/u.test(t);
}

/**
 * Markdown as the browser's own voice should read it, for when the hub has
 * no speech model (the hub cleans it itself otherwise, chat/voice.py
 * `speakable`): links by their anchor, no emphasis marks, no code.
 */
export function plainSpeech(text) {
  return String(text || '')
    .replace(/```[\s\S]*?(```|$)/g, ' ')
    .replace(/`[^`]*`/g, '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/(https?:\/\/|www\.)\S+/g, '')
    .replace(/^\s{0,3}#{1,6}\s*/gm, '')
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/gm, '')
    .replace(/(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1/g, '$2')
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ')
    .replace(/\s+([,.;:!?])/g, '$1')
    .trim();
}
