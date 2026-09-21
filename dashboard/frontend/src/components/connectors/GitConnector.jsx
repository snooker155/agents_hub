import { useCallback, useEffect, useState } from 'react';
import { RefreshCw, Save, Trash2, Wifi } from 'lucide-react';
import {
  getGitConfig, updateGitConfig, testGitConnection,
} from '../../api';
import { SectionCard, inputCls } from '../settingsUi';
import { useI18n } from '../../i18n';

// Moved out of the Settings page, which is where nobody looked for it: a
// connector is something you *attach*, so it belongs with the other things you
// attach (Connect → Connectors), not with model keys and log levels.
//
// The strings still live under the `settings.*` i18n keys they were written
// with. They are the same strings, moved verbatim; renaming a hundred keys
// across three locales in the same change as a page move would bury the move
// in the diff. That rename is mechanical and the parity test guards it, so it
// can happen on its own.

// ── Git connectors tab (GitHub / GitLab) ─────────────────────────────────────

function GitProviderSection({ provider, label, hint, config, onSaved }) {
  const { t } = useI18n();
  const [tokenInput, setTokenInput] = useState('');
  const [baseUrl, setBaseUrl] = useState(config.base_url || '');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState('');

  const isGitlab = provider === 'gitlab';

  const handleSave = async ({ clear_token } = {}) => {
    setSaving(true);
    setError('');
    setTestResult(null);
    try {
      const payload = { provider };
      if (clear_token) payload.clear_token = true;
      else if (tokenInput.trim()) payload.token = tokenInput.trim();
      if (isGitlab && baseUrl.trim()) payload.base_url = baseUrl.trim();
      const { data } = await updateGitConfig(payload);
      setTokenInput('');
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
      const { data } = await testGitConnection(provider);
      setTestResult(data);
    } catch (e) {
      setTestResult({ ok: false, error: e.response?.data?.detail || e.message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <SectionCard title={label}>
      <p className="text-sm text-gray-600">{hint}</p>
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-3 py-2 text-sm">{error}</div>}

      {isGitlab && (
        <div>
          <label className="text-sm font-medium text-gray-700 mb-1 block">{t('settings.baseUrl')}</label>
          <p className="text-xs text-gray-500 mb-1">{t('settings.changeForSelfHostedGitlab')}</p>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder="https://gitlab.com"
            className={inputCls}
          />
        </div>
      )}

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
        />
        <div className="flex flex-wrap items-center gap-2 mt-2">
          <button
            type="button"
            onClick={() => handleSave({})}
            disabled={saving || (!tokenInput.trim() && !isGitlab)}
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
        {testResult && (
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
  const [loading, setLoading] = useState(true);
  const [config, setConfig] = useState({ github: { has_token: false }, gitlab: { has_token: false, base_url: 'https://gitlab.com' } });
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const { data } = await getGitConfig();
      setConfig(data);
    } catch (e) {
      setError(`${t('settings.errors.gitLoad')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-10">
        <RefreshCw className="w-5 h-5 animate-spin text-indigo-500" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {error && <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">{error}</div>}
      <GitProviderSection
        provider="github"
        label={t('settings.github')}
        hint={t('settings.usedToBrowseYourRepos')}
        config={config.github || {}}
        onSaved={setConfig}
      />
      <GitProviderSection
        provider="gitlab"
        label={t('settings.gitlab')}
        hint={t('settings.usedToBrowseYourProjects')}
        config={config.gitlab || {}}
        onSaved={setConfig}
      />
    </div>
  );
}
