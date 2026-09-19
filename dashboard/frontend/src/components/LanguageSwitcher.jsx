import { useState, useRef, useEffect } from 'react';
import { Languages, Check } from 'lucide-react';
import { useI18n, LANGUAGES } from '../i18n';

/**
 * Interface language picker. Lives in the top bar right next to the theme
 * toggle and persists the choice in localStorage via the i18n provider.
 */
const LanguageSwitcher = () => {
  const { language, setLanguage, t } = useI18n();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onClickOutside = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [open]);

  const current = LANGUAGES.find((l) => l.code === language) || LANGUAGES[0];

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((v) => !v)}
        title={t('layout.language.tooltip')}
        aria-label={t('layout.language.label')}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 hover:text-gray-900 text-xs font-medium transition-colors"
      >
        <Languages className="w-4 h-4" />
        <span>{current.short}</span>
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 bg-white border border-gray-200 rounded-xl shadow-lg z-50 min-w-40 py-1">
          {LANGUAGES.map((lang) => (
            <button
              key={lang.code}
              onClick={() => { setLanguage(lang.code); setOpen(false); }}
              className={`w-full text-left flex items-center gap-2 px-3 py-2 text-xs hover:bg-gray-50 transition-colors ${
                lang.code === language ? 'bg-indigo-50 text-indigo-600 font-semibold' : 'text-gray-700'
              }`}
            >
              <span className="text-sm leading-none">{lang.flag}</span>
              <span className="truncate">{lang.label}</span>
              {lang.code === language && <Check className="w-3.5 h-3.5 ml-auto shrink-0" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

export default LanguageSwitcher;
