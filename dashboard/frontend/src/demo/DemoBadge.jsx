import { useI18n } from '../i18n';

/**
 * The "recorded data" pill of the demo build. Mounted from main.jsx next to
 * the app, only when VITE_DEMO=1, so it floats over every page without
 * Layout knowing about it.
 *
 * It also carries the only way back out of the demo: the dashboard has no
 * link to the site, and the browser's Back button is eaten by the router's
 * in-app history, so without this a visitor who came from the site is stuck.
 * The site lives one level above the demo's base path (/agents_hub/demo/ is
 * served from /agents_hub/), so the link is computed from BASE_URL rather
 * than hard coded, and a fork served from another prefix still gets it right.
 */
const SITE_URL = new URL('..', new URL(import.meta.env.BASE_URL, window.location.origin)).href;

export default function DemoBadge() {
  const { t } = useI18n();
  return (
    <div
      data-demo-badge=""
      className="fixed bottom-3 left-1/2 -translate-x-1/2 z-[60] flex items-center gap-2 select-none rounded-full border border-amber-200 bg-amber-50 py-1 pl-3 pr-1 text-xs font-semibold text-amber-800 shadow-sm"
    >
      <span role="status" title={t('demo.badgeTitle')}>{t('demo.badge')}</span>
      <a
        href={SITE_URL}
        className="rounded-full bg-amber-800 px-2.5 py-0.5 text-amber-50 no-underline hover:bg-amber-900"
      >
        {t('demo.backToSite')}
      </a>
    </div>
  );
}
