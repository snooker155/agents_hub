import { useCallback, useEffect, useMemo, useState } from 'react';
import { Check, Copy, Globe, GlobeLock, Loader, RefreshCw, Wifi, WifiOff } from 'lucide-react';

import {
  clearInstanceInboundSecret, getInstanceConnections, publishInstance,
  setInstanceInboundSecret, unpublishInstance,
} from '../../api';
import { useI18n } from '../../i18n';
import PageLoader from '../PageLoader';

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

function CopyButton({ text }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <button
      type="button"
      onClick={handleCopy}
      title={t('instanceDetail.access.copy')}
      className={`p-1.5 rounded transition-colors ${copied ? 'text-green-600 bg-green-50' : 'text-gray-400 hover:text-gray-700 hover:bg-gray-100'}`}
    >
      {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  );
}

const STATUS_COLOR = {
  200: 'bg-green-100 text-green-700',
  202: 'bg-green-100 text-green-700',
  400: 'bg-yellow-100 text-yellow-700',
  403: 'bg-orange-100 text-orange-700',
  404: 'bg-gray-100 text-gray-600',
  500: 'bg-red-100 text-red-700',
  503: 'bg-red-100 text-red-700',
};

/**
 * Publishing a resident instance: a public address through the hub
 * (routes/external.py), the inbound signature that guards it, and the log of
 * every call it has answered.
 */
