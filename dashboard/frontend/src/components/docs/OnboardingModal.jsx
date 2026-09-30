import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Rocket, X, BookOpen, Compass } from 'lucide-react';
import OnboardingChecklist from './OnboardingChecklist';
import { useWelcomeTour } from './WelcomeTour';
import { useWorkspace } from '../workspace';
import { getDemo } from '../../api/demo';
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
  const tour = useWelcomeTour();
  const workspace = useWorkspace();
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

  // The tour reads best over data. When the demo workspace is seeded, switch
  // to it first; an unreachable backend or no demo just tours what is there.
  const startTour = async () => {
    dismiss();
    try {
      const { data } = await getDemo();
      if (data?.present && workspace?.setSelectedWorkspace) {
        workspace.setSelectedWorkspace(data.workspace || 'demo');
      }
    } catch { /* no demo endpoint: tour the current workspace */ }
    tour.start();
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

        {/* Footer: the tour first as the primary action, then the guide, and
            skip last. Three controls fit one row; the row still wraps on a
            very narrow screen instead of spilling past the modal edge. */}
        <div className="px-6 py-4 border-t border-gray-200 bg-white flex flex-wrap items-center gap-2">
          <button
            onClick={startTour}
            className="flex items-center gap-1.5 whitespace-nowrap text-sm font-semibold px-4 py-2 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 transition-colors"
          >
            <Compass className="w-4 h-4" />
            {t('onboardingModal.startTour')}
          </button>
          <button
            onClick={() => { dismiss(); navigate('/docs/getting-started'); }}
            className="flex items-center gap-1.5 whitespace-nowrap text-sm font-semibold px-4 py-2 rounded-lg border border-indigo-200 text-indigo-700 hover:bg-indigo-50 transition-colors"
          >
            <BookOpen className="w-4 h-4" />
            {t('onboardingModal.openGuide')}
          </button>
          <button
            onClick={dismiss}
            className="ml-auto whitespace-nowrap text-sm font-medium text-gray-500 hover:text-gray-800 px-4 py-2 rounded-lg hover:bg-gray-100"
          >
            {t('onboardingModal.skipForNow')}
          </button>
        </div>
      </div>
    </div>
  );
}
