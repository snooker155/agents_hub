import { useI18n } from '../i18n';

/**
 * The "recorded data" pill of the demo build. Mounted from main.jsx next to
 * the app, only when VITE_DEMO=1, so it floats over every page without
 * Layout knowing about it.
 */
export default function DemoBadge() {
  const { t } = useI18n();
  return (
    <div
      role="status"
      data-demo-badge=""
      title={t('demo.badgeTitle')}
      className="fixed bottom-3 left-1/2 -translate-x-1/2 z-[60] pointer-events-none select-none rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-800 shadow-sm"
    >
      {t('demo.badge')}
    </div>
  );
}
