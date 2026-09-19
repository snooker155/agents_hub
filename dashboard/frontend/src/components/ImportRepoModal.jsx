import { useState, useEffect } from 'react';
import { GitBranch, Github, Gitlab, Loader, Lock, RefreshCw, Search, X } from 'lucide-react';
import { Link } from 'react-router-dom';
import { getGitConfig, listGitRepos, importProjectFromRepo, connectProjectRepo } from '../api';
import { useI18n } from '../i18n';

const inputCls = "w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-400 focus:outline-none";

/**
 * Repo picker modal with two modes:
 *  - mode="import": import a repo as a new project (needs `workspaces` + `defaultWorkspace`)
 *  - mode="connect": connect an existing project to a repo (needs `project`)
 * onDone(result) is called with the backend response after a successful import/connect.
 */
export default function ImportRepoModal({ mode = 'import', project = null, workspaces = [], defaultWorkspace = '', onClose, onDone }) {
  const { t } = useI18n();
  const [gitConfig, setGitConfig] = useState(null);
  const [provider, setProvider] = useState('');
  const [search, setSearch] = useState('');
  const [repos, setRepos] = useState([]);
  const [loadingRepos, setLoadingRepos] = useState(false);
  const [selectedRepo, setSelectedRepo] = useState(null);

  const [projectName, setProjectName] = useState('');
  const [workspace, setWorkspace] = useState(defaultWorkspace || '');
  const [branch, setBranch] = useState('');
  const [importIssues, setImportIssues] = useState(true);

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    (async () => {
      try {
        const { data } = await getGitConfig();
        setGitConfig(data);
        const first = ['github', 'gitlab'].find((p) => data[p]?.has_token);
        if (first) setProvider(first);
      } catch (e) {
        setError(e.response?.data?.detail || e.message);
        setGitConfig({ github: { has_token: false }, gitlab: { has_token: false } });
      }
    })();
  }, []);

  // Load repos when the provider changes or the search is submitted (debounced).
  useEffect(() => {
    if (!provider) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      setLoadingRepos(true);
      setError('');
      try {
        const { data } = await listGitRepos(provider, search.trim() || undefined);
        if (!cancelled) setRepos(data || []);
      } catch (e) {
        if (!cancelled) {
          setRepos([]);
          setError(e.response?.data?.detail || e.message);
        }
      } finally {
        if (!cancelled) setLoadingRepos(false);
      }
    }, 350);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [provider, search]);

  const selectRepo = (repo) => {
    setSelectedRepo(repo);
    setBranch(repo.default_branch || 'main');
    if (mode === 'import') setProjectName(repo.name || '');
  };

  const handleSubmit = async () => {
    if (!selectedRepo) return;
    setSubmitting(true);
    setError('');
    try {
      let result;
      if (mode === 'connect') {
        const { data } = await connectProjectRepo(project.id, {
          provider,
          remote_id: selectedRepo.remote_id,
          branch: branch.trim() || undefined,
          import_issues: importIssues,
        });
        result = data;
      } else {
        const { data } = await importProjectFromRepo({
          provider,
          remote_id: selectedRepo.remote_id,
          name: projectName.trim() || undefined,
          workspace,
          branch: branch.trim() || undefined,
          import_issues: importIssues,
        });
        result = data;
      }
      onDone(result);
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setSubmitting(false);
    }
  };

  const availableProviders = ['github', 'gitlab'].filter((p) => gitConfig?.[p]?.has_token);
  const canSubmit = selectedRepo && !submitting && (mode === 'connect' || workspace);

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto flex flex-col">
        <div className="p-6 border-b border-gray-100 flex items-center justify-between">
          <h2 className="text-lg font-bold text-gray-900">
            {mode === 'connect' ? t('importRepoModal.connectProject', { name: project?.name }) : t('importRepoModal.importProjectFromRepository')}
          </h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600"><X className="w-5 h-5" /></button>
        </div>

        <div className="p-6 space-y-4 flex-1">
          {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{error}</div>}

          {gitConfig === null ? (
            <div className="flex items-center justify-center py-8">
              <RefreshCw className="w-5 h-5 animate-spin text-indigo-500" />
            </div>
          ) : availableProviders.length === 0 ? (
            <div className="bg-amber-50 border border-amber-200 text-amber-800 rounded-lg px-4 py-3 text-sm">
              No git connector is configured yet. Add a GitHub or GitLab personal access token in{' '}
              <Link to="/settings/git" className="font-medium underline" onClick={onClose}>{t('importRepoModal.settingsGit')}</Link>.
            </div>
          ) : (
            <>
              <div className="flex gap-2">
                {availableProviders.map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => { setProvider(p); setSelectedRepo(null); }}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium border ${
                      provider === p ? 'bg-indigo-50 border-indigo-300 text-indigo-700' : 'border-gray-300 text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    {p === 'github' ? <Github className="w-4 h-4" /> : <Gitlab className="w-4 h-4" />}
                    {p === 'github' ? 'GitHub' : 'GitLab'}
                  </button>
                ))}
              </div>

              <div className="relative">
                <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={t('importRepoModal.searchRepositories')}
                  className={`${inputCls} pl-9`}
                />
              </div>

              <div className="border border-gray-200 rounded-lg max-h-52 overflow-y-auto divide-y divide-gray-100">
                {loadingRepos ? (
                  <div className="flex items-center justify-center py-6 text-gray-400">
                    <Loader className="w-4 h-4 animate-spin mr-2" /> {t('importRepoModal.loadingRepositories')}
                  </div>
                ) : repos.length === 0 ? (
                  <div className="py-6 text-center text-sm text-gray-400">{t('importRepoModal.noRepositoriesFound')}</div>
                ) : (
                  repos.map((repo) => (
                    <button
                      key={repo.remote_id}
                      type="button"
                      onClick={() => selectRepo(repo)}
                      className={`w-full text-left px-3 py-2 hover:bg-gray-50 ${
                        selectedRepo?.remote_id === repo.remote_id ? 'bg-indigo-50' : ''
                      }`}
                    >
                      <div className="flex items-center gap-2 text-sm font-medium text-gray-800">
                        {repo.full_name}
                        {repo.private && <Lock className="w-3 h-3 text-gray-400" />}
                      </div>
                      {repo.description && <div className="text-xs text-gray-500 truncate">{repo.description}</div>}
                    </button>
                  ))
                )}
              </div>

              {selectedRepo && (
                <div className="space-y-3 pt-2 border-t border-gray-100">
                  {mode === 'import' && (
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-sm font-medium text-gray-700 mb-1 block">{t('importRepoModal.projectName')}</label>
                        <input type="text" value={projectName} onChange={(e) => setProjectName(e.target.value)} className={inputCls} />
                      </div>
                      <div>
                        <label className="text-sm font-medium text-gray-700 mb-1 block">{t('importRepoModal.workspace')}</label>
                        <select value={workspace} onChange={(e) => setWorkspace(e.target.value)} className={inputCls}>
                          <option value="">{t('importRepoModal.selectWorkspace')}</option>
                          {workspaces.map((w) => <option key={w} value={w}>{w}</option>)}
                        </select>
                      </div>
                    </div>
                  )}
                  <div className="grid grid-cols-2 gap-3 items-end">
                    <div>
                      <label className="text-sm font-medium text-gray-700 mb-1 block">{t('importRepoModal.branch')}</label>
                      <div className="relative">
                        <GitBranch className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                        <input type="text" value={branch} onChange={(e) => setBranch(e.target.value)} className={`${inputCls} pl-9`} />
                      </div>
                    </div>
                    <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer pb-2">
                      <input
                        type="checkbox"
                        checked={importIssues}
                        onChange={(e) => setImportIssues(e.target.checked)}
                        className="accent-indigo-600"
                      />
                      {t('importRepoModal.importIssuesAsTasks')}
                    </label>
                  </div>
                </div>
              )}
            </>
          )}
        </div>

        <div className="px-6 py-4 border-t border-gray-100 flex justify-end gap-3">
          <button
            onClick={onClose}
            disabled={submitting}
            className="px-4 py-2 rounded-lg text-sm font-medium text-gray-600 hover:bg-gray-100 disabled:opacity-50"
          >
            {t('importRepoModal.cancel')}
          </button>
          <button
            onClick={handleSubmit}
            disabled={!canSubmit}
            className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
          >
            {submitting && <Loader className="w-4 h-4 animate-spin" />}
            {submitting
              ? t('importRepoModal.cloningRepository')
              : mode === 'connect' ? t('importRepoModal.connectRepository') : t('importRepoModal.importProject')}
          </button>
        </div>
      </div>
    </div>
  );
}
