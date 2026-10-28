import { useEffect, useState } from 'react';
import { Lock, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

import { getSyncHere } from '../../api/syncVault';
import { useI18n } from '../../i18n';
import { useWorkspace } from '../workspace';

/**
 * A slim notice in a workspace that holds encrypted synced records: they stay
 * sealed here until the person opens them on the Synced data page.
 */
export default function SyncedDataBanner() {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { selectedWorkspace } = useWorkspace();
  const [info, setInfo] = useState(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let alive = true;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- a new workspace resets the previous answer
    setInfo(null);
    if (!selectedWorkspace) return undefined;
    getSyncHere(selectedWorkspace)
      .then((res) => { if (alive) setInfo(res.data); })
      .catch(() => { if (alive) setInfo(null); });
    return () => { alive = false; };
  }, [selectedWorkspace]);

  const count = info?.space?.counts?.encrypted || 0;
  if (dismissed || !info?.synced || !info.space?.encrypted || count <= 0) return null;

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-sm bg-amber-50 border-b border-amber-200 text-amber-900">
      <Lock size={14} className="shrink-0" />
      <span className="flex-1 min-w-[200px]">{t('vault.banner.text', { count })}</span>
      <button
        type="button"
        onClick={() => navigate(`/vault?space=${encodeURIComponent(selectedWorkspace)}`)}
        className="font-medium underline hover:no-underline"
      >
        {t('vault.banner.view')}
      </button>
      <button type="button" onClick={() => setDismissed(true)} aria-label={t('vault.banner.dismiss')} className="text-amber-700 hover:text-amber-900">
        <X size={14} />
      </button>
    </div>
  );
}

export { SyncedDataBanner };
