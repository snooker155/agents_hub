/**
 * What a few spoken words mean to the page itself, before anything is sent
 * as a turn: a stop ("стоп", "hör auf"), the end of a conversation ("пока",
 * "goodbye"), and the wake phrase that calls the assistant ("Ассистент,
 * покажи задачи"). All three are matched on the whole utterance, never on a
 * word inside a longer sentence: "stop the nightly job" is a request for the
 * agent, not a press of Stop.
 */

const FILLERS = new Set([
  'please', 'ok', 'okay', 'hey', 'now', 'just',
  'пожалуйста', 'ну', 'ок', 'окей', 'эй', 'слушай', 'всё', 'все', 'уже',
  'bitte', 'jetzt', 'mal', 'hey', 'ok',
]);

//: Interrupt whatever the assistant is doing: the turn and its voice.
const STOP = [
  'stop', 'stop it', 'stop that', 'stop talking', 'stop now', 'enough', 'that is enough', 'thats enough',
  'be quiet', 'quiet', 'shut up', 'halt', 'hold on', 'wait', 'cancel that', 'never mind', 'nevermind',
  'стоп', 'хватит', 'остановись', 'остановиться', 'остановить', 'прекрати', 'прекращай', 'замолчи',
  'молчи', 'тихо', 'подожди', 'погоди', 'довольно', 'отставить', 'стой', 'перестань', 'не надо дальше',
  'stopp', 'halt', 'hör auf', 'hor auf', 'aufhören', 'aufhoren', 'genug', 'ruhe', 'sei still', 'warte',
  'moment',
];

//: End a hands-free conversation (said while nothing is running).
const END = [
  'goodbye', 'bye', 'bye bye', 'good bye', 'end conversation', 'end the conversation', 'stop listening',
  'that is all', 'thats all', 'we are done', 'were done', 'thank you that is all',
  'пока', 'пока пока', 'до свидания', 'конец разговора', 'закончим', 'закончим разговор',
  'закончить разговор', 'хватит слушать', 'перестань слушать', 'на этом всё', 'на этом все', 'это всё',
  'это все', 'спасибо это всё', 'спасибо это все',
  'tschüss', 'tschuss', 'auf wiedersehen', 'gespräch beenden', 'gesprach beenden', 'das wars',
  'das war es', 'hör auf zuzuhören', 'hor auf zuzuhoren', 'ende',
];

//: The wake phrase when the person has not set one: any interface language's.
export const DEFAULT_WAKE = { en: 'assistant', ru: 'ассистент', de: 'assistent' };

/** Longest utterance still read as a stop or an end, in words, fillers aside. */
export const MAX_COMMAND_WORDS = 4;

/** Lower case, ё as е, punctuation dropped: one form to compare. */
export function normalizeSpeech(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/['’]/g, '')
    .replace(/[^\p{L}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

const STOP_SET = new Set(STOP.map(normalizeSpeech));
const END_SET = new Set(END.map(normalizeSpeech));
const STOP_WORDS = new Set([...STOP_SET].filter((p) => !p.includes(' ')));

function editDistance(a, b) {
  if (Math.abs(a.length - b.length) > 1) return 2;
  const prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i += 1) {
    let diag = prev[0];
    prev[0] = i;
    for (let j = 1; j <= b.length; j += 1) {
      const up = prev[j];
      prev[j] = Math.min(prev[j] + 1, prev[j - 1] + 1, diag + (a[i - 1] === b[j - 1] ? 0 : 1));
      diag = up;
    }
  }
  return prev[b.length];
}

function sharedPrefix(a, b) {
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i += 1;
  return i;
}

/** Whether a heard word is the phrase's word: exact, or near it for a long word
 *  ("ассистенты", "assistants", a one-letter slip, and "assistance", which is
 *  what recognisers often make of "assistant"). */
function sameWord(heard, word) {
  if (heard === word) return true;
  if (word.length < 5) return false;
  if (heard.startsWith(word) || editDistance(heard, word) <= 1) return true;
  return sharedPrefix(heard, word) >= word.length - 1 && heard.length - word.length <= 2;
}

/** The wake phrases in force: the person's own, else every language's default. */
export function wakePhrases(custom) {
  const own = normalizeSpeech(custom);
  return own ? [own] : Object.values(DEFAULT_WAKE).map(normalizeSpeech);
}

/**
 * Where the wake phrase is in an utterance: `{ rest }` with what was said
 * after it, in the words heard ("" when nothing followed), or null. The
 * phrase must open the utterance, after at most two fillers ("ok", "эй"):
 * the assistant named in the middle of a sentence is talk, not a call.
 */
export function findWake(text, phrases) {
  const tokens = String(text || '').split(/\s+/).filter(Boolean);
  const norm = tokens.map(normalizeSpeech);
  for (const phrase of phrases || []) {
    const words = phrase.split(' ').filter(Boolean);
    if (!words.length) continue;
    let at = 0;
    let skipped = 0;
    while (at < norm.length && skipped <= 2) {
      if (!norm[at]) { at += 1; continue; }
      if (words.every((w, i) => norm[at + i] !== undefined && sameWord(norm[at + i], w))) {
        const rest = tokens.slice(at + words.length).join(' ').replace(/^[\s,.:;!?—–-]+/, '').trim();
        return { rest };
      }
      if (!FILLERS.has(norm[at])) break;
      at += 1;
      skipped += 1;
    }
  }
  return null;
}

/** The utterance without the wake phrase in front and without fillers. */
function bare(text, phrases) {
  const wake = phrases ? findWake(text, phrases) : null;
  const words = normalizeSpeech(wake ? wake.rest : text).split(' ').filter((w) => w && !FILLERS.has(w));
  return words.length > MAX_COMMAND_WORDS ? null : words;
}

/** "Стоп", "assistant, stop", "stop stop stop", "hör auf, bitte". */
export function isStopCommand(text, phrases = null) {
  const words = bare(text, phrases);
  if (!words || !words.length) return false;
  if (STOP_SET.has(words.join(' '))) return true;
  return words.every((w) => STOP_WORDS.has(w));
}

/** "Пока", "goodbye", "конец разговора". */
export function isEndCommand(text, phrases = null) {
  const words = bare(text, phrases);
  return Boolean(words && words.length && END_SET.has(words.join(' ')));
}
