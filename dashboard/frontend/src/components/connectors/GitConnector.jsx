import { useEffect, useState } from 'react';
import { RefreshCw, Save, Trash2, Wifi } from 'lucide-react';
import {
  getGitConfig, updateGitConfig, deleteGitConfig, testGitConnection,
} from '../../api';
import { SectionCard, inputCls } from '../settingsUi';
import { useI18n } from '../../i18n';
import { useWorkspace } from '../workspace';
import PageLoader from '../PageLoader';
import { ConnectorSourceBadge, ConnectorDefinedIn, ConnectorSourceActions } from './ConnectorSource';

// The GitLab connector: one token and a base URL per workspace. The strings
// still live under the `settings.*` i18n keys they were written with when the
// card sat on the Settings page; they are the same strings, moved verbatim.
//
// Per workspace, like every other connector (connectors/store.py):
// the default workspace's token works everywhere, another workspace's own
// token works only there. The config GET answers `sources` (per provider,
// "here" or "default") and, from the default workspace, `defined_in`.

const PROVIDER = 'gitlab';

function GitProviderSection({
  config, source, definedIn, isDefaultWorkspace, workspace, onSaved,
}) {
  const { t } = useI18n();
  const label = t('settings.gitlab');
  const [tokenInput, setTokenInput] = useState('');
  const [baseUrl, setBaseUrl] = useState(config.base_url || '');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState('');
  // "Define for this workspace" was clicked: the inherited, read only form
  // becomes editable, and a save is what creates this workspace's own copy.
  const [editing, setEditing] = useState(false);

  const readOnly = !isDefaultWorkspace && source === 'default' && !editing;

  const handleSave = async ({ clear_token } = {}) => {
    setSaving(true);
    setError('');
    setTestResult(null);
    try {
      const payload = { provider: PROVIDER };
      if (clear_token) payload.clear_token = true;
      else if (tokenInput.trim()) payload.token = tokenInput.trim();
      if (baseUrl.trim()) payload.base_url = baseUrl.trim();
      const { data } = await updateGitConfig(payload, workspace);
      setTokenInput('');
      setEditing(false);
      onSaved(data);
    } catch (e) {
      setError(`${t('settings.errors.save')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const { data } = await testGitConnection(PROVIDER, workspace);
      setTestResult(data);
    } catch (e) {
      setTestResult({ ok: false, error: e.response?.data?.detail || e.message });
    } finally {
      setTesting(false);
    }
  };

  const handleRemove = async () => {
    if (!window.confirm(t('connectors.source.confirmRemove'))) return;
    setSaving(true);
    setError('');
    try {
      const { data } = await deleteGitConfig(PROVIDER, workspace);
      onSaved(data);
    } catch (e) {
      setError(`${t('settings.errors.save')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setSaving(false);
    }
  };

  return (
    <SectionCard title={label} actions={<ConnectorSourceBadge source={source} />}>
      <p className="text-sm text-gray-600">{t('settings.usedToBrowseYourProjects')}</p>
      {isDefaultWorkspace ? (
        <ConnectorDefinedIn definedIn={definedIn} />
      ) : (
        <ConnectorSourceActions
          payload={{ source }}
          isDefaultWorkspace={isDefaultWorkspace}
          editing={editing}
          onDefine={() => setEditing(true)}
          onRemove={handleRemove}
          busy={saving}
        />
      )}
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{error}</div>}

      <div>
        <label className="text-sm font-medium text-gray-700 mb-1 block">{t('settings.baseUrl')}</label>
        <p className="text-xs text-gray-500 mb-1">{t('settings.changeForSelfHostedGitlab')}</p>
        <input
          type="text"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://gitlab.com"
          className={inputCls}
          disabled={readOnly}
        />
      </div>

      <div>
        <label className="text-sm font-medium text-gray-700 mb-1 block">
          {t('settings.git.personalAccessToken')} {config.has_token && <span className="text-gray-400 font-normal">({t('settings.currentlySet')})</span>}
        </label>
        <input
          type="password"
          value={tokenInput}
          onChange={(e) => setTokenInput(e.target.value)}
          placeholder={config.has_token ? t('settings.keepExistingToken') : t('settings.git.pasteToken', { provider: label })}
          className={inputCls}
          autoComplete="new-password"
          disabled={readOnly}
        />
        {!readOnly && (
          <div className="flex flex-wrap items-center gap-2 mt-2">
            <button
              type="button"
              onClick={() => handleSave({})}
              disabled={saving}
              className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
            >
              {saving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              {t('common.save')}
            </button>
            <button
              type="button"
              onClick={handleTest}
              disabled={testing || !config.has_token}
              className="flex items-center gap-1.5 border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg text-sm font-medium text-gray-700 disabled:opacity-50"
            >
              {testing ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Wifi className="w-3.5 h-3.5" />}
              {t('settings.testConnection')}
            </button>
            {config.has_token && (
              <button
                type="button"
                onClick={() => handleSave({ clear_token: true })}
                disabled={saving}
                className="flex items-center gap-1.5 border border-red-200 text-red-700 hover:bg-red-50 px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-50"
              >
                <Trash2 className="w-3.5 h-3.5" /> {t('settings.clearToken')}
              </button>
            )}
          </div>
        )}
        {testResult && !readOnly && (
          <div className={`mt-2 text-sm rounded-lg px-3 py-2 ${testResult.ok ? 'bg-green-50 border border-green-200 text-green-700' : 'bg-red-50 border border-red-200 text-red-700'}`}>
            {testResult.ok
              ? <>{t('settings.connectedAs')} <strong>{testResult.login}</strong></>
              : <>{t('settings.testFailed')}: {testResult.error}</>}
          </div>
        )}
      </div>
    </SectionCard>
  );
}

export default function GitConnector() {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const workspace = selectedWorkspace || 'default';
  const isDefaultWorkspace = workspace === 'default';
  const [loadedFor, setLoadedFor] = useState(null);
  const loading = loadedFor !== workspace;
  const [config, setConfig] = useState({
    gitlab: { has_token: false, base_url: 'https://gitlab.com' },
  });
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { data } = await getGitConfig(workspace);
        if (cancelled) return;
        setError('');
        setConfig(data);
      } catch (e) {
        if (cancelled) return;
        setError(`${t('settings.errors.gitLoad')}: ` + (e.response?.data?.detail || e.message));
      } finally {
        if (!cancelled) setLoadedFor(workspace);
      }
    })();
    return () => { cancelled = true; };
  }, [workspace, t]);

  if (loading) {
    return (
      <PageLoader size="sm" />
    );
  }

  return (
    <div className="space-y-5">
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}
      <GitProviderSection
        config={config.gitlab || {}}
        source={config.sources?.[PROVIDER]}
        definedIn={config.defined_in?.[PROVIDER]}
        isDefaultWorkspace={isDefaultWorkspace}
        workspace={workspace}
        onSaved={setConfig}
      />
    </div>
  );
}
