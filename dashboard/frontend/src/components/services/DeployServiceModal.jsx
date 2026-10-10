import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Info, Loader, Rocket, X } from 'lucide-react';

import { createService, getAgents, getEnvironments } from '../../api';
import { useWorkspace } from '../workspace';
import { useI18n } from '../../i18n';

/**
 * Deploy: create a service (services/store.py), the desired state of an agent
 * kept running as replicas, and open its page. From the agent's page the
 * agent is fixed; from the Services page the form offers a select, plus a
 * runner (no agent: a replica that answers any agent's chat turn).
 */
export default function DeployServiceModal({ open, onClose, agentId = null, defaultWorkspace = '' }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const { selectedWorkspace } = useWorkspace();

  const [agentList, setAgentList] = useState([]);
  const [selectedAgentId, setSelectedAgentId] = useState(agentId || '');
  // The workspace is the one chosen in the header (or the page's own): the
  // form shows it rather than asking again.
  const [workspace, setWorkspace] = useState(defaultWorkspace || selectedWorkspace || '');
  const [environments, setEnvironments] = useState([]);
  const [environmentId, setEnvironmentId] = useState('');
  const [name, setName] = useState('');
  const [replicasMin, setReplicasMin] = useState('1');
  const [replicasMax, setReplicasMax] = useState('1');
  const [concurrency, setConcurrency] = useState('4');
  const [idleStopMinutes, setIdleStopMinutes] = useState('10');
  const [takeTasks, setTakeTasks] = useState(false);
  const [publish, setPublish] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [budget, setBudget] = useState('');
  const [version, setVersion] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  // Start the form over each time the dialog opens or its target changes:
  // adjusted during render so the previous values are never painted.
  const resetKey = open ? `${agentId}|${defaultWorkspace}` : null;
  const [seenResetKey, setSeenResetKey] = useState(null);
  if (seenResetKey !== resetKey) {
    setSeenResetKey(resetKey);
    if (resetKey !== null) {
      setSelectedAgentId(agentId || '');
      setWorkspace(defaultWorkspace || selectedWorkspace || '');
      setEnvironmentId('');
      setName('');
      setReplicasMin('1');
      setReplicasMax('1');
      setConcurrency('4');
      setIdleStopMinutes('10');
      setTakeTasks(false);
      setPublish(false);
      setShowAdvanced(false);
      setBudget('');
      setVersion('');
      setError('');
    }
  }

  useEffect(() => {
    if (!open) return;
    getAgents().then((r) => setAgentList(r.data?.agents || r.data || [])).catch(() => setAgentList([]));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    getEnvironments(workspace || undefined).then((r) => setEnvironments(r.data || [])).catch(() => setEnvironments([]));
  }, [open, workspace]);

  if (!open) return null;

  const effectiveAgentId = agentId || selectedAgentId;
  const agentName = agentId ? (agentList.find((a) => a.id === agentId)?.name || agentId) : null;
  const isRunner = !effectiveAgentId;

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError('');
    const min = Math.max(0, parseInt(replicasMin, 10) || 0);
    const max = Math.max(min, parseInt(replicasMax, 10) || 0);
    try {
      const { data } = await createService({
        agent_id: effectiveAgentId || null,
        workspace: workspace || null,
        environment_id: environmentId || null,
        name: name.trim() || null,
        replicas_min: min,
        replicas_max: max,
        concurrency: Math.max(1, parseInt(concurrency, 10) || 4),
        take_tasks: takeTasks,
        idle_stop_seconds: Math.max(0, Math.round((parseFloat(idleStopMinutes) || 0) * 60)),
        publish,
        ...(budget.trim() ? { budget_usd: parseFloat(budget) } : {}),
        ...(version.trim() ? { agent_version: parseInt(version, 10) } : {}),
      });
      onClose();
      navigate(`/services/${data.service_id}`);
    } catch (err) {
      setError(err.response?.data?.detail || t('services.deploy.failed'));
    } finally {
      setSubmitting(false);
    }
  };

  const field = 'w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none';
  const label = 'block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1.5';

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-5 border-b">
          <h2 className="text-lg font-semibold text-gray-800 flex items-center gap-2">
            <Rocket className="w-4 h-4 text-indigo-600" />
            {t('services.deploy.title')}
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
            <label className={label}>{t('services.deploy.agent')}</label>
            {agentId ? (
              <div className="px-3 py-2 text-sm bg-gray-50 border border-gray-200 rounded-lg text-gray-700">{agentName}</div>
            ) : (
              <select className={field} value={selectedAgentId} onChange={(e) => setSelectedAgentId(e.target.value)}>
                <option value="">{t('services.deploy.runnerOption')}</option>
                {(agentList || []).map((a) => (
                  <option key={a.id} value={a.id}>{a.name || a.id}</option>
                ))}
              </select>
            )}
            {isRunner && (
              <p className="text-xs text-gray-500 mt-1 flex items-start gap-1">
                <Info className="w-3 h-3 mt-0.5 shrink-0" />{t('services.deploy.runnerHint')}
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={label}>{t('services.deploy.workspace')}</label>
              <div className="px-3 py-2 text-sm bg-gray-50 border border-gray-200 rounded-lg text-gray-700 truncate">
                {workspace || t('services.deploy.defaultWorkspace')}
              </div>
            </div>
            <div>
              <label className={label}>{t('services.deploy.environment')}</label>
              <select className={field} value={environmentId} onChange={(e) => setEnvironmentId(e.target.value)}>
                <option value="">{t('services.deploy.environmentDefault')}</option>
                {environments.map((env) => (
                  <option key={env.id} value={env.id}>{env.name}</option>
                ))}
              </select>
            </div>
          </div>

          <div>
            <label className={label}>
              {t('services.deploy.name')} <span className="text-gray-400 font-normal normal-case">({t('common.optional')})</span>
            </label>
            <input className={field} placeholder={t('services.deploy.namePlaceholder')} value={name}
                   onChange={(e) => setName(e.target.value)} />
          </div>

          {/* Bottom-aligned: a label that wraps must not push its box down. */}
          <div className="grid grid-cols-3 gap-3 items-end">
            <div>
              <label className={label}>{t('services.deploy.replicasMin')}</label>
              <input type="number" min="0" max="64" className={field} value={replicasMin}
                     onChange={(e) => setReplicasMin(e.target.value)} />
            </div>
            <div>
              <label className={label}>{t('services.deploy.replicasMax')}</label>
              <input type="number" min="0" max="64" className={field} value={replicasMax}
                     onChange={(e) => setReplicasMax(e.target.value)} />
            </div>
            <div>
              <label className={label}>{t('services.deploy.concurrency')}</label>
              <input type="number" min="1" max="32" className={field} value={concurrency}
                     onChange={(e) => setConcurrency(e.target.value)} />
            </div>
          </div>
          <p className="text-xs text-gray-500 -mt-2">{t('services.deploy.replicasHint')}</p>

          <div>
            <label className={label}>{t('services.deploy.idleStop')}</label>
            <input type="number" min="0" step="1" className={field} value={idleStopMinutes}
                   onChange={(e) => setIdleStopMinutes(e.target.value)} />
            <p className="text-xs text-gray-500 mt-1">{t('services.deploy.idleStopHint')}</p>
          </div>

          {!isRunner && (
            <label className="flex items-start gap-3 cursor-pointer select-none">
              <input type="checkbox" className="mt-0.5" checked={takeTasks} onChange={(e) => setTakeTasks(e.target.checked)} />
              <span>
                <span className="block text-sm font-medium text-gray-800">{t('services.deploy.takeTasks')}</span>
                <span className="block text-xs text-gray-500 mt-0.5">{t('services.deploy.takeTasksHint')}</span>
              </span>
            </label>
          )}

          {!isRunner && (
            <label className="flex items-start gap-3 cursor-pointer select-none">
              <input type="checkbox" className="mt-0.5" checked={publish} onChange={(e) => setPublish(e.target.checked)} />
              <span>
                <span className="block text-sm font-medium text-gray-800">{t('services.deploy.publish')}</span>
                <span className="block text-xs text-gray-500 mt-0.5">{t('services.deploy.publishHint')}</span>
              </span>
            </label>
          )}

          <div>
            <button type="button" onClick={() => setShowAdvanced((v) => !v)}
                    className="text-xs text-indigo-600 hover:text-indigo-800 font-medium">
              {showAdvanced ? t('services.deploy.hideAdvanced') : t('services.deploy.showAdvanced')}
            </button>
            {showAdvanced && (
              <div className="mt-2 grid grid-cols-2 gap-3 items-start">
                <div>
                  <label className={label}>{t('services.deploy.budget')}</label>
                  <input type="number" min="0" step="0.01" className={field} value={budget}
                         placeholder="—" onChange={(e) => setBudget(e.target.value)} />
                  <p className="text-xs text-gray-500 mt-1">{t('services.deploy.budgetHint')}</p>
                </div>
                {!isRunner && (
                  <div>
                    <label className={label}>{t('services.deploy.version')}</label>
                    <input type="number" min="1" step="1" className={field} value={version}
                           placeholder={t('services.deploy.versionLatest')} onChange={(e) => setVersion(e.target.value)} />
                    <p className="text-xs text-gray-500 mt-1">{t('services.deploy.versionHint')}</p>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose}
                    className="px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">
              {t('services.deploy.cancel')}
            </button>
            <button type="submit" disabled={submitting}
                    className="inline-flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50">
              {submitting ? <Loader className="w-4 h-4 animate-spin" /> : <Rocket className="w-4 h-4" />}
              {submitting ? t('services.deploy.deploying') : t('services.deploy.submit')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
