import { Lock } from 'lucide-react';
import useWorkspaceIsolation from './useWorkspaceIsolation';
import { useI18n } from '../../i18n';

/**
 * "Isolated" next to the workspace name, wherever the currently selected
 * workspace is shown (the header's workspace picker). Nothing renders while
 * loading or when the request fails: the badge is a hint, not a status the
 * user must wait on or be warned about here (common/isolation.py `state()`).
 */
export default function IsolationBadge({ workspace }) {
  const { t } = useI18n();
  const iso = useWorkspaceIsolation(workspace || undefined);
  if (!iso.data?.isolated) return null;
  return (
    <span
      className="inline-flex items-center gap-1 text-xs font-medium text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-full px-2 py-0.5 shrink-0"
      title={t('isolation.switchLabel')}
      data-testid="isolation-badge"
    >
      <Lock className="w-3 h-3" /> {t('isolation.badge')}
    </span>
  );
}
