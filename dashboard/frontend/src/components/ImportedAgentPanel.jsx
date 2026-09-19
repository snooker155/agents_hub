import { useState } from 'react';
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  GitBranch,
  Info,
  Loader,
  RefreshCw,
  Server,
  X,
} from 'lucide-react';
import { recheckImportedAgent } from '../api';
import { useI18n } from '../i18n';

/**
 * The imported-agent panel on an agent's page.
 *
 * An agent can be imported before it is runnable, so this is where the
 * remaining work gets done: point the record at the service once it is up,
 * re-run the checks, and see the same verdict the import dialog showed. It
 * renders nothing for agents that were not imported.
 */
export default function ImportedAgentPanel({ agent, workspace = '', onUpdated }) {
  const { t } = useI18n();
  const remote = agent?.remote;
  const [url, setUrl] = useState(remote?.url || '');
  const [readiness, setReadiness] = useState(remote?.readiness || null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState('');

  if (!remote) return null;

  const ready = !!readiness?.runnable;

  const recheck = async () => {
    setChecking(true);
    setError('');
    try {
      const { data } = await recheckImportedAgent(agent.id, {
        url: url.trim(),
        workspace: workspace || undefined,
      });
      setReadiness(data.report);
      onUpdated?.();
    } catch (e) {
      setError(e.response?.data?.detail || e.message);
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="bg-white p-6 shadow-md rounded-lg">
      <div className="flex items-start justify-between gap-4 mb-4">
        <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2">
          <Server className="w-4 h-4 text-indigo-500" /> {t('importedAgentPanel.importedAgent')}
        </h3>
        <span
          className={`inline-flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider px-2.5 py-1 rounded-full ${
            ready ? 'bg-emerald-50 text-emerald-700' : 'bg-amber-50 text-amber-700'
          }`}
        >
          {ready ? <CheckCircle2 className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
          {ready ? t('importedAgentPanel.ready') : t('importedAgentPanel.needsSetup')}
        </span>
      </div>

      <p className="text-sm text-gray-500 mb-4">
        {t('importedAgentPanel.runsOutsideHub')}{' '}
        {remote.stream_path
          ? t('importedAgentPanel.streams')
          : t('importedAgentPanel.doesNotStream')}
      </p>

      {/* Provenance */}
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-5 text-sm">
        <div>
          <dt className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('importedAgentPanel.repository')}</dt>
          <dd className="text-gray-700 break-all flex items-start gap-1.5">
            <GitBranch className="w-3.5 h-3.5 mt-0.5 text-gray-400 shrink-0" />
            {remote.repo_url || '—'}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('importedAgentPanel.commit')}</dt>
          <dd className="text-gray-700">
            <code>{remote.commit || '—'}</code>
            {remote.branch && <span className="text-gray-400"> · {remote.branch}</span>}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('importedAgentPanel.runEndpoint')}</dt>
          <dd className="text-gray-700 break-all">
            POST {(remote.url || t('importedAgentPanel.notSet')) + (remote.run_path || '/run')}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('importedAgentPanel.healthEndpoint')}</dt>
          <dd className="text-gray-700 break-all">
            GET {(remote.url || t('importedAgentPanel.notSet')) + (remote.health_path || '/health')}
          </dd>
        </div>
        <div>
          <dt className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('importedAgentPanel.streaming')}</dt>
          <dd className="text-gray-700 break-all">
            {remote.stream_path
              ? `POST ${(remote.url || t('importedAgentPanel.notSet')) + remote.stream_path}`
              : t('importedAgentPanel.notDeclared')}
          </dd>
        </div>
      </dl>

      {/* Endpoint + re-check */}
      <div className="mb-5">
        <label className="block text-[10px] text-gray-400 uppercase tracking-wider font-semibold mb-1">
          {t('importedAgentPanel.serviceUrl')}
        </label>
        <div className="flex gap-2">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && recheck()}
            placeholder="http://localhost:8410"
            className="flex-1 border border-gray-300 rounded px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
          />
          <button
            onClick={recheck}
            disabled={checking}
            className="bg-indigo-600 text-white px-4 py-2 rounded text-sm font-bold flex items-center gap-2 hover:bg-indigo-700 disabled:opacity-50"
          >
            {checking ? <Loader className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Save &amp; re-check
          </button>
        </div>
        {error && <p className="text-sm text-red-600 mt-2">{error}</p>}
      </div>

      {/* Verdict */}
      {readiness && (
        <div>
          <p
            className={`text-sm font-semibold mb-2 ${
              ready ? 'text-emerald-700' : 'text-amber-700'
            }`}
          >
            {readiness.summary}
          </p>
          <ul className="space-y-1.5">
            {(readiness.checks || []).map((c) => (
              <li key={c.id} className="flex items-start gap-2 text-sm">
                <span className="mt-0.5 shrink-0">
                  {c.ok ? (
                    <Check className="w-4 h-4 text-emerald-600" />
                  ) : c.required ? (
                    <X className="w-4 h-4 text-red-600" />
                  ) : (
                    <Info className="w-4 h-4 text-amber-600" />
                  )}
                </span>
                <span className="min-w-0">
                  <span className="font-medium text-gray-800">{c.label}</span>
                  {c.detail && <span className="text-gray-500"> — {c.detail}</span>}
                  {!c.ok && c.fix && (
                    <span className="block text-gray-500 italic text-[13px]">{c.fix}</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
          {readiness.checked_at && (
            <p className="text-[11px] text-gray-400 mt-3">
              Last checked {new Date(readiness.checked_at).toLocaleString()}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
