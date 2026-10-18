import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loader, Play, X } from 'lucide-react';

import { getAgents, getWorkspaces, startInstance } from '../../api';
import { useWorkspace } from '../workspace';
import { instancePath } from '../instanceUtils';
import { useI18n } from '../../i18n';

/**
 * Run, where the agent is not already fixed: the Instances page's button.
 *
 * The form asks only the agent and the workspace (routes/instances.py) and
 * opens the copy's page. A runner answers for the agent named in each
 * message, so its page opens with that agent picked.
 *
 * `agentId` fixes the agent (Run from the agent's own page); without it the
 * form offers a select built from `agents` (the fleet grid already holds the
 * list, so it is passed in rather than fetched again).
 */
export default function StartInstanceModal({ open, onClose, agentId = null, agents = null, defaultWorkspace = '' }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { selectedWorkspace } = useWorkspace();

  const [agentList, setAgentList] = useState(agents || []);
  const [selectedAgentId, setSelectedAgentId] = useState(agentId || '');
  const [workspaces, setWorkspaces] = useState([]);
  const [workspace, setWorkspace] = useState(defaultWorkspace || selectedWorkspace || '');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  // Opening the modal (or pointing it at another agent or workspace) starts
  // the form over. Adjusted while rendering; `selectedWorkspace` is read but
  // deliberately not part of the key, as before.
  const resetKey = open ? `${agentId}|${defaultWorkspace}` : null;
  const [seenResetKey, setSeenResetKey] = useState(resetKey);
  if (resetKey !== seenResetKey) {
    setSeenResetKey(resetKey);
    if (open) {
      setSelectedAgentId(agentId || '');
      setWorkspace(defaultWorkspace || selectedWorkspace || '');
      setError('');
    }
  }

  useEffect(() => {
    if (!open || agentId || agents) return;
    getAgents().then((r) => setAgentList(r.data?.agents || r.data || [])).catch(() => setAgentList([]));
  }, [open, agentId, agents]);

  useEffect(() => {
    if (!open) return;
    getWorkspaces().then((r) => setWorkspaces(r.data || [])).catch(() => setWorkspaces([]));
  }, [open]);

  if (!open) return null;

  const effectiveAgentId = agentId || selectedAgentId;
  const agentName = agentId
    ? (agentList.find((a) => a.id === agentId)?.name || agentId)
    : null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!effectiveAgentId || submitting) return;
    setSubmitting(true);
    setError('');
    try {
      const { data } = await startInstance({
        agent_id: effectiveAgentId,
        workspace: workspace || null,
      });
      onClose();
      navigate(instancePath(data, effectiveAgentId));
    } catch (err) {
      setError(err.response?.data?.detail || t('startInstance.failed'));
    } finally {
      setSubmitting(false);
    }
  };

  const field = 'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none';
  const label = 'block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5';

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-md">
        <div className="flex items-center justify-between p-5 border-b">
          <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
            <Play className="w-4 h-4 text-indigo-600" />
            {t('startInstance.title')}
          </h2>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>
        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          {error && (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg p-3">{error}</div>
          )}

          <div>
            <label className={label}>{t('startInstance.agent')}</label>
            {agentId ? (
              <div className="px-3 py-2 text-sm bg-gray-50 border border-gray-200 rounded-lg text-gray-700">{agentName}</div>
            ) : (
              <select className={field} value={selectedAgentId} onChange={(e) => setSelectedAgentId(e.target.value)}>
                <option value="">{t('startInstance.selectAgent')}</option>
                {(agentList || []).map((a) => (
                  <option key={a.id} value={a.id}>{a.name || a.id}</option>
                ))}
              </select>
            )}
          </div>

          <div>
            <label className={label}>{t('startInstance.workspace')}</label>
            <select className={field} value={workspace} onChange={(e) => setWorkspace(e.target.value)}>
              <option value="">{t('startInstance.defaultWorkspace')}</option>
              {workspaces.map((ws) => (
                <option key={ws.name} value={ws.name}>{ws.label || ws.id || ws.name}</option>
              ))}
            </select>
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200"
              disabled={submitting}
            >
              {t('startInstance.cancel')}
            </button>
            <button
              type="submit"
              disabled={submitting || !effectiveAgentId}
              className="flex items-center gap-2 px-5 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? <Loader className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
              {submitting ? t('startInstance.starting') : t('startInstance.submit')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
