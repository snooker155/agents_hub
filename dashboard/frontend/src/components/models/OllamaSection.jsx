import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { RefreshCw, Loader, Trash2, AlertCircle, Download } from 'lucide-react';
import { getOllamaModels, pullOllamaModel, deleteOllamaModel } from '../../api/localModels';
import { humanBytes } from './jobs';
import { useI18n } from '../../i18n';
import { useToast, errorDetail } from '../toast';

const inputCls = 'border border-gray-300 rounded-lg px-2 py-1.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none';

/**
 * The external Ollama server this hub was pointed at (Settings has the base
 * URL). Shows its models, lets the operator pull a new one and delete an old
 * one; `refreshKey` bumps to reload after a pull job (tracked in the shared
 * job list up in LocalTab) finishes.
 */
export default function OllamaSection({ refreshKey, onJobStarted }) {
  const { t } = useI18n();
  const toast = useToast();
  const [state, setState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [pullName, setPullName] = useState('');
  const [pulling, setPulling] = useState(false);
  const [deletingName, setDeletingName] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await getOllamaModels();
      setState(data || { ok: false });
    } catch (e) {
      setState({ ok: false, error: errorDetail(e) });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load, refreshKey]);

  const handlePull = async (e) => {
    e.preventDefault();
    const name = pullName.trim();
    if (!name || pulling) return;
    setPulling(true);
    try {
      await pullOllamaModel(name);
      setPullName('');
      toast.success(t('localModels.ollama.pullStarted', { name }));
      onJobStarted?.();
    } catch (e2) {
      toast.error(t('localModels.ollama.pullFailed'), errorDetail(e2));
    } finally {
      setPulling(false);
    }
  };

  const handleDelete = async (name) => {
    if (!window.confirm(t('localModels.ollama.confirmDelete', { name }))) return;
    setDeletingName(name);
    try {
      await deleteOllamaModel(name);
      toast.success(t('localModels.ollama.deleted', { name }));
      load();
    } catch (e) {
      toast.error(t('localModels.ollama.deleteFailed'), errorDetail(e));
    } finally {
      setDeletingName('');
    }
  };

  const ok = state?.ok !== false;
  const models = state?.models || [];

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100 bg-gray-50 flex-wrap">
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
          <span className="text-sm font-semibold text-gray-800">{t('localModels.ollama.title')}</span>
          {state?.base_url && <span className="text-xs text-gray-400 font-mono truncate">{state.base_url}</span>}
          {ok && state && (
            <span className="text-xs text-gray-400">{t('localModels.ollama.diskUsed', { size: humanBytes(state.disk_bytes) || '0 B' })}</span>
          )}
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-xs font-medium transition-colors shrink-0"
        >
          {loading ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          {t('common.refresh')}
        </button>
      </div>

      <div className="p-3">
        {loading && !state ? (
          <p className="text-sm text-gray-500 flex items-center gap-2"><Loader className="w-4 h-4 animate-spin" /> {t('localModels.loading')}</p>
        ) : !ok ? (
          <div className="text-sm space-y-1.5">
            <p className="flex items-center gap-1.5 text-red-700"><AlertCircle className="w-4 h-4 shrink-0" /> {state?.error || t('localModels.ollama.unreachable')}</p>
            <p className="text-xs text-gray-500">
              {t('localModels.ollama.startHint')} <Link to="/settings" className="text-indigo-600 hover:underline">{t('localModels.ollama.settingsLink')}</Link>.
            </p>
          </div>
        ) : (
          <>
            <form onSubmit={handlePull} className="flex items-center gap-2 mb-3 flex-wrap">
              <input
                type="text"
                value={pullName}
                onChange={(e) => setPullName(e.target.value)}
                placeholder={t('localModels.ollama.pullPlaceholder')}
                className={`${inputCls} flex-1 min-w-40 max-w-xs`}
              />
              <button
                type="submit"
                disabled={pulling || !pullName.trim()}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 text-xs font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {pulling ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
                {t('localModels.ollama.pull')}
              </button>
            </form>

            {models.length === 0 ? (
              <p className="text-sm text-gray-400 px-1 py-2">{t('localModels.ollama.noModels')}</p>
            ) : (
              <div className="overflow-x-auto border border-gray-100 rounded-lg">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-gray-400 uppercase tracking-wider border-b border-gray-100 bg-gray-50">
                      <th className="px-2 py-1.5 font-medium">{t('localModels.ollama.name')}</th>
                      <th className="px-2 py-1.5 font-medium">{t('localModels.ollama.family')}</th>
                      <th className="px-2 py-1.5 font-medium">{t('localModels.ollama.paramSize')}</th>
                      <th className="px-2 py-1.5 font-medium">{t('localModels.ollama.quantization')}</th>
                      <th className="px-2 py-1.5 font-medium">{t('localModels.ollama.size')}</th>
                      <th className="px-2 py-1.5 font-medium">{t('localModels.ollama.modified')}</th>
                      <th className="px-2 py-1.5 font-medium w-20"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {models.map((m) => (
                      <tr key={m.name} className="border-t border-gray-50 hover:bg-gray-50">
                        <td className="px-2 py-1.5 font-mono text-gray-700">{m.name}</td>
                        <td className="px-2 py-1.5 text-gray-500">{m.family || <span className="text-gray-300">{t('common.none')}</span>}</td>
                        <td className="px-2 py-1.5 text-gray-500">{m.parameter_size || <span className="text-gray-300">{t('common.none')}</span>}</td>
                        <td className="px-2 py-1.5 text-gray-500">{m.quantization_level || <span className="text-gray-300">{t('common.none')}</span>}</td>
                        <td className="px-2 py-1.5 text-gray-500 tabular-nums">{humanBytes(m.size) || <span className="text-gray-300">{t('common.none')}</span>}</td>
                        <td className="px-2 py-1.5 text-gray-500">{m.modified_at ? new Date(m.modified_at).toLocaleDateString() : <span className="text-gray-300">{t('common.none')}</span>}</td>
                        <td className="px-2 py-1.5">
                          <div className="flex items-center justify-end gap-2">
                            <Link
                              to={`/models/ollama/${encodeURIComponent(m.name)}`}
                              title={t('localModels.structure')}
                              className="text-xs font-medium text-gray-400 hover:text-indigo-600"
                            >
                              {t('localModels.structureShort')}
                            </Link>
                            <button
                              type="button"
                              onClick={() => handleDelete(m.name)}
                              disabled={deletingName === m.name}
                              title={t('common.delete')}
                              className="text-gray-300 hover:text-red-500 disabled:opacity-50"
                            >
                              {deletingName === m.name ? <Loader className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