export default function AccessTab({ instance, onInstanceUpdated, actions = null, exampleBody = null, hint = null }) {
  const { t } = useI18n();
  // A service's page reuses this card: `actions` names its own endpoints and
  // `instance` is the service record with its id under `instance_id`.
  const api = useMemo(() => actions || {
    publish: publishInstance,
    unpublish: unpublishInstance,
    setSecret: setInstanceInboundSecret,
    clearSecret: clearInstanceInboundSecret,
    connections: getInstanceConnections,
  }, [actions]);
  const [toggling, setToggling] = useState(false);
  const [publishError, setPublishError] = useState('');
  const [secretValue, setSecretValue] = useState('');
  const [secretBusy, setSecretBusy] = useState(false);
  const [secretError, setSecretError] = useState('');
  const [connections, setConnections] = useState([]);
  const [loadingConnections, setLoadingConnections] = useState(true);

  const isExposed = !!instance.is_exposed;

  const fetchConnections = useCallback(async () => {
    setLoadingConnections(true);
    try {
      const { data } = await api.connections(instance.instance_id);
      setConnections(Array.isArray(data) ? data : (data?.items || []));
    } catch {
      setConnections([]);
    } finally {
      setLoadingConnections(false);
    }
  }, [instance.instance_id, api]);

  useEffect(() => { fetchConnections(); }, [fetchConnections]);

  const handleTogglePublish = async () => {
    setToggling(true);
    setPublishError('');
    try {
      const { data } = isExposed
        ? await api.unpublish(instance.instance_id)
        : await api.publish(instance.instance_id);
      onInstanceUpdated(data);
    } catch (err) {
      setPublishError(err.response?.data?.detail || t('instanceDetail.access.publishFailed'));
    } finally {
      setToggling(false);
    }
  };

  const applySecret = async (secret) => {
    setSecretBusy(true);
    setSecretError('');
    try {
      const { data } = secret
        ? await api.setSecret(instance.instance_id, secret)
        : await api.clearSecret(instance.instance_id);
      onInstanceUpdated(data);
      setSecretValue('');
    } catch (err) {
      setSecretError(err.response?.data?.detail || t('instanceDetail.access.secretFailed'));
    } finally {
      setSecretBusy(false);
    }
  };

  const externalUrl = instance.external_url;
  const secretConfigured = !!instance.inbound_secret_configured;

  const plainBody = exampleBody || '{"message": "Hello", "conversation_id": "main"}';
  const streamBody = exampleBody
    ? exampleBody.replace(/}\s*$/, ', "stream": true}')
    : '{"message": "Hello", "stream": true}';
  const plainExample = externalUrl
    ? `curl -X POST "${externalUrl}" \\\n  -H "Content-Type: application/json" \\\n  -d '${plainBody}'`
    : '';
  const streamExample = externalUrl
    ? `curl -N -X POST "${externalUrl}" \\\n  -H "Content-Type: application/json" \\\n  -d '${streamBody}'`
    : '';
  // A 202 answer carries poll_url, which is this address plus the message id.
  const pollExample = externalUrl ? `curl "${externalUrl}/<msg_id>"` : '';

  return (
    <div className="space-y-4">
      <div className="bg-white border border-gray-200 rounded-xl p-4">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            {isExposed ? <Globe className="w-5 h-5 text-indigo-600" /> : <GlobeLock className="w-5 h-5 text-gray-400" />}
            <div>
              <h3 className="text-sm font-semibold text-gray-800">{t('instanceDetail.access.title')}</h3>
              <p className="text-xs text-gray-500 mt-0.5">
                {isExposed ? t('instanceDetail.access.exposedHint') : t('instanceDetail.access.notExposedHint')}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleTogglePublish}
            disabled={toggling}
            className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${
              isExposed ? 'bg-indigo-600' : 'bg-gray-200'
            } ${toggling ? 'opacity-50 cursor-not-allowed' : ''}`}
          >
            <span className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${isExposed ? 'translate-x-5' : 'translate-x-0'}`} />
          </button>
        </div>

        {publishError && (
          <div className="mt-3 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">{publishError}</div>
        )}
        {hint && (
          <div className="mt-3 text-xs text-gray-600 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">{hint}</div>
        )}

        {isExposed && externalUrl && (
          <div className="mt-4 space-y-3">
            <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-3">
              <p className="text-[10px] font-bold uppercase tracking-wider text-indigo-500 mb-1.5">{t('instanceDetail.access.externalUrl')}</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 text-xs text-indigo-800 break-all">{externalUrl}</code>
                <CopyButton text={externalUrl} />
              </div>
            </div>

            <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
              <p className="text-[10px] font-bold uppercase tracking-wider text-gray-500 mb-1.5">{t('instanceDetail.access.plainCall')}</p>
              <pre className="text-xs text-gray-700 whitespace-pre-wrap break-all leading-5">{plainExample}</pre>
            </div>
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
              <p className="text-[10px] font-bold uppercase tracking-wider text-gray-500 mb-1.5">{t('instanceDetail.access.streamingCall')}</p>
              <pre className="text-xs text-gray-700 whitespace-pre-wrap break-all leading-5">{streamExample}</pre>
            </div>
            <div className="bg-gray-50 border border-gray-200 rounded-lg p-3">
              <p className="text-[10px] font-bold uppercase tracking-wider text-gray-500 mb-1.5">{t('instanceDetail.access.pollCall')}</p>
              <pre className="text-xs text-gray-700 whitespace-pre-wrap break-all leading-5">{pollExample}</pre>
              <p className="text-xs text-gray-400 mt-1.5">{t('instanceDetail.access.pollHint')}</p>
            </div>

            <div className="bg-white border border-gray-200 rounded-lg p-3">
              <div className="flex items-center justify-between mb-1.5">
                <p className="text-[10px] font-bold uppercase tracking-wider text-gray-500">{t('instanceDetail.access.inboundSecret')}</p>
                <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${secretConfigured ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                  {secretConfigured ? t('instanceDetail.access.secretSet') : t('instanceDetail.access.secretNotSet')}
                </span>
              </div>
              <p className="text-xs text-gray-500 mb-2">{t('instanceDetail.access.secretHint')}</p>
              <div className="flex items-center gap-2">
                <input
                  type="password"
                  value={secretValue}
                  onChange={(e) => setSecretValue(e.target.value)}
                  placeholder={t('instanceDetail.access.newSecret')}
                  className="flex-1 border border-gray-300 rounded-lg px-3 py-1.5 text-xs focus:outline-none"
                />
                <button type="button" onClick={() => applySecret(secretValue.trim())} disabled={secretBusy || !secretValue.trim()}
                        className="text-[10px] font-semibold px-2 py-1.5 rounded border border-indigo-200 text-indigo-700 hover:bg-indigo-50 disabled:opacity-50">
                  {t('instanceDetail.access.setSecret')}
                </button>
                <button type="button" onClick={() => applySecret('')} disabled={secretBusy || !secretConfigured}
                        className="text-[10px] font-semibold px-2 py-1.5 rounded border border-gray-300 text-gray-600 hover:bg-gray-100 disabled:opacity-50">
                  {t('instanceDetail.access.clearSecret')}
                </button>
              </div>
              {secretError && <p className="mt-2 text-xs text-red-600">{secretError}</p>}
            </div>
          </div>
        )}
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
          <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-2">
            <Wifi className="w-4 h-4 text-gray-500" />
            {t('instanceDetail.access.connections')}
          </h3>
          <button type="button" onClick={fetchConnections} className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100">
            <RefreshCw className={`w-3.5 h-3.5 ${loadingConnections ? 'animate-spin' : ''}`} />
          </button>
        </div>
        {loadingConnections ? (
          <PageLoader size="sm" />
        ) : !connections.length ? (
          <div className="py-10 text-center">
            <WifiOff className="w-7 h-7 text-gray-300 mx-auto mb-2" />
            <p className="text-sm text-gray-400">{t('instanceDetail.access.noConnections')}</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 text-left text-gray-400">
                  <th className="px-4 py-2 font-medium">{t('instanceDetail.access.time')}</th>
                  <th className="px-4 py-2 font-medium">{t('instanceDetail.access.clientIp')}</th>
                  <th className="px-4 py-2 font-medium">{t('instanceDetail.access.path')}</th>
                  <th className="px-4 py-2 font-medium">{t('instanceDetail.access.status')}</th>
                  <th className="px-4 py-2 font-medium">{t('instanceDetail.access.prompt')}</th>
                  <th className="px-4 py-2 font-medium text-right">{t('instanceDetail.access.ms')}</th>
                </tr>
              </thead>
              <tbody>
                {connections.map((c, i) => (
                  <tr key={c.id || i} className="border-b border-gray-50 last:border-0">
                    <td className="px-4 py-2 whitespace-nowrap text-gray-500">{fmtDate(c.timestamp)}</td>
                    <td className="px-4 py-2 whitespace-nowrap">{c.client_ip || '—'}</td>
                    <td className="px-4 py-2 font-mono">{c.path || '—'}</td>
                    <td className="px-4 py-2">
                      <span className={`px-2 py-0.5 rounded-full font-semibold ${STATUS_COLOR[c.response_status] || 'bg-gray-100 text-gray-600'}`}>
                        {c.response_status ?? '—'}
                      </span>
                    </td>
                    <td className="px-4 py-2 max-w-xs truncate text-gray-600" title={c.prompt_preview}>{c.prompt_preview || '—'}</td>
                    <td className="px-4 py-2 text-right text-gray-500">{c.elapsed_ms ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
