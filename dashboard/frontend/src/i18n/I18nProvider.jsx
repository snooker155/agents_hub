import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { I18nContext, DEFAULT_LANGUAGE, LANGUAGES, translate } from './core';

const STORAGE_KEY = 'agents_hub_language';

function detectLanguage() {
  const supported = new Set(LANGUAGES.map((l) => l.code));
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && supported.has(stored)) return stored;
  } catch { /* storage unavailable */ }
  const candidates = (navigator.languages && navigator.languages.length)
    ? navigator.languages
    : [navigator.language || DEFAULT_LANGUAGE];
  for (const c of candidates) {
    const base = String(c).toLowerCase().split('-')[0];
    if (supported.has(base)) return base;
  }
  return DEFAULT_LANGUAGE;
}

/** Holds the active language and hands `t` to the tree below it. */
export default function I18nProvider({ children }) {
  const [language, setLanguageState] = useState(detectLanguage);

  useEffect(() => {
    document.documentElement.setAttribute('lang', language);
  }, [language]);

  // `t` keeps a stable identity and reads the language from a ref, so a callback
  // or effect that captured it still translates into the *current* language
  // rather than the one active when it was created. The ref is written here, in
  // the event handler that changes the language, so it is already up to date by
  // the time the state update re-renders the tree.
  const languageRef = useRef(language);

  const setLanguage = useCallback((lang) => {
    if (!LANGUAGES.some((l) => l.code === lang)) return;
    languageRef.current = lang;
    setLanguageState(lang);
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch { /* storage unavailable */ }
  }, []);

  const t = useCallback((key, vars) => translate(languageRef.current, key, vars), []);

  const value = useMemo(() => ({ language, setLanguage, t }), [language, setLanguage, t]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
