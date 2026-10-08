import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { BookOpen, Compass, Keyboard, Mic, Rocket, X } from 'lucide-react';
import { useWelcomeTour } from './WelcomeTour';
import { useWorkspace } from '../workspace';
import { getDemo } from '../../api/demo';
import useSetupGuide from '../setup/useSetupGuide';
import FirstModelForm from '../setup/FirstModelForm';
import { useI18n } from '../../i18n';

// ---------------------------------------------------------------------------
// OnboardingModal — auto-opens on first launch (tracked in localStorage) and
// is otherwise dismissed. It can always be re-opened from the Docs section.
// Mounted once, near the app root (in Layout), so it overlays every page.
//
// What it shows depends on the guided setup (useSetupGuide, docs/assistant.md
// "Guided setup"): no model connected yet, the one step the browser itself
// can do (FirstModelForm); otherwise the hand over to the assistant, which
// leads the rest of it by voice or by text.
// ---------------------------------------------------------------------------

// Versioned: bumping the suffix re-shows the welcome popup once after the
// onboarding content changes, instead of hiding it forever from anyone who
// dismissed an older version.
export const ONBOARDING_SEEN_KEY = 'agents_hub_onboarding_seen_v3';

export default function OnboardingModal() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const tour = useWelcomeTour();
  const workspace = useWorkspace();
  const { guide, act, refresh } = useSetupGuide();
  // Dismissed this launch or a past one — derived once from localStorage, so
  // no effect (and therefore no synchronous setState on mount).
  const [closed, setClosed] = useState(() => {
    try {
      return localStorage.getItem(ONBOARDING_SEEN_KEY) === '1';
    } catch {
      return false;
    }
  });

  const dismiss = () => {
    try {
      localStorage.setItem(ONBOARDING_SEEN_KEY, '1');
    } catch { /* storage unavailable */ }
    setClosed(true);
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

  // The hand over: start the guide in this mode, then let the Assistant page
  // send the first turn (its own kickoff handling reads `state.setup`).
  const startGuide = async (mode) => {
    dismiss();
    try { await act('start', { mode }); } catch { /* the hand over still works without it */ }
    navigate('/assistant', { state: { setup: { mode, kickoff: true } } });
  };

  // Nothing decided yet (the guide is still loading), dismissed already, or
  // a guide that is running or over: there is nothing left for this to open.
  if (closed || !guide || guide.active || guide.finished_at) return null;

  const needsModel = Boolean(guide.needs_model);

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
                {t(needsModel ? 'onboardingModal.needsModelSubtitle' : 'onboardingModal.handoverSubtitle')}
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

        {/* Body: the one step the browser does itself, or the hand over. */}
        <div className="px-6 py-5 overflow-y-auto">
          {needsModel ? (
            <FirstModelForm admin={guide.admin} onDone={refresh} />
          ) : (
            <div className="space-y-4">
              <p className="text-sm text-gray-600">{t('onboardingModal.chooseWay')}</p>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <button
                  onClick={() => startGuide('voice')}
                  className="flex flex-col items-center gap-2 px-4 py-6 rounded-xl border-2 border-indigo-200 bg-white hover:border-indigo-400 hover:bg-indigo-50 transition-colors"
                >
                  <Mic className="w-7 h-7 text-indigo-600" />
                  <span className="text-sm font-semibold text-gray-800">{t('onboardingModal.talkToAssistant')}</span>
                </button>
                <button
                  onClick={() => startGuide('text')}
                  className="flex flex-col items-center gap-2 px-4 py-6 rounded-xl border-2 border-indigo-200 bg-white hover:border-indigo-400 hover:bg-indigo-50 transition-colors"
                >
                  <Keyboard className="w-7 h-7 text-indigo-600" />
                  <span className="text-sm font-semibold text-gray-800">{t('onboardingModal.typeToAssistant')}</span>
                </button>
              </div>
            </div>
          )}
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
