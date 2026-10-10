/**
 * Shows the plan ``GET /api/kits/{id}`` computes for the current workspace
 * (create / update / unchanged), then installs on confirm
 * (``POST /api/kits/{id}/install``). See docs/kits.md.
 */
import { useEffect, useState } from 'react';
import { Package, X, Loader2, ArrowRight } from 'lucide-react';
import { getKit, installKit } from '../../api/kits';
import { useI18n } from '../../i18n';
import { errorDetail, useToast } from '../toast';

const ACTION_STYLES = {
  create: 'bg-green-50 text-green-700 border-green-200',
  update: 'bg-amber-50 text-amber-700 border-amber-200',
  unchanged: 'bg-gray-50 text-gray-500 border-gray-200',
};

export default function KitInstallDialog({ kit, workspace, onClose, onInstalled }) {
  const { t } = useI18n();
  const toast = useToast();
  // The kit and workspace the plan was last fetched for; loading is derived.
  const planKey = `${kit.id}|${workspace}`;
  const [loadedKey, setLoadedKey] = useState(null);
  const loading = loadedKey !== planKey;
  const [plan, setPlan] = useState(null);
  const [installing, setInstalling] = useState(false);
  const [result, setResult] = useState(null);

  useEffect(() => {
    let alive = true;
    getKit(kit.id, workspace)
      .then(({ data }) => { if (alive) setPlan(data.plan); })
      .catch((e) => alive && toast.error(t('marketplace.kits.planFailed'), errorDetail(e)))
      .finally(() => alive && setLoadedKey(planKey));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kit.id, workspace, planKey]);

  const submit = async () => {
    setInstalling(true);
    try {
      const { data } = await installKit(kit.id, { workspace });
      setResult(data);
      toast.success(t('marketplace.kits.installed', { kit: kit.name }));
      onInstalled?.(data);
    } catch (e) {
      toast.error(t('marketplace.kits.installFailed'), errorDetail(e));
    } finally {
      setInstalling(false);
    }
  };

  const changes = plan?.changes || [];
  const createdAgents = (result?.agents || []).filter((id) => changes.some(
    (c) => c.kind === 'agent' && c.hub_id === id));

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" role="dialog"
      aria-modal="true" aria-label={kit.name}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <h3 className="text-lg font-bold text-gray-800 flex items-center gap-2">
            <Package className="w-5 h-5 text-indigo-600" /> {kit.name}
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label={t('common.close')}>
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-6 space-y-4">
          {loading ? (
            <div className="flex items-center justify-center py-8 text-gray-400">
              <Loader2 className="w-5 h-5 animate-spin" />
            </div>
          ) : result ? (
            <div className="space-y-3">
              <p className="text-sm text-gray-700">
                {t('marketplace.kits.installedInto', { workspace: workspace || 'default' })}
              </p>
              {createdAgents.length > 0 && (
                <div className="flex flex-wrap gap-2">
                  {createdAgents.map((id) => (
                    <a key={id} href={`/agents/${encodeURIComponent(id)}`}
                      className="inline-flex items-center gap-1 text-xs font-semibold text-indigo-600 hover:underline">
                      {id} <ArrowRight className="w-3 h-3" />
                    </a>
                  ))}
                </div>
              )}
              {kit.next_steps && <p className="text-xs text-gray-500 whitespace-pre-line">{kit.next_steps}</p>}
            </div>
          ) : (
            <div className="space-y-2">
              <div className="text-xs uppercase font-semibold text-gray-400">{t('marketplace.kits.plan')}</div>
              {changes.length === 0 ? (
                <p className="text-sm text-gray-500">{t('marketplace.kits.nothingToDo')}</p>
              ) : (
                <ul className="space-y-1">
                  {changes.map((c) => (
                    <li key={c.address} className="flex items-center gap-2 text-xs">
                      <span className={`px-1.5 py-0.5 rounded border font-semibold ${
                        ACTION_STYLES[c.action] || 'bg-gray-50 text-gray-500 border-gray-200'}`}>
                        {c.action}
                      </span>
                      <span className="text-gray-600 truncate">{c.address}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
        <div className="flex justify-end gap-2 px-6 py-4 border-t border-gray-100">
          <button onClick={onClose}
            className="px-4 py-2 text-sm font-semibold border border-gray-200 text-gray-600 rounded-lg hover:bg-gray-50">
            {result ? t('common.close') : t('marketplace.kits.cancel')}
          </button>
          {!result && (
            <button onClick={submit} disabled={installing || loading}
              className="px-4 py-2 text-sm font-semibold bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 inline-flex items-center gap-2">
              {installing && <Loader2 className="w-4 h-4 animate-spin" />}
              {t('marketplace.kits.install')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
