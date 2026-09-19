import { describe, it, expect } from 'vitest';
import { LANGUAGES, DEFAULT_LANGUAGE } from '../core';

// Same glob core.js composes the runtime dictionaries from, so the test sees
// exactly what the app sees — adding a namespace file needs no change here.
const MODULES = import.meta.glob('../locales/*/*.js', { eager: true });

const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/;

/** lang -> Set of dotted keys, with plural suffixes folded away. */
function keysByLanguage() {
  const out = {};
  for (const [path, mod] of Object.entries(MODULES)) {
    const [, lang, namespace] = path.match(/\.\.\/locales\/([^/]+)\/([^/]+)\.js$/);
    const bucket = (out[lang] ||= new Set());
    const walk = (node, prefix) => {
      for (const [k, v] of Object.entries(node)) {
        const key = `${prefix}.${k}`;
        if (v && typeof v === 'object' && !Array.isArray(v)) walk(v, key);
        else bucket.add(key.replace(PLURAL_SUFFIX, ''));
      }
    };
    walk(mod.default || mod, namespace);
  }
  return out;
}

const KEYS = keysByLanguage();
const OTHERS = LANGUAGES.map((l) => l.code).filter((code) => code !== DEFAULT_LANGUAGE);

describe('locale parity', () => {
  it('ships a dictionary for every advertised language', () => {
    LANGUAGES.forEach(({ code }) => {
      expect(Object.keys(KEYS)).toContain(code);
      expect(KEYS[code].size).toBeGreaterThan(0);
    });
  });

  it.each(OTHERS)('%s translates every key English has', (lang) => {
    const missing = [...KEYS[DEFAULT_LANGUAGE]].filter((key) => !KEYS[lang].has(key));
    expect(missing).toEqual([]);
  });

  it.each(OTHERS)('%s has no key English lost', (lang) => {
    // An extra key is a leftover: the English string was renamed or deleted and
    // the translation was not, so nothing will ever read it again.
    const extra = [...KEYS[lang]].filter((key) => !KEYS[DEFAULT_LANGUAGE].has(key));
    expect(extra).toEqual([]);
  });

  it('keeps every leaf a string or a list of strings', () => {
    Object.entries(MODULES).forEach(([path, mod]) => {
      const walk = (node, prefix) => {
        Object.entries(node).forEach(([k, v]) => {
          const key = `${prefix}.${k}`;
          if (Array.isArray(v)) v.forEach((item) => expect(typeof item, key).toBe('string'));
          else if (v && typeof v === 'object') walk(v, key);
          else expect(typeof v, `${path} ${key}`).toBe('string');
        });
      };
      walk(mod.default || mod, '');
    });
  });
});
