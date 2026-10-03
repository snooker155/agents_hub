import React, { useEffect, useRef, useState } from 'react';
import { AlertTriangle } from 'lucide-react';
import { useI18n } from '../../i18n';
import { setAgentConflictHandler } from '../../api/agentRevision';

/**
 * Asks what to do when a save on the agent page meets somebody else's newer
 * edit (a 409 `version_conflict`, agents/revision.py). Mounted once on the
 * agent page; while it is mounted, the API layer (api/agentRevision.js) asks
 * it instead of failing the save outright.
 *
 * Reload drops this page's unsaved edits and loads the agent as it is now;
 * Overwrite saves this edit over the other one; Cancel leaves both alone.
 * `onReload` defaults to a full page reload.
 */
export default function AgentConflictDialog({ agentId, onReload }) {
  const { t } = useI18n();
  const [pending, setPending] = useState(null);
  const resolver = useRef(null);

  useEffect(() => setAgentConflictHandler((info) => {
    if (agentId && info.agentId !== agentId) return Promise.resolve('cancel');
    return new Promise((resolve) => {
      resolver.current = resolve;
      setPending(info);
    });
  }), [agentId]);

  if (!pending) return null;

  const close = (decision) => {
    const resolve = resolver.current;
    resolver.current = null;
    setPending(null);
    if (resolve) resolve(decision);
    if (decision === 'reload') {
      if (onReload) onReload();
      else window.location.reload();
    }
  };

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4" data-testid="agent-conflict-dialog">
      <div role="dialog" aria-modal="true" aria-labelledby="agent-conflict-title"
        className="bg-white rounded-xl shadow-2xl w-full max-w-md p-5 space-y-4">
        <div className="flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <h3 id="agent-conflict-title" className="text-sm font-semibold text-gray-900">
              {t('agentRevision.title')}
            </h3>
            <p className="text-sm text-gray-600">
              {pending.currentVersion != null
                ? t('agentRevision.bodyVersion', { version: pending.currentVersion })
                : t('agentRevision.body')}
            </p>
          </div>
        </div>
        <div className="flex justify-end gap-2 flex-wrap">
          <button type="button" onClick={() => close('cancel')}
            className="px-3 py-1.5 text-sm rounded-lg text-gray-600 hover:bg-gray-100">
            {t('agentRevision.cancel')}
          </button>
          <button type="button" onClick={() => close('reload')}
            className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 text-gray-800 hover:bg-gray-50">
            {t('agentRevision.reload')}
          </button>
          <button type="button" onClick={() => close('overwrite')}
            className="px-3 py-1.5 text-sm rounded-lg bg-amber-600 text-white hover:bg-amber-700">
            {t('agentRevision.overwrite')}
          </button>
        </div>
      </div>
    </div>
  );
}
