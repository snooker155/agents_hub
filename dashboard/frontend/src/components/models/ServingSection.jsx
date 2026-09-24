import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Loader, AlertCircle, Copy, Check } from 'lucide-react';
import { getServingInfo, getServingUsage } from '../../api/serving';
import { useI18n } from '../../i18n';
import { errorDetail } from '../toast';

function fmtInt(n) { return (n || 0).toLocaleString(); }

function curlExample(baseUrl) {
  const url = `${baseUrl || ''}/chat/completions`;
  return [
    `curl ${url} \\`,
    "  -H \"Authorization: Bearer <your API key>\" \\",
    "  -H \"Content-Type: application/json\" \\",
    "  -d '{",
    '    "model": "provider/model",',
    '    "messages": [{"role": "user", "content": "Hello"}]',
    "  }'",
  ].join('\n');
}

/**
 * The hub's own OpenAI compatible /v1 endpoint: what an outside caller (or a
 * stray curl) hits, and how much it has been used. Distinct from the Usage
 * tab on this same page, which is the hub's own agents calling out to
 * providers; this is the other direction.
 */
export default function ServingSection() {
  const { t } = useI18n();
  const [info, setInfo] = useState(null);
  const [usage, setUsage] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [copied, setCopied] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [infoRes, usageRes] = await Promise.all([getServingInfo(), getServingUsage({})]);
      setInfo(infoRes.data || {});
      setUsage(usageRes.data || {});
    } catch (e) {
      setError(errorDetail(e) || t('localModels.serving.unreachable'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { load(); }, [load]);

  const handleCopy = async () => {
    const text = curlExample(info?.base_url);
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be blocked (permissions, insecure context); the
      // example is still selectable text, so this is not fatal.
    }
  };

  const rows = usage?.rows || [];
  const totals = usage?.totals || { requests: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 };
  const recent = usage?.recent || [];

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100 bg-gray-50 flex-wrap">
        <span className="text-sm font-semibold text-gray-800">{t('localModels.serving.title')}</span>
        {info?.base_url && <span className="text-xs text-gray-400 font-mono truncate">{info.base_url}</span>}
      </div>

      <div className="p-3">
        {loading && !info ? (
          <p className="text-sm text-gray-500 flex items-center gap-2"><Loader className="w-4 h-4 animate-spin" /> {t('localModels.loading')}</p>
        ) : error && !info ? (
          <p className="flex items-center gap-1.5 text-sm text-red-700"><AlertCircle className="w-4 h-4 shrink-0" /> {error}</p>
        ) : (
          <>
            <p className="text-sm text-gray-600 mb-2">
              {t('localModels.serving.intro', { models: info?.models ?? 0 })}
            </p>
            <div className="relative bg-gray-900 rounded-lg p-3 mb-2">
              <button
                type="button"
                onClick={handleCopy}
                title={t('localModels.serving.copy')}
                className="absolute top-2 right-2 text-gray-400 hover:text-white"
              >
                {copied ? <Check className="w-3.5 h-3.5 text-green-400" /> : <Copy className="w-3.5 h-3.5" />}
              </button>
              <pre className="text-xs text-gray-100 overflow-x-auto pr-6"><code>{curlExample(info?.base_url)}</code></pre>
            </div>
            {info?.auth === 'api_key' && (
              <p className="text-xs text-gray-500 mb-3">
                {t('localModels.serving.keysHint')} <Link to="/account" className="text-indigo-600 hover:underline">{t('localModels.serving.accountLink')}</Link>.
              </p>
            )}

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
              {[
                { label: t('localModels.serving.requests'), value: fmtInt(totals.requests) },
                { label: t('localModels.serving.promptTokens'), value: fmtInt(totals.prompt_tokens) },
                { label: t('localModels.serving.completionTokens'), value: fmtInt(totals.completion_tokens) },
                { label: t('localModels.serving.totalTokens'), value: fmtInt(totals.total_tokens) },
              ].map((c) => (
                <div key={c.label} className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
                  <p className="text-[11px] uppercase tracking-wide text-gray-400">{c.label}</p>
                  <p className="text-lg font-semibold text-gray-800">{c.value}</p>
                </div>
              ))}
            </div>

            {rows.length === 0 ? (
              <p className="text-sm text-gray-400 px-1 py-2">{t('localModels.serving.noUsage')}</p>
            ) : (
              <div className="overflow-x-auto border border-gray-100 rounded-lg mb-3">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-gray-400 uppercase tracking-wider border-b border-gray-100 bg-gray-50">
                      <th className="px-2 py-1.5 font-medium">{t('models.provider')}</th>
                      <th className="px-2 py-1.5 font-medium">{t('models.model')}</th>
                      <th className="px-2 py-1.5 font-medium text-right">{t('localModels.serving.requests')}</th>
                      <th className="px-2 py-1.5 font-medium text-right">{t('localModels.serving.promptTokens')}</th>
                      <th className="px-2 py-1.5 font-medium text-right">{t('localModels.serving.completionTokens')}</th>
                      <th className="px-2 py-1.5 font-medium text-right">{t('localModels.serving.errors')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr key={`${r.provider}:${r.model}`} className="border-t border-gray-50 hover:bg-gray-50">
                        <td className="px-2 py-1.5 text-gray-700">{r.provider}</td>
                        <td className="px-2 py-1.5 font-mono text-gray-700">{r.model}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{fmtInt(r.requests)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{fmtInt(r.prompt_tokens)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{fmtInt(r.completion_tokens)}</td>
                        <td className="px-2 py-1.5 text-right tabular-nums">{r.errors > 0 ? <span className="text-red-600">{fmtInt(r.errors)}</span> : fmtInt(r.errors)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {recent.length > 0 && (
              <div>
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">{t('localModels.serving.recent')}</p>
                <div className="space-y-1">
                  {recent.map((r, i) => (
                    <div key={`${r.at}-${i}`} className="flex items-center gap-2 text-xs text-gray-500 py-0.5">
                      <span className="text-gray-400 shrink-0 tabular-nums">{r.at ? new Date(r.at).toLocaleTimeString() : ''}</span>
                      <span className="truncate">{r.actor_name || t('common.none')}</span>
                      <span className="font-mono text-gray-600 truncate">{r.model}</span>
                      <span className="ml-auto shrink-0 tabular-nums">{fmtInt((r.prompt_tokens || 0) + (r.completion_tokens || 0))}</span>
                      <span className={`shrink-0 font-medium ${r.status === 'error' ? 'text-red-600' : 'text-gray-500'}`}>{r.status}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
