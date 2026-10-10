/**
 * Skill sources: public repositories of Agent Skills a workspace can connect
 * in one click (memory/skill_sources.py). A source is a clone in the
 * workspace's hidden .skills/sources folder, not a project; its skills are
 * synced and reviewed like any repository skill, Update pulls and syncs
 * again, Disconnect deletes the clone and the catalog entries it brought. The curated list has licenses checked by hand; any other https
 * repository can be connected by URL and goes through the same review.
 */
import { useCallback, useEffect, useState } from 'react';
import { Library, X, RefreshCw, Plus, ShieldAlert, Scale, Terminal, Check, Unlink } from 'lucide-react';
import { addSkillSource, listSkillSources, removeSkillSource, updateSkillSource } from '../../api/skillVersions';
import { useFormatters, useI18n } from '../../i18n';
import { errorDetail, useToast } from '../toast';

export default function SkillSourcesModal({ workspace, onClose, onChanged }) {
  const { t } = useI18n();
  const { formatDate } = useFormatters();
  const toast = useToast();
  const [sources, setSources] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(null); // url being connected, updated or disconnected
  const [url, setUrl] = useState('');
  const [branch, setBranch] = useState('');

  // Promise chain rather than try/await: the lint rule cannot tell that no
  // state is set before the first await of an async function with a catch.
  // The effect calls this directly (`loading` starts true); reloads after an
  // action go through `load`, which raises the busy flag first.
  const fetchSources = useCallback(() => (
    listSkillSources(workspace)
      .then(({ data }) => setSources(data || []))
      .catch((e) => toast.error(t('skillsCatalog.sources.loadFailed'), errorDetail(e)))
      .finally(() => setLoading(false))
  ), [workspace, t, toast]);

  const load = useCallback(() => {
    setLoading(true);
    return fetchSources();
  }, [fetchSources]);

  useEffect(() => { fetchSources(); }, [fetchSources]);

  const connect = async (sourceUrl, sourceBranch = '') => {
    setBusy(sourceUrl);
    try {
      const { data } = await addSkillSource(workspace, sourceUrl, sourceBranch);
      const count = (key) => (data.sync?.[key] || []).length;
      const repo = sourceUrl.replace(/^https:\/\/[^/]+\//, '');
      toast.success(t(data.already_present ? 'skillsCatalog.sources.alreadyPresent' : 'skillsCatalog.sources.done', {
        repo, added: count('added'), updated: count('updated'), flagged: count('flagged'),
      }));
      if (sourceUrl === url) { setUrl(''); setBranch(''); }
      await load();
      onChanged?.(data);
    } catch (e) {
      toast.error(t('skillsCatalog.sources.failed'), errorDetail(e));
    } finally {
      setBusy(null);
    }
  };

  const count = (report, key) => (report?.[key] || []).length;

  const update = async (s) => {
    setBusy(s.url);
    try {
      const { data } = await updateSkillSource(workspace, s.source_id);
      toast.success(t('skillsCatalog.sources.updated', {
        repo: s.repo, added: count(data.sync, 'added'), updated: count(data.sync, 'updated'),
        missing: count(data.sync, 'missing'), flagged: count(data.sync, 'flagged'),
      }));
      await load();
      onChanged?.(data);
    } catch (e) {
      toast.error(t('skillsCatalog.sources.updateFailed'), errorDetail(e));
    } finally {
      setBusy(null);
    }
  };

  const remove = async (s) => {
    if (!window.confirm(t('skillsCatalog.sources.confirmRemove', { repo: s.repo, count: s.skills || 0 }))) return;
    setBusy(s.url);
    try {
      const { data } = await removeSkillSource(workspace, s.source_id);
      toast.success(t('skillsCatalog.sources.removed', { repo: s.repo, count: (data.removed || []).length }));
      await load();
      onChanged?.(data);
    } catch (e) {
      toast.error(t('skillsCatalog.sources.removeFailed'), errorDetail(e));
    } finally {
      setBusy(null);
    }
  };

  const inputCls = 'w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none';

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" role="dialog"
      aria-modal="true" aria-label={t('skillsCatalog.sources.title')}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-3xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <h3 className="text-lg font-bold text-gray-800 flex items-center gap-2">
            <Library className="w-5 h-5 text-indigo-600" /> {t('skillsCatalog.sources.title')}
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label={t('common.close')}>
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-6 space-y-4">
          <p className="text-xs text-gray-500">{t('skillsCatalog.sources.hint')}</p>
          <p className="text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2 flex gap-2">
            <ShieldAlert className="w-4 h-4 shrink-0 mt-0.5" /> {t('skillsCatalog.sources.warning')}
          </p>

          {loading ? (
            <div className="text-sm text-gray-400 py-6 text-center">{t('skillsCatalog.history.loading')}</div>
          ) : (
            <ul className="divide-y divide-gray-100 border border-gray-100 rounded-lg">
              {sources.map((s) => {
                const connected = Boolean(s.source_id);
                return (
                  <li key={s.id} className="px-4 py-3 flex items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <a href={s.url} target="_blank" rel="noreferrer" className="text-sm font-semibold text-gray-900 hover:underline truncate">
                          {s.repo}
                        </a>
                        <span className="text-[10px] uppercase font-semibold text-gray-500 bg-gray-100 px-1.5 py-0.5 rounded">
                          {t(`skillsCatalog.sources.kind.${s.kind}`)}
                        </span>
                        {s.license && (
                          <span className="inline-flex items-center gap-1 text-[11px] text-gray-500" title={t('skillsCatalog.license.title')}>
                            <Scale className="w-3 h-3" /> {s.license}
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-gray-500 mt-0.5">{s.publisher}{s.note ? ` · ${s.note}` : ''}</div>
                      {connected && (
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] mt-1">
                          <span className="inline-flex items-center gap-1 text-emerald-700">
                            <Check className="w-3 h-3" /> {t('skillsCatalog.sources.connected')} · {t('skillsCatalog.sources.skillsCount', { count: s.skills || 0 })}
                          </span>
                          {(s.branch || s.updated_at) && (
                            <span className="text-gray-400">
                              {[s.branch, s.updated_at && t('skillsCatalog.sources.updatedAt', { date: formatDate(s.updated_at) })].filter(Boolean).join(' · ')}
                            </span>
                          )}
                          {s.flagged > 0 && (
                            <span className="inline-flex items-center gap-1 text-red-600">
                              <ShieldAlert className="w-3 h-3" /> {t('skillsCatalog.sources.flaggedCount', { count: s.flagged })}
                            </span>
                          )}
                          {s.scripts > 0 && (
                            <span className="inline-flex items-center gap-1 text-amber-700">
                              <Terminal className="w-3 h-3" /> {t('skillsCatalog.sources.scriptsCount', { count: s.scripts })}
                            </span>
                          )}
                          {s.not_open > 0 && (
                            <span className="inline-flex items-center gap-1 text-amber-700">
                              <Scale className="w-3 h-3" /> {t('skillsCatalog.sources.notOpenCount', { count: s.not_open })}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                    {connected ? (
                      <div className="shrink-0 flex items-center gap-2">
                        <button
                          onClick={() => update(s)}
                          disabled={busy !== null}
                          className="inline-flex items-center px-3 py-1.5 text-xs font-semibold rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 transition-colors disabled:opacity-50"
                        >
                          <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${busy === s.url ? 'animate-spin' : ''}`} />
                          {t('skillsCatalog.sources.update')}
                        </button>
                        <button
                          onClick={() => remove(s)}
                          disabled={busy !== null}
                          title={t('skillsCatalog.sources.remove')}
                          aria-label={t('skillsCatalog.sources.remove')}
                          className="inline-flex items-center p-1.5 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 transition-colors disabled:opacity-50"
                        >
                          <Unlink className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => connect(s.url)}
                        disabled={busy !== null}
                        className="shrink-0 inline-flex items-center px-3 py-1.5 text-xs font-semibold rounded-lg border bg-indigo-600 text-white border-indigo-600 hover:bg-indigo-700 transition-colors disabled:opacity-50"
                      >
                        {busy === s.url
                          ? <RefreshCw className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                          : <Plus className="w-3.5 h-3.5 mr-1.5" />}
                        {t('skillsCatalog.sources.connect')}
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}

          <div className="border-t border-gray-100 pt-4">
            <div className="text-xs font-semibold text-gray-700 mb-1">{t('skillsCatalog.sources.custom')}</div>
            <p className="text-[11px] text-gray-500 mb-2">{t('skillsCatalog.sources.customHint')}</p>
            <div className="flex flex-col sm:flex-row gap-2">
              <input
                type="text"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder={t('skillsCatalog.sources.urlPlaceholder')}
                aria-label={t('skillsCatalog.sources.custom')}
                className={`${inputCls} flex-1`}
              />
              <input
                type="text"
                value={branch}
                onChange={(e) => setBranch(e.target.value)}
                placeholder={t('skillsCatalog.sources.branch')}
                aria-label={t('skillsCatalog.sources.branch')}
                className={`${inputCls} sm:w-40`}
              />
              <button
                onClick={() => connect(url.trim(), branch.trim())}
                disabled={!url.trim() || busy !== null}
                className="inline-flex items-center justify-center px-3 py-2 text-xs font-semibold bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
              >
                {busy === url.trim() && url.trim()
                  ? <RefreshCw className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                  : <Plus className="w-3.5 h-3.5 mr-1.5" />}
                {t('skillsCatalog.sources.connect')}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
