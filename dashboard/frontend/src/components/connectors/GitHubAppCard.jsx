import { useCallback, useEffect, useState } from 'react';
import { ExternalLink, Github, Loader, RefreshCw, Unlink } from 'lucide-react';
import {
  getGitHubApp, getWorkspaces, setWorkspaceGitHubInstallation, syncGitHubApp,
} from '../../api';
import { SectionCard, inputCls } from '../settingsUi';
import { useI18n } from '../../i18n';

const btnPrimary = 'flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white '
  + 'px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50';
const btnGhost = 'flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 '
  + 'rounded-lg text-sm font-medium text-gray-700 disabled:opacity-50';

/**
 * The GitHub App on the Git connector page (routes/github_app.py,
 * docs/github-app.md): whether it is configured, the install link, and the
 * installations with the workspace each one is bound to. A bound
 * installation is what a run in that workspace receives as GITHUB_TOKEN when
 * its agent declares the name and no explicit secret holds one.
 *
 * A 403 (a non-admin in multi mode) hides the card rather than showing an
 * error: installations are an administrator's concern.
 */
export default function GitHubAppCard() {
  const { t } = useI18n();
  const [app, setApp] = useState(null);
  const [hidden, setHidden] = useState(false);
  const [workspaces, setWorkspaces] = useState([]);
  const [error, setError] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [busyId, setBusyId] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await getGitHubApp();
      setApp(data);
      setError('');
    } catch (err) {
      if (err?.response?.status === 403 || err?.response?.status === 404) setHidden(true);
      else setError(err?.response?.data?.detail || t('githubApp.loadFailed'));
    }
  }, [t]);

  useEffect(() => {
    load();
    getWorkspaces()
      .then(({ data }) => setWorkspaces((Array.isArray(data) ? data : []).map((w) => w.name).filter(Boolean)))
      .catch(() => setWorkspaces([]));
  }, [load]);

  const sync = async () => {
    setSyncing(true);
    try {
      const { data } = await syncGitHubApp();
      setApp(data);
      setError('');
    } catch (err) {
      setError(err?.response?.data?.detail || t('githubApp.syncFailed'));
    } finally {
      setSyncing(false);
    }
  };

  const bind = async (installation, workspace) => {
    setBusyId(installation.installation_id);
    try {
      if (workspace) await setWorkspaceGitHubInstallation(workspace, installation.installation_id);
      else if (installation.workspace) await setWorkspaceGitHubInstallation(installation.workspace, null);
      await load();
    } catch (err) {
      setError(err?.response?.data?.detail || t('githubApp.bindFailed'));
    } finally {
      setBusyId(null);
    }
  };

  if (hidden) return null;

  const installations = app?.installations || [];

  return (
    <SectionCard
      title={t('githubApp.title')}
      actions={app?.configured ? (
        <div className="flex items-center gap-2">
          {app.install_url && (
            <a href={app.install_url} target="_blank" rel="noreferrer" className={btnPrimary}>
              <ExternalLink className="w-3.5 h-3.5" /> {t('githubApp.install')}
            </a>
          )}
          <button type="button" onClick={sync} disabled={syncing} className={btnGhost}>
            {syncing ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
            {t('githubApp.sync')}
          </button>
        </div>
      ) : null}
    >
      <p className="text-sm text-gray-600">{t('githubApp.description')}</p>
      {error && (
        <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>
      )}
      {app === null && !error ? (
        <p className="text-sm text-gray-500 flex items-center gap-2">
          <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
        </p>
      ) : app && !app.configured ? (
        <div className="text-sm text-gray-600 space-y-2">
          <p className="flex items-center gap-2">
            <Github className="w-4 h-4 text-gray-500" /> {t('githubApp.notConfigured')}
          </p>
          <p className="text-xs text-gray-500">{t('githubApp.envHint')}</p>
          <code className="block text-xs bg-gray-50 border border-gray-100 rounded px-2 py-1.5 whitespace-pre-wrap">
            {(app.env || []).join('\n')}
          </code>
        </div>
      ) : app && (
        installations.length === 0 ? (
          <p className="text-sm text-gray-500">{t('githubApp.noInstallations')}</p>
        ) : (
          <div className="overflow-x-auto -mx-2">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase tracking-wider text-gray-400">
                <tr>
                  <th className="text-left px-2 py-1.5 font-semibold">{t('githubApp.account')}</th>
                  <th className="text-left px-2 py-1.5 font-semibold">{t('githubApp.type')}</th>
                  <th className="text-left px-2 py-1.5 font-semibold">{t('githubApp.repositories')}</th>
                  <th className="text-left px-2 py-1.5 font-semibold">{t('githubApp.workspace')}</th>
                  <th className="px-2 py-1.5" />
                </tr>
              </thead>
              <tbody>
                {installations.map((inst) => (
                  <tr key={inst.installation_id} className="border-t border-gray-100">
                    <td className="px-2 py-2 font-medium text-gray-800">{inst.account_login || inst.installation_id}</td>
                    <td className="px-2 py-2 text-gray-600">
                      {t(`githubApp.types.${inst.account_type}`, { defaultValue: inst.account_type || '—' })}
                    </td>
                    <td className="px-2 py-2 text-gray-600">
                      {t(`githubApp.targets.${inst.target}`, { defaultValue: inst.target || '—' })}
                    </td>
                    <td className="px-2 py-2">
                      <select
                        aria-label={t('githubApp.bindTo', { account: inst.account_login })}
                        value={inst.workspace || ''}
                        disabled={busyId === inst.installation_id}
                        onChange={(e) => bind(inst, e.target.value)}
                        className={`${inputCls} py-1`}
                      >
                        <option value="">{t('githubApp.unbound')}</option>
                        {Array.from(new Set([...workspaces, inst.workspace].filter(Boolean))).map((name) => (
                          <option key={name} value={name}>{name}</option>
                        ))}
                      </select>
                    </td>
                    <td className="px-2 py-2 text-right">
                      {inst.workspace && (
                        <button type="button" onClick={() => bind(inst, '')}
                          disabled={busyId === inst.installation_id}
                          className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-700 disabled:opacity-50">
                          <Unlink className="w-3.5 h-3.5" /> {t('githubApp.unbind')}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </SectionCard>
  );
}
