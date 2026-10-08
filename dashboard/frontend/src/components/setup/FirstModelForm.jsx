/**
 * The one mandatory step in the browser, before the assistant can think at
 * all: connecting the hub's first model (routes/setup_guide.py POST
 * /setup-guide/model). Shown by the welcome window while `needs_model` is
 * true; `onDone` hands the server's answer back so the caller can move past
 * this step.
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader2, Plug } from 'lucide-react';
import { useI18n } from '../../i18n';
import { inputCls } from '../settingsUi';
import { connectFirstModel } from '../../api/setupGuide';

// A local provider needs no key, and its address replaces one (the field
// is hidden for it); OpenAI's address is an optional override, same as on
// the Settings page.
const PROVIDERS = [
  { id: 'openai', local: false, baseUrl: true },
  { id: 'anthropic', local: false, baseUrl: false },
  { id: 'google', local: false, baseUrl: false },
  { id: 'ollama', local: true, baseUrl: true, placeholder: 'http://localhost:11434' },
  { id: 'lmstudio', local: true, baseUrl: true, placeholder: 'http://localhost:1234' },
];

function describeError(err, t) {
  if (err?.response?.status === 403) return t('setupGuide.firstModel.errors.forbidden');
  const detail = err?.response?.data?.detail;
  const code = detail && typeof detail === 'object' ? detail.code : '';
  const message = detail && typeof detail === 'object' ? detail.message
    : (typeof detail === 'string' ? detail : '');
  if (code) return t(`setupGuide.firstModel.errors.${code}`, { defaultValue: message || t('setupGuide.firstModel.errors.failed') });
  return message || err.message || t('setupGuide.firstModel.errors.failed');
}

export default function FirstModelForm({ admin = true, onDone }) {
  const { t } = useI18n();
  const [provider, setProvider] = useState('openai');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState('');

  // Nobody but an administrator may change what the whole hub thinks with.
  if (!admin) {
    return <p className="text-sm text-gray-600" data-testid="first-model-admin-only">{t('setupGuide.firstModel.adminOnly')}</p>;
  }

  const current = PROVIDERS.find((p) => p.id === provider) || PROVIDERS[0];
  const canConnect = current.local || apiKey.trim().length > 0;

  const connect = async () => {
    if (!canConnect || connecting) return;
    setConnecting(true);
    setError('');
    try {
      const { data } = await connectFirstModel({
        provider, api_key: apiKey.trim(), base_url: baseUrl.trim(),
      });
      onDone?.(data);
    } catch (e) {
      setError(describeError(e, t));
    } finally {
      setConnecting(false);
    }
  };

  return (
    <div className="space-y-3" data-testid="first-model-form">
      <div>
        <label htmlFor="first-model-provider" className="block text-sm font-medium text-gray-700">
          {t('setupGuide.firstModel.provider')}
        </label>
        <select
          id="first-model-provider"
          value={provider}
          onChange={(e) => { setProvider(e.target.value); setError(''); }}
          className={inputCls}
        >
          {PROVIDERS.map((p) => (
            <option key={p.id} value={p.id}>{t(`setupGuide.firstModel.providers.${p.id}`)}</option>
          ))}
        </select>
      </div>
      {!current.local && (
        <div>
          <label htmlFor="first-model-key" className="block text-sm font-medium text-gray-700">
            {t('setupGuide.firstModel.apiKey')}
          </label>
          <input
            id="first-model-key"
            type="password"
            autoComplete="new-password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            className={inputCls}
          />
        </div>
      )}
      {current.baseUrl && (
        <div>
          <label htmlFor="first-model-base-url" className="block text-sm font-medium text-gray-700">
            {t('setupGuide.firstModel.baseUrl')}
          </label>
          <input
            id="first-model-base-url"
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={current.placeholder}
            className={inputCls}
          />
        </div>
      )}
      <button
        type="button"
        onClick={connect}
        disabled={connecting || !canConnect}
        className="flex items-center gap-1.5 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
      >
        {connecting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plug className="w-4 h-4" />}
        {connecting ? t('setupGuide.firstModel.connecting') : t('setupGuide.firstModel.connect')}
      </button>
      {error && <p className="text-sm text-red-600" role="alert">{error}</p>}
      <p className="text-xs text-gray-500">
        {t('setupGuide.firstModel.runtimeHint')}{' '}
        <Link to="/models?tab=local" className="text-indigo-600 hover:text-indigo-800 underline">
          {t('setupGuide.firstModel.runtimeLink')}
        </Link>
      </p>
    </div>
  );
}
