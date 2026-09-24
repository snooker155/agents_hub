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
} from '../api';
import { getAgentVersions } from '../api/agentVersions';
import {
  Rocket,
  Plus,
  RefreshCw,
  Loader,
  Trash2,
  Pause,
  Play,
  X,
  XCircle,
  CheckCircle,
  Clock,
  AlertCircle,
  Repeat,
  Bot,
  Pencil,
  Zap,
  Workflow,
  RotateCw,
  History,
  AlertTriangle,
} from 'lucide-react';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import DateInput from '../components/DateInput';

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

function DeploymentModal({ job, agents, flows, loops, environments, workspace, onClose, onSaved }) {
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
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>

        {!isEdit && kind === 'agent_task' && (
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.agent')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
          />
        </div>

        <div className="flex gap-3">
          <div className="flex-1">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.when')}</label>
            <DateInput
              mode="datetime"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={runAt}
              onChange={setRunAt}
            />
          </div>
          <div className="w-36">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.repeat')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                value={tz}
                onChange={(e) => setTz(e.target.value)}
                placeholder="Europe/Berlin"
              />
            </div>
          </div>
        )}

        {recurrence !== 'none' && (
          <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
            <input
              type="checkbox"
              checked={catchUp}
              onChange={(e) => setCatchUp(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
            />
            {t('deployments.catchUp')}
          </label>
        )}

        <div className="flex gap-3">
          <div className="flex-1">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.environment')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={environmentId}
              onChange={(e) => setEnvironmentId(e.target.value)}
            >
              <option value="">{t('deployments.workspaceDefault')}</option>
              {(environments || []).map((env) => <option key={env.id} value={env.id}>{env.name}</option>)}
            </select>
          </div>
          <div className="w-32">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.budgetUsd')}</label>
            <input
              type="number" min="0" step="0.01"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={budgetUsd}
              onChange={(e) => setBudgetUsd(e.target.value)}
              placeholder={t('deployments.uncapped')}
            />
          </div>
          <div className="w-32">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('deployments.autoPauseAfter')}</label>
            <input
              type="number" min="0"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={autoPauseAfter}
              onChange={(e) => setAutoPauseAfter(e.target.value)}
            />
          </div>
        </div>

        {kind === 'agent_task' && pinnedAgentId && agentVersions.length > 0 && (
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">
              {t('agentVersionPin.jobFieldLabel')}
            </label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
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
            className="h-3.5 w-3.5 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
          />
          {t('deployments.onlyFailures')}
        </label>
        {loading ? (
          <div className="flex justify-center py-10"><Loader className="w-5 h-5 animate-spin text-indigo-500" /></div>
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
  const [loading, setLoading] = useState(true);
  const [showFinished, setShowFinished] = useState(false);
  const [modalJob, setModalJob] = useState(undefined); // undefined = closed, null = create, object = edit
  const [journalJob, setJournalJob] = useState(null);
  const [acting, setActing] = useState({});

  const fetchData = useCallback(async () => {
    try {
      const { data } = await getPlanJobs(workspaceFilter, undefined, ['agent_task', 'flow', 'loop']);
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

      <label className="flex items-center gap-2 text-xs text-gray-500 cursor-pointer select-none">
        <input
          type="checkbox"
          checked={showFinished}
          onChange={(e) => setShowFinished(e.target.checked)}
          className="h-3.5 w-3.5 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
        />
        {t('deployments.showFinished')}
      </label>

      {loading ? (
        <div className="bg-white rounded-xl border border-gray-200 flex justify-center py-16">
          <Loader className="w-6 h-6 animate-spin text-indigo-500" />
        </div>
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
