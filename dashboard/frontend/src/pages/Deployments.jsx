import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import {
  getPlanJobs,
  createPlanJob,
  updatePlanJob,
  deletePlanJob,
  pausePlanJob,
  resumePlanJob,
  cancelPlanJob,
  runPlanJobNow,
  getJobFires,
  getAgents,
  listFlows,
  getLoops,
  getEnvironments,
  getProjects,
  getSharedMemories,
  getWorkspaceSecrets,
} from '../api';
import { getAgentVersions } from '../api/agentVersions';
import { listWorkspaceFiles, uploadWorkspaceFileObject } from '../api/files';
import { Activity, AlertCircle, AlertTriangle, Bot, CheckCircle, Clock, History, Loader, Pause, Pencil, Play, Plus, RefreshCw, Repeat, Rocket, RotateCw, Trash2, Upload, Workflow, X, XCircle, Zap } from 'lucide-react';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { DeployStatusPill } from '../components/projects/DeployPanel';
import { listDeployedApps } from '../api';
import { useI18n } from '../i18n';
import CronHint from '../components/CronHint';

// Project deployments (docs/project-deployments.md): the apps the hub runs
// for projects of this workspace, each opening on its project's Deploy tab.
function DeployedApps({ workspace, t }) {
  const [apps, setApps] = useState([]);
  useEffect(() => {
    let cancelled = false;
    listDeployedApps(workspace).then(({ data }) => { if (!cancelled) setApps(data.items || []); }).catch(() => {});
    const timer = setInterval(() => {
      listDeployedApps(workspace).then(({ data }) => { if (!cancelled) setApps(data.items || []); }).catch(() => {});
    }, 10000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [workspace]);
  if (!apps.length) return null;
  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-4 py-2 border-b border-gray-100 text-xs font-semibold text-gray-500 uppercase tracking-wider">
        {t('projectDeploy.appsTitle')}
      </div>
      <table className="w-full text-sm">
        <tbody className="divide-y divide-gray-100">
          {apps.map((app) => (
            <tr key={app.id}>
              <td className="px-4 py-2 font-medium text-gray-800">
                <Link to={`/projects/${app.project_id}`} className="hover:text-indigo-600">{app.name || app.project_id}</Link>
              </td>
              <td className="px-4 py-2"><DeployStatusPill status={app.status} t={t} /></td>
              <td className="px-4 py-2 text-xs text-gray-500">{t(`projectDeploy.modes.${app.mode}`)} · {(app.services || []).length} {t('projectDeploy.servicesCount')}</td>
              <td className="px-4 py-2 text-xs text-gray-500">{app.workspace}</td>
              <td className="px-4 py-2 text-right text-xs">
                {app.status === 'running' && app.links?.path ? (
                  <a href={app.links.path} target="_blank" rel="noopener noreferrer" className="text-indigo-600 hover:text-indigo-800">{t('projectDeploy.open')}</a>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
import DateInput from '../components/DateInput';
import PageLoader from '../components/PageLoader';

// ---- helpers ----------------------------------------------------------------

const STATUS_STYLES = {
  scheduled: { bg: 'bg-blue-100',  text: 'text-blue-700',  icon: Clock },
  paused:    { bg: 'bg-amber-100', text: 'text-amber-700', icon: Pause },
  fired:     { bg: 'bg-green-100', text: 'text-green-700', icon: CheckCircle },
  cancelled: { bg: 'bg-gray-100',  text: 'text-gray-600',  icon: X },
  failed:    { bg: 'bg-red-100',   text: 'text-red-700',   icon: XCircle },
};

function StatusBadge({ status, pausedReason, t }) {
  const s = STATUS_STYLES[status] || { bg: 'bg-gray-100', text: 'text-gray-500', icon: AlertCircle };
  const Icon = s.icon;
  return (
    <span className="inline-flex items-center gap-1">
      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
        <Icon className="w-3 h-3" />
        {status}
      </span>
      {status === 'paused' && pausedReason && (
        <span
          title={t(`deployments.pausedReason.${pausedReason}`, { defaultValue: pausedReason })}
          className="text-[10px] font-medium text-amber-500 uppercase"
        >
          {t(`deployments.pausedReason.${pausedReason}`, { defaultValue: pausedReason })}
        </span>
      )}
    </span>
  );
}

function KindIcon({ kind }) {
  if (kind === 'flow') return <Workflow className="w-3.5 h-3.5" />;
  if (kind === 'loop') return <RotateCw className="w-3.5 h-3.5" />;
  // A proactive agent's pulse (docs/proactive.md): owned by the agent's
  // profile, listed here because every tick is a deployment-shaped firing.
  if (kind === 'heartbeat') return <Activity className="w-3.5 h-3.5" />;
  return <Bot className="w-3.5 h-3.5" />;
}

function formatLocal(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString([], {
      year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    });
  } catch {
    return iso;
  }
}

function localInputToIso(value) {
  return value ? new Date(value).toISOString() : null;
}

function isoToLocalInput(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const RECURRENCE_OPTIONS = ['none', 'hourly', 'daily', 'weekly', 'cron'];

function browserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

function targetLabel(job, agents, flows, loops) {
  if (job.kind === 'flow') {
    const f = flows.find((x) => x.id === job.flow_id);
    return f?.name || job.flow_id || '—';
  }
  if (job.kind === 'loop') {
    const l = loops.find((x) => x.id === job.loop_id);
    return l?.name || job.loop_id || '—';
  }
  const a = agents.find((x) => x.id === job.agent_id);
  return a?.name || job.agent_id || '—';
}

// ---- create / edit modal ----------------------------------------------------

function DeploymentModal({ job, agents, flows, loops, environments, resources, workspace, onClose, onSaved }) {
  const { t } = useI18n();
  const isEdit = !!job;
  const [kind, setKind] = useState(job?.kind || 'agent_task');
  const [title, setTitle] = useState(job?.title || '');
  const [message, setMessage] = useState(job?.message || '');
  const [runAt, setRunAt] = useState(job ? isoToLocalInput(job.run_at) : '');
  const [recurrence, setRecurrence] = useState(job?.recurrence || 'none');
  const [cron, setCron] = useState(job?.cron || '');
  const [tz, setTz] = useState(job?.timezone || browserTimezone());
  const [catchUp, setCatchUp] = useState(job?.catch_up ?? false);
  const [agentId, setAgentId] = useState(job?.agent_id || '');
  const [flowId, setFlowId] = useState(job?.flow_id || '');
  const [loopId, setLoopId] = useState(job?.loop_id || '');
  const [environmentId, setEnvironmentId] = useState(job?.environment_id || '');
  const [budgetUsd, setBudgetUsd] = useState(job?.budget_usd ?? '');
  const [agentVersion, setAgentVersion] = useState(job?.agent_version ?? '');
  const [agentVersions, setAgentVersions] = useState([]);
  const [autoPauseAfter, setAutoPauseAfter] = useState(job?.auto_pause_after ?? 3);
  // The deployment's resources (agent_task only, docs/deployments.md
  // "Resources"): copied onto every task the job creates, never onto the
  // agent record.
  const [projectId, setProjectId] = useState(job?.project_id || '');
  const [fileIds, setFileIds] = useState(job?.file_ids || []);
  const [secretNames, setSecretNames] = useState(job?.secrets || []);
  const [newSecret, setNewSecret] = useState('');
  const [memoryPoolIds, setMemoryPoolIds] = useState(job?.memory_pool_ids || []);
  const [memoryAccess, setMemoryAccess] = useState(job?.memory_access || 'read');
  const [fileQuery, setFileQuery] = useState('');
  // Files uploaded from this form, on top of the workspace list the page
  // loaded, so a fresh upload is selectable at once.
  const [uploadedFiles, setUploadedFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // The agent is only known once it is picked (on create) or already fixed
  // (on edit, where the kind/agent selector above is hidden but job.agent_id
  // still names it) — either way, the version list is this agent's own, so a
  // change of agent on create clears whatever version was picked for the last one.
  const pinnedAgentId = isEdit ? (job?.agent_id || '') : agentId;
  useEffect(() => {
    if (!pinnedAgentId || kind !== 'agent_task') { setAgentVersions([]); return; }
    let cancelled = false;
    getAgentVersions(pinnedAgentId)
      .then(({ data }) => { if (!cancelled) setAgentVersions(data?.versions || []); })
      .catch(() => { if (!cancelled) setAgentVersions([]); });
    return () => { cancelled = true; };
  }, [pinnedAgentId, kind]);
  useEffect(() => {
    if (!isEdit) setAgentVersion('');
  }, [agentId, isEdit]);

  const handleSave = async () => {
    if (!title.trim()) { setError(t('deployments.errors.titleRequired')); return; }
    if (!runAt) { setError(t('deployments.errors.timeRequired')); return; }
    if (kind === 'flow' && !flowId) { setError(t('deployments.errors.pickFlow')); return; }
    if (kind === 'loop' && !loopId) { setError(t('deployments.errors.pickLoop')); return; }
    if (recurrence === 'cron' && !cron.trim()) { setError(t('deployments.errors.cronRequired')); return; }
    setSaving(true);
    setError('');
    try {
      const shared = {
        title: title.trim(),
        message,
        run_at: localInputToIso(runAt),
        recurrence,
        cron: recurrence === 'cron' ? cron.trim() : null,
        timezone: recurrence !== 'none' ? (tz.trim() || null) : null,
        catch_up: catchUp,
        environment_id: environmentId || null,
        budget_usd: budgetUsd === '' ? null : Number(budgetUsd),
        // Only meaningful together with an agent_id, on an agent_task job.
        agent_version: kind === 'agent_task' && agentVersion !== '' ? Number(agentVersion) : null,
        auto_pause_after: Number(autoPauseAfter) || 0,
        // Resources apply to agent task jobs only; a flow or loop job sends
        // them empty so the server has nothing to refuse.
        project_id: kind === 'agent_task' ? (projectId || null) : null,
        file_ids: kind === 'agent_task' ? fileIds : [],
        secrets: kind === 'agent_task' ? secretNames : [],
        memory_pool_ids: kind === 'agent_task' ? memoryPoolIds : [],
        memory_access: kind === 'agent_task' ? memoryAccess : null,
      };
      if (isEdit) {
        await updatePlanJob(job.id, shared);
      } else {
        await createPlanJob({
          kind,
          ...shared,
          workspace: workspace || null,
          agent_id: kind === 'agent_task' ? (agentId || null) : null,
          flow_id: kind === 'flow' ? flowId : null,
          loop_id: kind === 'loop' ? loopId : null,
        });
      }
      onSaved();
    } catch (err) {
      setError(err?.response?.data?.detail || t('deployments.errors.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 space-y-4 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-gray-900">{isEdit ? t('deployments.editDeployment') : t('deployments.newDeployment')}</h2>
          <button onClick={onClose} className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100">
            <X className="w-5 h-5" />
          </button>
        </div>

        {!isEdit && (
          <div className="flex gap-2">
            {[
              { value: 'agent_task', label: t('deployments.kinds.agentTask'), icon: Bot },
              { value: 'flow', label: t('deployments.kinds.flow'), icon: Workflow },
              { value: 'loop', label: t('deployments.kinds.loop'), icon: RotateCw },
            ].map(({ value, label, icon: Icon }) => (
              <button
                key={value}
                onClick={() => setKind(value)}
                className={`flex-1 flex flex-col items-center gap-1 px-3 py-3 rounded-lg border text-sm transition-colors ${
                  kind === value
                    ? 'border-indigo-500 bg-indigo-50 text-indigo-700'
                    : 'border-gray-200 text-gray-600 hover:bg-gray-50'
                }`}
              >
                <Icon className="w-5 h-5" />
                <span className="font-medium">{label}</span>
              </button>
            ))}
          </div>
        )}

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.title')}</label>
          <input
            type="text"
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>

        {!isEdit && kind === 'agent_task' && (
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.agent')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
              value={agentId}
              onChange={(e) => setAgentId(e.target.value)}
            >
              <option value="">{t('deployments.letTheOrchestratorDecide')}</option>
              {agents.map((a) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
            </select>
          </div>
        )}
        {!isEdit && kind === 'flow' && (
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.flow')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
              value={flowId}
              onChange={(e) => setFlowId(e.target.value)}
            >
              <option value="">{t('deployments.selectAFlow')}</option>
              {(flows || []).map((f) => <option key={f.id} value={f.id}>{f.name || f.id}</option>)}
            </select>
          </div>
        )}
        {!isEdit && kind === 'loop' && (
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.loop')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
              value={loopId}
              onChange={(e) => setLoopId(e.target.value)}
            >
              <option value="">{t('deployments.selectALoop')}</option>
              {(loops || []).map((l) => <option key={l.id} value={l.id}>{l.name || l.id}</option>)}
            </select>
          </div>
        )}

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.message')}</label>
          <textarea
            rows={3}
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
        </div>

        <div className="flex gap-3">
          <div className="flex-1">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.when')}</label>
            <DateInput
              mode="datetime"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
              value={runAt}
              onChange={setRunAt}
            />
          </div>
          <div className="w-36">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.repeat')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
              value={recurrence}
              onChange={(e) => setRecurrence(e.target.value)}
            >
              {RECURRENCE_OPTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
        </div>

        {recurrence !== 'none' && (
          <div className="flex gap-3">
            {recurrence === 'cron' && (
              <div className="flex-1">
                <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.cronExpression')}</label>
                <input
                  type="text"
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none"
                  value={cron}
                  onChange={(e) => setCron(e.target.value)}
                  placeholder="0 9 * * 1-5"
                />
              </div>
            )}
            <div className={recurrence === 'cron' ? 'w-44' : 'flex-1'}>
              <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.timezone')}</label>
              <input
                type="text"
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
                value={tz}
                onChange={(e) => setTz(e.target.value)}
                placeholder="Europe/Berlin"
              />
            </div>
          </div>
        )}

        {recurrence !== 'none' && (
          <CronHint recurrence={recurrence} cron={cron} timezone={tz} start={runAt} />
        )}

        {recurrence !== 'none' && (
          <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
            <input
              type="checkbox"
              checked={catchUp}
              onChange={(e) => setCatchUp(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-indigo-600"
            />
            {t('deployments.catchUp')}
          </label>
        )}

        {/* items-end keeps the three inputs on one baseline when a label
            wraps to two lines, instead of the wrapped one dropping lower. */}
        <div className="grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_9rem_9rem] gap-3 items-end">
          <div className="min-w-0">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.environment')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
              value={environmentId}
              onChange={(e) => setEnvironmentId(e.target.value)}
            >
              <option value="">{t('deployments.workspaceDefault')}</option>
              {(environments || []).map((env) => <option key={env.id} value={env.id}>{env.name}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider leading-tight">{t('deployments.budgetUsd')}</label>
            <input
              type="number" min="0" step="0.01"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
              value={budgetUsd}
              onChange={(e) => setBudgetUsd(e.target.value)}
              placeholder={t('deployments.uncapped')}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider leading-tight">{t('deployments.autoPauseAfter')}</label>
            <input
              type="number" min="0"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
              value={autoPauseAfter}
              onChange={(e) => setAutoPauseAfter(e.target.value)}
            />
          </div>
        </div>

        {kind === 'agent_task' && (() => {
          const toggleIn = (list, setList, id) => setList(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
          const SECRET_RE = /^[A-Z][A-Z0-9_]*$/;
          const addSecret = () => {
            const name = newSecret.trim().toUpperCase();
            if (!SECRET_RE.test(name)) { setError(t('deployments.errors.badSecretName')); return; }
            setError('');
            if (!secretNames.includes(name)) setSecretNames([...secretNames, name]);
            setNewSecret('');
          };
          const knownSecrets = [...new Set([...(resources?.secrets || []), ...secretNames])];
          const q = fileQuery.trim().toLowerCase();
          const known = new Set((resources?.files || []).map((f) => f.id));
          const allFiles = [...uploadedFiles.filter((f) => !known.has(f.id)), ...(resources?.files || [])];
          const files = allFiles.filter((f) => !q || (f.filename || f.name || f.id || '').toLowerCase().includes(q) || fileIds.includes(f.id));
          const onUpload = async (e) => {
            const picked = Array.from(e.target.files || []);
            e.target.value = '';
            if (!picked.length) return;
            setUploading(true);
            setError('');
            try {
              for (const file of picked) {
                const { data } = await uploadWorkspaceFileObject(workspace || 'default', file, { source: 'deployment' });
                if (data?.id) {
                  setUploadedFiles((prev) => (prev.some((f) => f.id === data.id) ? prev : [data, ...prev]));
                  setFileIds((prev) => (prev.includes(data.id) ? prev : [...prev, data.id]));
                }
              }
            } catch (err) {
              setError(err?.response?.data?.detail || t('deployments.errors.uploadFailed'));
            } finally {
              setUploading(false);
            }
          };
          const chipCls = (on) => `px-2.5 py-1 rounded-full text-xs font-semibold border ${on ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-gray-600 border-gray-200 hover:border-indigo-300'}`;
          return (
            <div className="rounded-lg border border-gray-200 p-3 space-y-3" data-testid="deployment-resources">
              <div>
                <div className="text-xs font-semibold text-gray-700 uppercase tracking-wider">{t('deployments.resources')}</div>
                <p className="text-xs text-gray-500 mt-0.5">{t('deployments.resourcesHint')}</p>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.project')}</label>
                <select
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
                  value={projectId}
                  onChange={(e) => setProjectId(e.target.value)}
                  aria-label={t('deployments.project')}
                >
                  <option value="">{t('deployments.noProject')}</option>
                  {(resources?.projects || []).map((p) => <option key={p.id} value={p.id}>{p.name || p.id}</option>)}
                </select>
              </div>
              <div>
                <div className="flex items-center justify-between gap-2 mb-1">
                  <label className="block text-xs font-medium text-gray-500 uppercase tracking-wider">{t('deployments.files')} {fileIds.length ? `(${fileIds.length})` : ''}</label>
                  <div className="flex items-center gap-2">
                    {allFiles.length > 8 && (
                      <input type="search" value={fileQuery} onChange={(e) => setFileQuery(e.target.value)} placeholder={t('deployments.filesSearch')}
                        className="border border-gray-200 rounded-lg px-2 py-1 text-xs focus:outline-none" />
                    )}
                    {/* Upload straight from the form: the file lands in the
                        workspace's file store and is selected here at once. */}
                    <label className={`relative inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border text-xs font-semibold cursor-pointer ${uploading ? 'opacity-50 cursor-wait' : 'hover:bg-gray-50'} border-gray-200 text-gray-700`}>
                      {uploading ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
                      {t('deployments.uploadFile')}
                      <input type="file" multiple className="sr-only" onChange={onUpload} disabled={uploading} aria-label={t('deployments.uploadFile')} />
                    </label>
                  </div>
                </div>
                {files.length === 0 ? (
                  <p className="text-xs text-gray-400 italic">{t('deployments.noFiles')}</p>
                ) : (
                  <div className="flex flex-wrap gap-1.5 max-h-28 overflow-y-auto">
                    {files.slice(0, 60).map((f) => (
                      <button key={f.id} type="button" onClick={() => toggleIn(fileIds, setFileIds, f.id)} className={chipCls(fileIds.includes(f.id))} title={f.id}>
                        {f.filename || f.name || f.id}
                      </button>
                    ))}
                  </div>
                )}
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.secrets')} {secretNames.length ? `(${secretNames.length})` : ''}</label>
                <p className="text-xs text-gray-500 mb-1.5">{t('deployments.secretsHint')}</p>
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {knownSecrets.map((name) => (
                    <button key={name} type="button" onClick={() => toggleIn(secretNames, setSecretNames, name)} className={`${chipCls(secretNames.includes(name))} font-mono`}>
                      {name}
                    </button>
                  ))}
                </div>
                <div className="flex gap-2">
                  <input type="text" value={newSecret} onChange={(e) => setNewSecret(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addSecret(); } }}
                    placeholder={t('deployments.addSecretPlaceholder')} aria-label={t('deployments.addSecretPlaceholder')}
                    className="flex-1 border border-gray-200 rounded-lg px-3 py-1.5 text-xs font-mono focus:outline-none" />
                  <button type="button" onClick={addSecret} className="px-3 py-1.5 text-xs font-semibold border border-gray-200 rounded-lg hover:bg-gray-50">{t('deployments.addSecret')}</button>
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.memoryPools')} {memoryPoolIds.length ? `(${memoryPoolIds.length})` : ''}</label>
                <p className="text-xs text-gray-500 mb-1.5">{t('deployments.memoryPoolsHint')}</p>
                {(resources?.pools || []).length === 0 ? (
                  <p className="text-xs text-gray-400 italic">{t('deployments.noPools')}</p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {(resources?.pools || []).map((m) => (
                      <button key={m.id} type="button" onClick={() => toggleIn(memoryPoolIds, setMemoryPoolIds, m.id)} className={chipCls(memoryPoolIds.includes(m.id))} title={m.id}>
                        {m.name || m.id}
                      </button>
                    ))}
                  </div>
                )}
                {memoryPoolIds.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-4" role="radiogroup" aria-label={t('deployments.memoryAccess')}>
                    {[
                      { value: 'read', label: t('deployments.memoryAccessRead'), hint: t('deployments.memoryAccessReadHint') },
                      { value: 'write', label: t('deployments.memoryAccessWrite'), hint: t('deployments.memoryAccessWriteHint') },
                    ].map((opt) => (
                      <label key={opt.value} className="flex items-start gap-2 text-xs text-gray-700 cursor-pointer">
                        <input type="radio" name="memory_access" value={opt.value} checked={memoryAccess === opt.value}
                          onChange={() => setMemoryAccess(opt.value)} className="mt-0.5 accent-indigo-600" />
                        <span><span className="font-medium">{opt.label}</span><span className="block text-gray-500">{opt.hint}</span></span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })()}

        {kind === 'agent_task' && pinnedAgentId && agentVersions.length > 0 && (
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">
              {t('agentVersionPin.jobFieldLabel')}
            </label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none"
              value={agentVersion}
              onChange={(e) => setAgentVersion(e.target.value)}
              aria-label={t('agentVersionPin.jobFieldLabel')}
            >
              <option value="">{t('agentVersionPin.jobFieldLive')}</option>
              {agentVersions.slice().reverse().map((v) => (
                <option key={v.version} value={v.version}>
                  {t('agentVersionPin.versionOption', { version: v.version })}
                </option>
              ))}
            </select>
          </div>
        )}

        {error && <p className="text-sm text-red-600">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50">
            {t('deployments.cancel')}
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {saving ? <Loader className="w-4 h-4 animate-spin" /> : <Rocket className="w-4 h-4" />}
            {isEdit ? t('deployments.save') : t('deployments.deploy')}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- firing journal drawer --------------------------------------------------

function JournalDrawer({ job, onClose }) {
  const { t } = useI18n();
  const [fires, setFires] = useState([]);
  const [loading, setLoading] = useState(true);
  const [onlyErrors, setOnlyErrors] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getJobFires(job.id, { limit: 50, only_errors: onlyErrors })
      .then(({ data }) => { if (!cancelled) setFires(data || []); })
      .catch(() => { if (!cancelled) setFires([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [job.id, onlyErrors]);

  // The checkbox is a user event, not an effect, so setting the spinner back
  // on here (ahead of the refetch above) is the one place that may do it
  // synchronously.
  const toggleOnlyErrors = (checked) => {
    setLoading(true);
    setOnlyErrors(checked);
  };

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex justify-end" onClick={onClose}>
      <div className="bg-white h-full w-full max-w-lg shadow-xl p-6 space-y-4 overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-gray-900">{t('deployments.journalFor', { name: job.title })}</h2>
          <button onClick={onClose} className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100">
            <X className="w-5 h-5" />
          </button>
        </div>
        <label className="flex items-center gap-2 text-xs text-gray-500 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={onlyErrors}
            onChange={(e) => toggleOnlyErrors(e.target.checked)}
            className="h-3.5 w-3.5 rounded border-gray-300 text-indigo-600"
          />
          {t('deployments.onlyFailures')}
        </label>
        {loading ? (
          <PageLoader size="sm" />
        ) : fires.length === 0 ? (
          <p className="text-sm text-gray-400 py-6 text-center">{t('deployments.noFires')}</p>
        ) : (
          <div className="divide-y divide-gray-100">
            {fires.map((f) => (
              <div key={f.id} className="py-3 flex items-start gap-3">
                <div className={`mt-0.5 p-1 rounded-full ${f.ok ? 'bg-emerald-100 text-emerald-600' : 'bg-red-100 text-red-600'}`}>
                  {f.ok ? <CheckCircle className="w-3.5 h-3.5" /> : <XCircle className="w-3.5 h-3.5" />}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-gray-500 flex items-center gap-2">
                    <span>{formatLocal(f.at)}</span>
                    <span className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 text-[10px] uppercase">{f.trigger}</span>
                    {f.error_type && <span className="text-red-500">{f.error_type}</span>}
                  </div>
                  {f.error && <div className="text-xs text-red-600 mt-0.5">{f.error}</div>}
                  {f.task_id && (
                    <Link to={`/tasks/${f.task_id}`} className="text-xs text-indigo-600 hover:underline mt-0.5 inline-block">
                      {t('deployments.viewTask')}
                    </Link>
                  )}
                </div>
                {typeof f.duration_ms === 'number' && (
                  <span className="text-[11px] text-gray-400 shrink-0">{f.duration_ms}ms</span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ---- main page ----------------------------------------------------------------

export default function Deployments() {
  const { t } = useI18n();
  const { workspaceFilter } = useWorkspace();

  const [jobs, setJobs] = useState([]);
  const [agents, setAgents] = useState([]);
  const [flows, setFlows] = useState([]);
  const [loops, setLoops] = useState([]);
  const [environments, setEnvironments] = useState([]);
  // What a deployment may attach: the workspace's projects, files, secret
  // names and memory pools. Any list that cannot be loaded (no access, no
  // store) is simply empty; the form still saves ids typed elsewhere.
  const [resources, setResources] = useState({ projects: [], files: [], secrets: [], pools: [] });
  const [loading, setLoading] = useState(true);
  const [showFinished, setShowFinished] = useState(false);
  const [modalJob, setModalJob] = useState(undefined); // undefined = closed, null = create, object = edit
  const [journalJob, setJournalJob] = useState(null);
  const [acting, setActing] = useState({});

  const fetchData = useCallback(async () => {
    try {
      const { data } = await getPlanJobs(workspaceFilter, undefined, ['agent_task', 'flow', 'loop', 'heartbeat']);
      setJobs(data || []);
    } catch (err) {
      console.error('Failed to load deployments', err);
    } finally {
      setLoading(false);
    }
  }, [workspaceFilter]);

  useEffect(() => { setLoading(true); fetchData(); }, [fetchData]);

  useEffect(() => {
    getAgents(workspaceFilter).then((r) => setAgents(r.data || [])).catch(() => {});
    listFlows(workspaceFilter).then((r) => setFlows(r.data || [])).catch(() => {});
    getLoops(workspaceFilter).then((r) => setLoops(r.data || [])).catch(() => {});
    getEnvironments(workspaceFilter).then((r) => setEnvironments(r.data || [])).catch(() => {});
    const ws = workspaceFilter || 'default';
    const quiet = (p) => Promise.resolve().then(() => p).catch(() => ({ data: null }));
    Promise.all([
      quiet(getProjects(workspaceFilter)),
      quiet(listWorkspaceFiles(ws, { limit: 200 })),
      quiet(getWorkspaceSecrets(ws)),
      quiet(getSharedMemories(workspaceFilter)),
    ]).then(([pr, fr, sr, mr]) => setResources({
      projects: Array.isArray(pr.data) ? pr.data : [],
      files: Array.isArray(fr.data?.files) ? fr.data.files : [],
      secrets: [...new Set((Array.isArray(sr.data) ? sr.data : []).map((row) => row.name).filter(Boolean))],
      pools: Array.isArray(mr.data) ? mr.data : [],
    }));
  }, [workspaceFilter]);

  const act = async (id, fn) => {
    setActing((s) => ({ ...s, [id]: true }));
    try {
      await fn();
      await fetchData();
    } catch (err) {
      window.alert(err?.response?.data?.detail || t('deployments.errors.actionFailed'));
    } finally {
      setActing((s) => ({ ...s, [id]: false }));
    }
  };

  const pendingJobs = jobs.filter((j) => ['scheduled', 'paused'].includes(j.status));
  const finishedJobs = jobs.filter((j) => !['scheduled', 'paused'].includes(j.status));
  const visibleJobs = showFinished ? [...pendingJobs, ...finishedJobs] : pendingJobs;

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={Rocket}
        title={t('deployments.deployments')}
        description={t('deployments.pageDescription')}
        actions={<>
          <label className="flex items-center gap-2 text-xs text-gray-500 cursor-pointer select-none mr-1">
            <input
              type="checkbox"
              checked={showFinished}
              onChange={(e) => setShowFinished(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-gray-300 text-indigo-600"
            />
            {t('deployments.showFinished')}
          </label>
          <button
            onClick={fetchData}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" /> {t('deployments.refresh')}
          </button>
          <button
            onClick={() => setModalJob(null)}
            className="flex items-center gap-2 px-3 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
          >
            <Plus className="w-4 h-4" /> {t('deployments.newDeployment')}
          </button>
        </>}
      />
      <DeployedApps workspace={workspaceFilter || undefined} t={t} />

      {loading ? (
        <div className="bg-white rounded-xl border border-gray-200"><PageLoader /></div>
      ) : visibleJobs.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 text-center py-16">
          <Rocket className="w-10 h-10 text-gray-300 mx-auto mb-3" />
          <p className="text-gray-500 text-sm">{t('deployments.nothingDeployed')}</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50">
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('deployments.name')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('deployments.target')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('deployments.schedule')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('deployments.environment')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('deployments.budget')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('deployments.status')}</th>
                <th className="text-left px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('deployments.lastFire')}</th>
                <th className="text-right px-4 py-2.5 text-[10px] font-bold uppercase tracking-wider text-gray-400">{t('deployments.actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {visibleJobs.map((job) => {
                const pending = ['scheduled', 'paused'].includes(job.status);
                const busy = acting[job.id];
                const env = environments.find((e) => e.id === job.environment_id);
                const upcoming = job.upcoming_runs_at || [];
                return (
                  <tr key={job.id} className="hover:bg-gray-50 transition-colors">
                    <td className="px-4 py-3 min-w-0">
                      <div className="font-medium text-gray-900 truncate max-w-[14rem]">{job.title}</div>
                      {job.consecutive_errors > 0 && (
                        <div className="text-[11px] text-red-500 flex items-center gap-1 mt-0.5">
                          <AlertTriangle className="w-3 h-3" /> {t('deployments.consecutiveErrors', { count: job.consecutive_errors })}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-gray-600 text-xs">
                      <span className="inline-flex items-center gap-1.5">
                        <KindIcon kind={job.kind} />
                        {targetLabel(job, agents, flows, loops)}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-600" title={upcoming.length ? upcoming.map(formatLocal).join('\n') : ''}>
                      <div>{formatLocal(job.run_at)}</div>
                      {job.recurrence !== 'none' && (
                        <div className="text-gray-400 flex items-center gap-1 mt-0.5">
                          <Repeat className="w-3 h-3" />
                          {job.recurrence === 'cron' ? (job.cron || 'cron') : job.recurrence}
                          {job.timezone ? ` · ${job.timezone}` : ''}
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-600">{env?.name || <span className="text-gray-400">—</span>}</td>
                    <td className="px-4 py-3 text-xs text-gray-600">
                      {job.budget_usd ? `$${job.budget_usd}` : <span className="text-gray-400">{t('deployments.uncapped')}</span>}
                    </td>
                    <td className="px-4 py-3"><StatusBadge status={job.status} pausedReason={job.paused_reason} t={t} /></td>
                    <td className="px-4 py-3 text-xs">
                      {job.last_fire ? (
                        <span className={`inline-flex items-center gap-1 ${job.last_fire.ok ? 'text-emerald-600' : 'text-red-600'}`}>
                          {job.last_fire.ok ? <CheckCircle className="w-3 h-3" /> : <XCircle className="w-3 h-3" />}
                          {formatLocal(job.last_fire.at)}
                        </span>
                      ) : <span className="text-gray-400">—</span>}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center justify-end gap-1">
                        {busy ? (
                          <Loader className="w-4 h-4 animate-spin text-gray-400" />
                        ) : (
                          <>
                            <button
                              title={t('deployments.viewJournal')}
                              onClick={() => setJournalJob(job)}
                              className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50"
                            ><History className="w-4 h-4" /></button>
                            <button
                              title={t('deployments.runNow')}
                              onClick={() => act(job.id, () => runPlanJobNow(job.id))}
                              className="p-1.5 rounded text-gray-400 hover:text-green-600 hover:bg-green-50"
                            ><Zap className="w-4 h-4" /></button>
                            {pending && (
                              <button
                                title={t('deployments.edit')}
                                onClick={() => setModalJob(job)}
                                className="p-1.5 rounded text-gray-400 hover:text-indigo-600 hover:bg-indigo-50"
                              ><Pencil className="w-4 h-4" /></button>
                            )}
                            {pending && (job.status === 'scheduled' ? (
                              <button
                                title={t('deployments.pause')}
                                onClick={() => act(job.id, () => pausePlanJob(job.id))}
                                className="p-1.5 rounded text-gray-400 hover:text-amber-600 hover:bg-amber-50"
                              ><Pause className="w-4 h-4" /></button>
                            ) : (
                              <button
                                title={t('deployments.resume')}
                                onClick={() => act(job.id, () => resumePlanJob(job.id))}
                                className="p-1.5 rounded text-gray-400 hover:text-green-600 hover:bg-green-50"
                              ><Play className="w-4 h-4" /></button>
                            ))}
                            {pending && (
                              <button
                                title={t('deployments.cancel')}
                                onClick={() => act(job.id, () => cancelPlanJob(job.id))}
                                className="p-1.5 rounded text-gray-400 hover:text-red-600 hover:bg-red-50"
                              ><X className="w-4 h-4" /></button>
                            )}
                            <button
                              title={t('deployments.delete')}
                              onClick={() => {
                                if (window.confirm(t('deployments.confirmDelete', { name: job.title }))) act(job.id, () => deletePlanJob(job.id));
                              }}
                              className="p-1.5 rounded text-gray-400 hover:text-red-600 hover:bg-red-50"
                            ><Trash2 className="w-4 h-4" /></button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {modalJob !== undefined && (
        <DeploymentModal
          job={modalJob}
          agents={agents}
          flows={flows}
          loops={loops}
          environments={environments}
          resources={resources}
          workspace={workspaceFilter}
          onClose={() => setModalJob(undefined)}
          onSaved={() => { setModalJob(undefined); fetchData(); }}
        />
      )}
      {journalJob && (
        <JournalDrawer job={journalJob} onClose={() => setJournalJob(null)} />
      )}
    </PageContainer>
  );
}
