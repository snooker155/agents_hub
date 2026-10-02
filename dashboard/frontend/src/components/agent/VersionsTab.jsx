import { History, Loader } from 'lucide-react';
import { fmtNodeDate } from './dateFormat';
import { useAgentPage } from './context';

/**
 * The definition's version history: what changed in each stored version, the
 * diff against what is live now, and rolling one back (agents/versions.py,
 * docs/agents.md "Versions"). Its own tab, so the Config tab holds the
 * prompt files alone.
 */
export default function VersionsTab() {
  const {
    agentVersions, diffLoading, diffText, handleRollback, handleToggleDiff, openDiffVersion,
    rollbackBusy, rollbackConfirmVersion, rollbackError, setRollbackConfirmVersion,
    setRollbackError, t, versionsError, versionsLoading,
  } = useAgentPage();
  return (
    <div className="bg-white p-6 shadow-md rounded-lg">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-lg font-bold flex items-center">
          <History className="w-5 h-5 mr-2 text-indigo-600" />
          {t('agentDetails.versions.title')}
        </h3>
        {versionsLoading && <Loader className="w-4 h-4 animate-spin text-gray-400" />}
      </div>
      <p className="text-xs text-gray-500 mb-3">{t('agentDetails.versions.description')}</p>

      {versionsError && <p className="text-xs text-red-600 mb-3">{versionsError}</p>}
      {!versionsLoading && agentVersions.length === 0 && !versionsError && (
        <p className="text-xs text-gray-500">{t('agentDetails.versions.empty')}</p>
      )}

      <div className="space-y-2">
        {[...agentVersions].reverse().map((v) => {
          const s = v.summary || {};
          const isDiffOpen = openDiffVersion === v.version;
          const diff = diffText[v.version];
          return (
            <div key={v.version} className="border border-gray-200 rounded-lg p-3">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div>
                  <span className="text-sm font-semibold text-gray-800">
                    {t('agentDetails.versions.version', { n: v.version })}
                  </span>
                  <span className="text-xs text-gray-400 ml-2">{fmtNodeDate(v.created_at)}</span>
                  {v.actor && (
                    <span className="text-xs text-gray-400 ml-2">
                      {t('agentDetails.versions.by', { actor: v.actor })}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => handleToggleDiff(v.version)}
                    className="px-2.5 py-1 text-xs font-semibold text-indigo-700 bg-indigo-50 rounded hover:bg-indigo-100"
                  >
                    {isDiffOpen ? t('agentDetails.versions.hideDiff') : t('agentDetails.versions.diff')}
                  </button>
                  <button
                    type="button"
                    onClick={() => { setRollbackConfirmVersion(v.version); setRollbackError(''); }}
                    className="px-2.5 py-1 text-xs font-semibold text-amber-700 bg-amber-50 rounded hover:bg-amber-100"
                  >
                    {t('agentDetails.versions.rollback')}
                  </button>
                </div>
              </div>

              <div className="mt-2 flex flex-wrap gap-1.5 text-[11px]">
                {s.initial && (
                  <span className="px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
                    {t('agentDetails.versions.initial')}
                  </span>
                )}
                {(s.tools_added || []).map(tid => (
                  <span key={`add-${tid}`} className="px-2 py-0.5 rounded-full bg-green-100 text-green-700">+{tid}</span>
                ))}
                {(s.tools_removed || []).map(tid => (
                  <span key={`rm-${tid}`} className="px-2 py-0.5 rounded-full bg-red-100 text-red-700">-{tid}</span>
                ))}
                {s.model_changed && (
                  <span className="px-2 py-0.5 rounded-full bg-blue-100 text-blue-700">
                    {t('agentDetails.versions.modelChanged', { from: s.from_model || '—', to: s.to_model || '—' })}
                  </span>
                )}
                {(s.files_changed || []).map(f => (
                  <span key={`f-${f}`} className="px-2 py-0.5 rounded-full bg-purple-100 text-purple-700">{f}</span>
                ))}
              </div>

              {isDiffOpen && (
                <div className="mt-3">
                  {diffLoading === v.version ? (
                    <Loader className="w-4 h-4 animate-spin text-gray-400" />
                  ) : diff?.error ? (
                    <p className="text-xs text-red-600">{diff.error}</p>
                  ) : diff && Object.keys(diff).length === 0 ? (
                    <p className="text-xs text-gray-500">{t('agentDetails.versions.noDiff')}</p>
                  ) : (
                    Object.entries(diff || {}).map(([part, text]) => (
                      <div key={part} className="mb-2">
                        <div className="text-[10px] uppercase tracking-wide font-semibold text-gray-400 mb-1">{part}</div>
                        <pre className="text-xs bg-gray-900 text-green-300 p-3 rounded-lg overflow-auto whitespace-pre-wrap max-h-64">{text}</pre>
                      </div>
                    ))
                  )}
                </div>
              )}

              {rollbackConfirmVersion === v.version && (
                <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded-lg">
                  <p className="text-xs text-amber-800 mb-2">
                    {t('agentDetails.versions.rollbackConfirm', { n: v.version })}
                  </p>
                  {rollbackError && <p className="text-xs text-red-600 mb-2">{rollbackError}</p>}
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={rollbackBusy}
                      onClick={() => handleRollback(v.version)}
                      className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-amber-600 rounded hover:bg-amber-700 disabled:opacity-40"
                    >
                      {rollbackBusy && <Loader className="w-3.5 h-3.5 mr-1 animate-spin" />}
                      {t('agentDetails.versions.confirmRollback')}
                    </button>
                    <button
                      type="button"
                      disabled={rollbackBusy}
                      onClick={() => { setRollbackConfirmVersion(null); setRollbackError(''); }}
                      className="px-3 py-1.5 text-xs font-semibold text-gray-600 bg-gray-100 rounded hover:bg-gray-200"
                    >
                      {t('agentDetails.cancel')}
                    </button>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
