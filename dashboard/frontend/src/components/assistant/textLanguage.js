/**
 * The language a line is in, as the hub tells it before reading it aloud
 * (providers/speech_languages.py `text_language`): Cyrillic is Russian
 * (Ukrainian with its own letters), Latin is German or English by umlauts and
 * common words, a tie is `fallback` (the page's language) when that is one of
 * the two, else English. The browser's own voice takes its locale from it, so
 * a Russian answer on an English page is read with a Russian voice.
 */
const CYRILLIC = /[Ѐ-ӿ]/g;
const LATIN = /[A-Za-zÀ-ÿ]/g;
const UKRAINIAN = /[іїєґІЇЄҐ]/;
const UMLAUT = /[äöüßÄÖÜ]/;
const GERMAN = new Set(('der das und ist nicht ich sie es ein eine einen mit für auf zu den dem wir ihr wie '
  + 'auch sind haben kann bitte danke hallo ja nein gut sehr oder aber noch schon jetzt hier wird werden '
  + 'mein dein ihre ihnen').split(' '));
const ENGLISH = new Set(('the and is not you it with for on to we are have can please thanks hello yes no '
  + 'good very or but still already now here will this that your what how my').split(' '));

export default function textLanguage(text, fallback = '') {
  const s = String(text || '');
  const fb = String(fallback || '').toLowerCase().slice(0, 2);
  const cyrillic = (s.match(CYRILLIC) || []).length;
  const latin = (s.match(LATIN) || []).length;
  if (!cyrillic && !latin) return fb;
  if (cyrillic >= latin) return UKRAINIAN.test(s) ? 'uk' : 'ru';
  const words = s.toLowerCase().match(/[a-zäöüß]+/g) || [];
  const german = words.filter((w) => GERMAN.has(w)).length + (UMLAUT.test(s) ? 2 : 0);
  const english = words.filter((w) => ENGLISH.has(w)).length;
  if (german > english) return 'de';
  if (english > german) return 'en';
  return fb === 'en' || fb === 'de' ? fb : 'en';
}
