import { useState } from 'react';
import { ChevronDown, ChevronRight, Loader, RefreshCw, RotateCcw } from 'lucide-react';
import { clearRuntimeUsage } from '../../api/localModels';
import { useToast, errorDetail } from '../toast';
import { useI18n } from '../../i18n';

const SOURCES = ['hub', 'endpoint', 'voice', 'direct'];

function fmtInt(n) { return (n || 0).toLocaleString(); }
function fmtMs(ms) {
  if (!ms) return '0 ms';
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} s`;
}

/**
 * Every call to the local models, counted by the runtime's own gateway
 * (deploy/models/app.py, Usage), so nothing slips past: the hub's agents,
 * outside calls through the hub's /v1 endpoint and the assistant's voice,
 * each labelled by the hub with X-Hub-Source; a call without the label is
 * "direct". The Endpoint tab counts only the outside calls, the Usage tab
 * only the agents' runs; this is their sum on the runtime's side.
 *
 * The counts come with the runtime's status (GET /models/local/runtime,
 * `usage`), which RuntimeSection polls; this card asks for nothing itself
 * except a reset. Nothing is rendered while the runtime is off or
 * unreachable: the runtime card above already says so. A runtime too old to
 * count sets `usage_outdated`, which asks for a restart.
 *
 * Folded by default like Import and Downloads below it; the folded header
 * still shows the call and error totals, or that a restart is needed.
 */
export default function RuntimeUsage({ data, outdated, loading, onRefresh, onChange }) {
  const { t } = useI18n();
  const toast = useToast();
  const [clearing, setClearing] = useState(false);
  const [open, setOpen] = useState(false);

  const reset = async () => {
    if (!window.confirm(t('localModels.runtimeUsage.confirmReset'))) return;
    setClearing(true);
    try {
      const res = await clearRuntimeUsage();
      onChange(res.data || {});
    } catch (err) {
      toast.error(t('localModels.runtimeUsage.resetFailed'), errorDetail(err));
    } finally {
      setClearing(false);
    }
  };

  if (!data && !outdated) return null;

  const rows = data?.rows || [];
  const recent = data?.recent || [];
  const totals = data?.totals || {};
  const bySource = SOURCES.map((s) => ({
    id: s,
    requests: rows.filter((r) => r.source === s).reduce((n, r) => n + r.requests, 0),
  })).filter((s) => s.requests > 0);
  const sourceLabel = (s) => t(`localModels.runtimeUsage.sources.${s}`, { defaultValue: s });
  const kindLabel = (k) => t(`localModels.runtimeUsage.kinds.${k}`, { defaultValue: k });
  const tokens = (r) => (r.prompt_tokens || r.completion_tokens
    ? `${fmtInt(r.prompt_tokens)} / ${fmtInt(r.completion_tokens)}`
    : <span title={t('localModels.runtimeUsage.tokensUnknown')}>—</span>);

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden" data-testid="runtime-usage">
      <div className={`flex items-center gap-2 bg-gray-50 border-gray-100 ${open ? 'border-b' : ''}`}>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex-1 min-w-0 flex items-center gap-2 px-4 py-3 text-left hover:bg-gray-100 transition-colors focus:outline-none flex-wrap"
          aria-expanded={open}
          data-testid="runtime-usage-toggle"
        >
          {open ? <ChevronDown className="w-4 h-4 text-gray-500 shrink-0" /> : <ChevronRight className="w-4 h-4 text-gray-500 shrink-0" />}
          <span className="text-sm font-semibold text-gray-800 shrink-0">{t('localModels.runtimeUsage.title')}</span>
          {outdated ? (
            <span className="text-xs text-amber-700">{t('localModels.runtimeUsage.restartNeeded')}</span>
          ) : (
            <span className="text-xs text-gray-400" data-testid="runtime-usage-summary">
              {t('localModels.runtimeUsage.requests')}: {fmtInt(totals.requests)}
              {totals.errors > 0 && (
                <>
                  {' · '}
                  <span className="text-red-600">{t('localModels.runtimeUsage.errors')}: {fmtInt(totals.errors)}</span>
                </>
              )}
              {data?.since && <>{' · '}{t('localModels.runtimeUsage.since', { date: new Date(data.since).toLocaleString() })}</>}
            </span>
          )}
        </button>
        {open && data && (
          <div className="flex items-center gap-1.5 shrink-0 pr-4">
            <button
              type="button"
              onClick={reset}
              disabled={clearing || !totals.requests}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-xs font-medium transition-colors disabled:opacity-50"
              data-testid="runtime-usage-reset"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              {t('localModels.runtimeUsage.reset')}
            </button>
            <button
              type="button"
              onClick={onRefresh}
              disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-100 text-xs font-medium transition-colors"
            >
              {loading ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
              {t('common.refresh')}
            </button>
          </div>
        )}
      </div>

      {!open ? null : outdated ? (
        <p className="px-4 py-3 text-sm text-amber-800 bg-amber-50" data-testid="runtime-usage-outdated">
          {t('localModels.runtimeUsage.outdated')}
        </p>
      ) : (
        <div className="p-3 space-y-3">
          <p className="text-xs text-gray-500">{t('localModels.runtimeUsage.intro')}</p>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            {[
              { label: t('localModels.runtimeUsage.requests'), value: fmtInt(totals.requests) },
              { label: t('localModels.runtimeUsage.promptTokens'), value: fmtInt(totals.prompt_tokens) },
              { label: t('localModels.runtimeUsage.completionTokens'), value: fmtInt(totals.completion_tokens) },
              { label: t('localModels.runtimeUsage.avgTime'), value: totals.requests ? fmtMs(totals.duration_ms / totals.requests) : '—' },
              { label: t('localModels.runtimeUsage.errors'), value: fmtInt(totals.errors) },
            ].map((c) => (
              <div key={c.label} className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-2">
                <p className="text-[11px] uppercase tracking-wide text-gray-400">{c.label}</p>
                <p className="text-lg font-semibold text-gray-800">{c.value}</p>
              </div>
            ))}
          </div>

          {bySource.length > 0 && (
            <div className="flex flex-wrap gap-2" data-testid="runtime-usage-sources">
              {bySource.map((s) => (
                <span key={s.id} className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 px-2.5 py-0.5 text-xs text-gray-600">
                  {sourceLabel(s.id)}
                  <span className="font-semibold text-gray-800 tabular-nums">{fmtInt(s.requests)}</span>
                </span>
              ))}
            </div>
          )}

          {rows.length === 0 ? (
            <p className="text-sm text-gray-400 px-1 py-2">{t('localModels.runtimeUsage.noCalls')}</p>
          ) : (
            <div className="overflow-x-auto border border-gray-100 rounded-lg">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-gray-400 uppercase tracking-wider border-b border-gray-100 bg-gray-50">
                    <th className="px-2 py-1.5 font-medium">{t('localModels.runtimeUsage.model')}</th>
                    <th className="px-2 py-1.5 font-medium">{t('localModels.runtimeUsage.source')}</th>
                    <th className="px-2 py-1.5 font-medium">{t('localModels.runtimeUsage.kind')}</th>
                    <th className="px-2 py-1.5 font-medium text-right">{t('localModels.runtimeUsage.requests')}</th>
                    <th className="px-2 py-1.5 font-medium text-right">{t('localModels.runtimeUsage.promptTokens')} / {t('localModels.runtimeUsage.completionTokens')}</th>
                    <th className="px-2 py-1.5 font-medium text-right">{t('localModels.runtimeUsage.avgTime')}</th>
                    <th className="px-2 py-1.5 font-medium text-right" title={t('localModels.runtimeUsage.speedHint')}>{t('localModels.runtimeUsage.speed')}</th>
                    <th className="px-2 py-1.5 font-medium text-right">{t('localModels.runtimeUsage.errors')}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={`${r.model}:${r.source}:${r.kind}`} className="border-t border-gray-50 hover:bg-gray-50">
                      <td className="px-2 py-1.5 font-mono text-gray-700">{r.model}</td>
                      <td className="px-2 py-1.5 text-gray-700">{sourceLabel(r.source)}</td>
                      <td className="px-2 py-1.5 text-gray-500">{kindLabel(r.kind)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{fmtInt(r.requests)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{tokens(r)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{fmtMs(r.requests ? r.duration_ms / r.requests : 0)}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{r.gen_ms > 0 && r.gen_tokens > 0 ? (r.gen_tokens * 1000 / r.gen_ms).toFixed(1) : '—'}</td>
                      <td className="px-2 py-1.5 text-right tabular-nums">{r.errors > 0 ? <span className="text-red-600">{fmtInt(r.errors)}</span> : fmtInt(r.errors)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {recent.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1.5">{t('localModels.runtimeUsage.recent')}</p>
              <div className="space-y-1">
                {recent.map((r, i) => (
                  <div key={`${r.at}-${i}`} className="flex items-center gap-2 text-xs text-gray-500 py-0.5">
                    <span className="text-gray-400 shrink-0 tabular-nums">{r.at ? new Date(r.at).toLocaleTimeString() : ''}</span>
                    <span className="truncate">{sourceLabel(r.source)}</span>
                    <span className="font-mono text-gray-600 truncate">{r.model}</span>
                    <span className="shrink-0 text-gray-400">{kindLabel(r.kind)}</span>
                    <span className="ml-auto shrink-0 tabular-nums">{fmtMs(r.duration_ms)}</span>
                    <span className={`shrink-0 font-medium ${r.status === 'error' ? 'text-red-600' : 'text-gray-500'}`}>
                      {r.status === 'error' ? r.code : r.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
