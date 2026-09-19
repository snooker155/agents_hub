import { createContext, useContext, useMemo } from 'react';
// Namespaces are auto-composed from ./locales/<lang>/<namespace>.js — adding a
// new namespace file is enough, no registration needed.
const MODULES = import.meta.glob('./locales/*/*.js', { eager: true });

const RESOURCES = {};
for (const [path, mod] of Object.entries(MODULES)) {
  const match = path.match(/\.\/locales\/([^/]+)\/([^/]+)\.js$/);
  if (!match) continue;
  const [, lang, namespace] = match;
  RESOURCES[lang] = RESOURCES[lang] || {};
  RESOURCES[lang][namespace] = mod.default || mod;
}

export const LANGUAGES = [
  { code: 'en', label: 'English', short: 'EN', flag: '🇬🇧' },
  { code: 'ru', label: 'Русский', short: 'RU', flag: '🇷🇺' },
  { code: 'de', label: 'Deutsch', short: 'DE', flag: '🇩🇪' },
];

export const DEFAULT_LANGUAGE = 'en';
// Walk a dot-path ("nav.groups.main") through a nested locale object.
function lookup(dict, key) {
  let node = dict;
  for (const part of key.split('.')) {
    if (node == null || typeof node !== 'object') return undefined;
    node = node[part];
  }
  return node;
}

// Locale-aware plural suffix: en/de use one|other, ru adds few|many.
function pluralKey(key, count, lang) {
  let category = 'other';
  try {
    category = new Intl.PluralRules(lang).select(count);
  } catch { /* unsupported locale — fall back to `other` */ }
  return [`${key}_${category}`, `${key}_other`, key];
}

function interpolate(template, vars) {
  if (!vars || typeof template !== 'string') return template;
  return template.replace(/\{\{(\w+)\}\}/g, (match, name) => (
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match
  ));
}

export function translate(lang, key, vars) {
  if (!key) return '';
  const dicts = [RESOURCES[lang], RESOURCES[DEFAULT_LANGUAGE]].filter(Boolean);
  const keys = (vars && typeof vars.count === 'number')
    ? pluralKey(key, vars.count, lang)
    : [key];
  for (const dict of dicts) {
    for (const k of keys) {
      const value = lookup(dict, k);
      if (typeof value === 'string') return interpolate(value, vars);
      if (Array.isArray(value)) return value.map((v) => interpolate(v, vars));
    }
  }
  // Last resort: show the leaf of the key rather than an empty label.
  return vars?.defaultValue ?? key;
}

export const I18nContext = createContext({
  language: DEFAULT_LANGUAGE,
  setLanguage: () => {},
  t: (key) => key,
});

export const useI18n = () => useContext(I18nContext);

// Convenience hook — `const { t } = useTranslation();` mirrors react-i18next.
export const useTranslation = () => useContext(I18nContext);


// Backend status enums (`running`, `awaiting_approval`, …) are shown all over
// the UI. They live in the shared `status` namespace and fall back to the raw
// value so an enum we have not translated yet still renders.
export function statusLabel(status, t) {
  if (!status) return '';
  return t(`status.${status}`, { defaultValue: String(status).replace(/_/g, ' ') });
}

// Locale-aware formatting helpers shared by pages.
export function useFormatters() {
  const { language } = useI18n();
  return useMemo(() => ({
    formatDate: (value, options) => {
      if (!value) return '';
      const d = value instanceof Date ? value : new Date(value);
      if (Number.isNaN(d.getTime())) return String(value);
      return d.toLocaleString(language, options);
    },
    formatNumber: (value, options) => {
      if (value == null || value === '') return '';
      const n = Number(value);
      if (Number.isNaN(n)) return String(value);
      return n.toLocaleString(language, options);
    },
  }), [language]);
}
