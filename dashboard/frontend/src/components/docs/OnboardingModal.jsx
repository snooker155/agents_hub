import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Rocket, X, BookOpen } from 'lucide-react';
import OnboardingChecklist from './OnboardingChecklist';
import { useI18n } from '../../i18n';

// ---------------------------------------------------------------------------
// OnboardingModal — auto-opens on first launch (tracked in localStorage) and
// is otherwise dismissed. It can always be re-opened from the Docs section.
// Mounted once, near the app root (in Layout), so it overlays every page.
// ---------------------------------------------------------------------------

// Versioned: bumping the suffix re-shows the welcome popup once after the
// onboarding content changes, instead of hiding it forever from anyone who
// dismissed an older version.
export const ONBOARDING_SEEN_KEY = 'agents_hub_onboarding_seen_v2';

export default function OnboardingModal() {
  const { t } = useI18n();
  const navigate = useNavigate();
  // Open on first launch only — derived once from localStorage, so no effect
  // (and therefore no synchronous setState on mount).
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem(ONBOARDING_SEEN_KEY) !== '1';
    } catch {
      return true;
    }
  });

  const dismiss = () => {
    try {
      localStorage.setItem(ONBOARDING_SEEN_KEY, '1');
    } catch { /* storage unavailable */ }
    setOpen(false);
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
      <div className="bg-gray-50 rounded-2xl shadow-2xl w-full max-w-2xl max-h-[90vh] overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-start justify-between px-6 py-5 bg-gradient-to-r from-indigo-600 to-violet-600 text-white">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-white/20 flex items-center justify-center">
              <Rocket className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-lg font-bold">{t('onboardingModal.welcomeToAgentsHub')}</h2>
              <p className="text-sm text-white/80">
                {t('onboardingModal.subtitle')}
              </p>
            </div>
          </div>
          <button
            onClick={dismiss}
            aria-label={t('onboardingModal.close')}
            className="p-1.5 rounded-lg hover:bg-white/20 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5 overflow-y-auto">
          <OnboardingChecklist onNavigate={dismiss} />
        </div>

        {/* Footer — one row: the primary action, the docs link, and skip last.
            No wrapping, so the three controls always read as a single line. */}
        <div className="px-6 py-4 border-t border-gray-200 bg-white flex items-center gap-2">
          <button
            onClick={() => { dismiss(); navigate('/docs/getting-started'); }}
            className="flex items-center gap-1.5 shrink-0 text-sm font-semibold px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 transition-colors"
          >
            <BookOpen className="w-4 h-4" />
            {t('onboardingModal.openGuide')}
          </button>
          <button
            onClick={() => { dismiss(); navigate('/docs'); }}
            className="text-sm font-medium text-indigo-600 hover:text-indigo-800 px-3 py-2 rounded-lg hover:bg-indigo-50 whitespace-nowrap"
          >
            {t('onboardingModal.browseFullDocumentation')}
          </button>
          <button
            onClick={dismiss}
            className="ml-auto shrink-0 text-sm font-medium text-gray-500 hover:text-gray-800 px-4 py-2 rounded-lg hover:bg-gray-100"
          >
            {t('onboardingModal.skipForNow')}
          </button>
        </div>
      </div>
    </div>
  );
}
