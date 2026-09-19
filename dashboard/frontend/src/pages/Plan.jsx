import { useState, useEffect, useCallback, useRef } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import { useLiveRefetch } from '../components/stream';
import {
  getPlanJobs,
  createPlanJob,
  updatePlanJob,
  deletePlanJob,
  pausePlanJob,
  resumePlanJob,
  cancelPlanJob,
  runPlanJobNow,
  getNotifications,
  markNotificationRead,
  markAllNotificationsRead,
  deleteNotification,
  getAgents,
  getTelegramConfig,
  listFlows,
} from '../api';
import {
  CalendarClock,
  Bell,
  BellRing,
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
  MailOpen,
  Zap,
  Send,
  Workflow,
} from 'lucide-react';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import DateInput from '../components/DateInput';
// ---- helpers ----------------------------------------------------------------

const STATUS_STYLES = {
  scheduled: { bg: 'bg-blue-100',   text: 'text-blue-700',   icon: Clock },
  paused:    { bg: 'bg-amber-100',  text: 'text-amber-700',  icon: Pause },
  fired:     { bg: 'bg-green-100',  text: 'text-green-700',  icon: CheckCircle },
  cancelled: { bg: 'bg-gray-100',   text: 'text-gray-600',   icon: X },
  failed:    { bg: 'bg-red-100',    text: 'text-red-700',    icon: XCircle },
};

function StatusBadge({ status }) {
  const s = STATUS_STYLES[status] || { bg: 'bg-gray-100', text: 'text-gray-500', icon: AlertCircle };
  const Icon = s.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${s.bg} ${s.text}`}>
      <Icon className="w-3 h-3" />
      {status}
    </span>
  );
}

function KindBadge({ kind }) {
  const { t } = useI18n();
  if (kind === 'agent_task') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-violet-100 text-violet-700">
        <Bot className="w-3 h-3" /> {t('plan.agentTask')}
      </span>
    );
  }
  if (kind === 'flow') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-700">
        <Workflow className="w-3 h-3" /> {t('plan.flow2')}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-sky-100 text-sky-700">
      <Bell className="w-3 h-3" /> {t('plan.notification')}
    </span>
  );
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

function relativeTime(iso, t) {
  if (!iso) return '';
  const diff = new Date(iso) - new Date();
  const abs = Math.abs(diff);
  const mins = Math.round(abs / 60000);
  let text;
  if (mins < 1) text = t('plan.relative.lessThanAMinute');
  else if (mins < 60) text = t('plan.relative.minutes', { count: mins });
  else if (mins < 60 * 24) text = t('plan.relative.hours', { count: Math.round(mins / 60) });
  else text = t('plan.relative.days', { count: Math.round(mins / (60 * 24)) });
  return diff >= 0 ? t('plan.relative.inFuture', { time: text }) : t('plan.relative.inPast', { time: text });
}

// Convert a datetime-local input value to ISO with local offset preserved.
function localInputToIso(value) {
  return value ? new Date(value).toISOString() : null;
}

// Convert an ISO string to a datetime-local input value (local time).
function isoToLocalInput(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const RECURRENCE_OPTIONS = ['none', 'hourly', 'daily', 'weekly'];

// ---- create / edit modal ----------------------------------------------------

function JobModal({ job, agents, flows, onClose, onSaved, workspace, telegram }) {
  const { t } = useI18n();
  const isEdit = !!job;
  const [kind, setKind] = useState(job?.kind || 'notification');
  const [title, setTitle] = useState(job?.title || '');
  const [message, setMessage] = useState(job?.message || '');
  const [runAt, setRunAt] = useState(job ? isoToLocalInput(job.run_at) : '');
  const [recurrence, setRecurrence] = useState(job?.recurrence || 'none');
  const [agentId, setAgentId] = useState(job?.agent_id || '');
  const [flowId, setFlowId] = useState(job?.flow_id || '');
  const [seedText, setSeedText] = useState(job?.seed ? JSON.stringify(job.seed, null, 2) : '');
  const [maxConcurrent, setMaxConcurrent] = useState(job?.max_concurrent ?? 1);
  const [toTelegram, setToTelegram] = useState((job?.channels || []).includes('telegram'));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // Delivery to Telegram needs the integration enabled with a bot token.
  const tgReady = !!(telegram?.has_token && telegram?.enabled);
  const tgHint = !telegram?.has_token
    ? t('plan.telegram.noToken')
    : !telegram?.enabled
      ? t('plan.telegram.disabled')
      : t('plan.telegram.ready');
  const channels = tgReady && toTelegram ? ['dashboard', 'telegram'] : ['dashboard'];

  const handleSave = async () => {
    if (!title.trim()) { setError(t('plan.errors.titleRequired')); return; }
    if (!runAt) { setError(t('plan.errors.timeRequired')); return; }
    if (kind === 'flow' && !flowId) { setError(t('plan.errors.pickFlow')); return; }
    let seed = null;
    if (kind === 'flow' && seedText.trim()) {
      try {
        seed = JSON.parse(seedText);
        if (typeof seed !== 'object' || Array.isArray(seed)) throw new Error('not-object');
      } catch {
        setError(t('plan.errors.seedJson')); return;
      }
    }
    setSaving(true);
    setError('');
    try {
      if (isEdit) {
        await updatePlanJob(job.id, {
          title: title.trim(),
          message,
          run_at: localInputToIso(runAt),
          recurrence,
          agent_id: kind === 'agent_task' ? (agentId || null) : null,
          channels,
        });
      } else {
        await createPlanJob({
          kind,
          title: title.trim(),
          message,
          run_at: localInputToIso(runAt),
          recurrence,
          workspace: workspace || null,
          agent_id: kind === 'agent_task' ? (agentId || null) : null,
          flow_id: kind === 'flow' ? (flowId || null) : null,
          seed: kind === 'flow' ? seed : null,
          max_concurrent: kind === 'flow' ? (Number(maxConcurrent) || 0) : 1,
          channels,
        });
      }
      onSaved();
    } catch (err) {
      setError(err?.response?.data?.detail || t('plan.errors.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 space-y-4" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-gray-900">{isEdit ? t('plan.editScheduledJob') : t('plan.schedule')}</h2>
          <button onClick={onClose} className="p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100">
            <X className="w-5 h-5" />
          </button>
        </div>

        {!isEdit && (
          <div className="flex gap-2">
            {[
              { value: 'notification', label: t('plan.kinds.notification'), icon: Bell, hint: t('plan.kinds.notificationHint') },
              { value: 'agent_task', label: t('plan.kinds.agentTask'), icon: Bot, hint: t('plan.kinds.agentTaskHint') },
              { value: 'flow', label: t('plan.kinds.flow'), icon: Workflow, hint: t('plan.kinds.flowHint') },
            ].map(({ value, label, icon: Icon, hint }) => (
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
                <span className="text-[11px] text-gray-400">{hint}</span>
              </button>
            ))}
          </div>
        )}

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('plan.title')}</label>
          <input
            type="text"
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            value={title}
            onChange={e => setTitle(e.target.value)}
            placeholder={kind === 'agent_task' ? t('plan.taskTitle') : t('plan.reminderTitle')}
          />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">
            {kind === 'agent_task' ? t('plan.taskDescription') : t('plan.message')}
          </label>
          <textarea
            rows={3}
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
            value={message}
            onChange={e => setMessage(e.target.value)}
            placeholder={kind === 'agent_task' ? t('plan.taskPlaceholder') : t('plan.notificationPlaceholder')}
          />
        </div>

        <div className="flex gap-3">
          <div className="flex-1">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('plan.when')}</label>
            <DateInput
              mode="datetime"
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={runAt}
              onChange={setRunAt}
            />
          </div>
          <div className="w-36">
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('plan.repeat')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={recurrence}
              onChange={e => setRecurrence(e.target.value)}
            >
              {RECURRENCE_OPTIONS.map(r => <option key={r} value={r}>{r}</option>)}
            </select>
          </div>
        </div>

        {kind === 'agent_task' && (
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('plan.agent')}</label>
            <select
              className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              value={agentId}
              onChange={e => setAgentId(e.target.value)}
            >
              <option value="">{t('plan.letTheOrchestratorDecide')}</option>
              {agents.map(a => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
            </select>
          </div>
        )}

        {kind === 'flow' && !isEdit && (
          <div className="space-y-3">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('plan.flow')}</label>
              <select
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                value={flowId}
                onChange={e => setFlowId(e.target.value)}
              >
                <option value="">{t('plan.selectAFlow')}</option>
                {(flows || []).map(f => <option key={f.id} value={f.id}>{f.name || f.id}</option>)}
              </select>
            </div>
            <div className="flex gap-3">
              <div className="w-40">
                <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('plan.maxConcurrent')}</label>
                <input
                  type="number" min="0"
                  className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  value={maxConcurrent}
                  onChange={e => setMaxConcurrent(e.target.value)}
                />
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1 uppercase tracking-wider">{t('plan.seedJsonOptional')}</label>
              <textarea
                rows={3}
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
                value={seedText}
                onChange={e => setSeedText(e.target.value)}
                placeholder='{"key": "value"}'
              />
              <p className="text-[11px] text-gray-400 mt-1">{t('plan.mergedIntoTheFlowApos')}</p>
            </div>
          </div>
        )}

        <div className={`rounded-lg border px-3 py-2.5 ${tgReady ? 'border-gray-200' : 'border-gray-100 bg-gray-50'}`}>
          <label className={`flex items-center gap-2 text-sm ${tgReady ? 'cursor-pointer text-gray-700' : 'cursor-not-allowed text-gray-400'}`}>
            <input
              type="checkbox"
              checked={tgReady && toTelegram}
              disabled={!tgReady}
              onChange={e => setToTelegram(e.target.checked)}
              className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 disabled:opacity-50"
            />
            <Send className="w-4 h-4" />
            <span className="font-medium">{t('plan.alsoSendToTelegram')}</span>
          </label>
          <p className={`text-[11px] mt-1 ml-6 ${tgReady ? 'text-gray-400' : 'text-amber-600'}`}>
            {tgReady
              ? (kind === 'agent_task'
                  ? t('plan.telegram.taskStartedHint')
                  : t('plan.telegram.bothHint'))
              : tgHint}
          </p>
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <div className="flex justify-end gap-2 pt-2">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50">
            {t('plan.cancel')}
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {saving ? <Loader className="w-4 h-4 animate-spin" /> : <CalendarClock className="w-4 h-4" />}
            {isEdit ? 'Save' : 'Schedule'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- main page ----------------------------------------------------------------

export default function Plan() {
  const { t } = useI18n();
  const { workspaceFilter, liveUpdates } = useWorkspace();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get('tab') === 'notifications' ? 'notifications' : 'jobs';
  // Deep-link from a chat reply: ?job=<id> scrolls to that job and highlights
  // it (see common/entity_links.py).
  const linkedJobId = searchParams.get('job') || '';
  const linkedRowRef = useRef(null);

  const [jobs, setJobs] = useState([]);
  const [notifications, setNotifications] = useState([]);
  const [agents, setAgents] = useState([]);
  const [flows, setFlows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showFinished, setShowFinished] = useState(false);
  const [modalJob, setModalJob] = useState(undefined); // undefined = closed, null = create, object = edit
  const [acting, setActing] = useState({});
  const [telegram, setTelegram] = useState({ enabled: false, has_token: false });

  const fetchData = useCallback(async () => {
    try {
      const [jobsRes, notesRes] = await Promise.all([
        getPlanJobs(workspaceFilter),
        getNotifications({ ...(workspaceFilter ? { workspace: workspaceFilter } : {}), limit: 200 }),
      ]);
      setJobs(jobsRes.data);
      setNotifications(notesRes.data);
    } catch (err) {
      console.error('Failed to load plan data', err);
    } finally {
      setLoading(false);
    }
  }, [workspaceFilter]);

  useEffect(() => {
    setLoading(true);
    fetchData();
  }, [fetchData, liveUpdates]);
  // One hook, one debounce: jobs and notifications come from the same
  // fetchData, and a firing job emits both events, which used to cost two
  // full reloads. 1s rather than the default 300ms because a task's runs
  // trickle in over seconds, not in one instant burst.
  useLiveRefetch(fetchData, {
    sources: [
      { type: 'plan.changed' },
      { channel: '__notifications__', type: 'notification' },
    ],
    enabled: liveUpdates,
    debounceMs: 1000,
  });

  // A linked job that already ran sits in the "finished" group, which is hidden
  // by default — reveal it rather than landing the user on an empty list.
  useEffect(() => {
    if (!linkedJobId || showFinished) return;
    const job = jobs.find(j => j.id === linkedJobId);
    if (job && !['scheduled', 'paused'].includes(job.status)) setShowFinished(true);
  }, [linkedJobId, jobs, showFinished]);

  useEffect(() => {
    if (linkedRowRef.current) {
      linkedRowRef.current.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }
  }, [linkedJobId, jobs, showFinished]);

  useEffect(() => {
    getAgents(workspaceFilter).then(r => setAgents(r.data || [])).catch(() => {});
    listFlows(workspaceFilter).then(r => setFlows(r.data || [])).catch(() => {});
  }, [workspaceFilter]);

  useEffect(() => {
    getTelegramConfig()
      .then(r => setTelegram({ enabled: !!r.data?.enabled, has_token: !!r.data?.has_token }))
      .catch(() => setTelegram({ enabled: false, has_token: false }));
  }, []);

  const setTab = (t) => setSearchParams(t === 'notifications' ? { tab: 'notifications' } : {});

  const act = async (id, fn) => {
    setActing(s => ({ ...s, [id]: true }));
    try {
      await fn();
      await fetchData();
    } catch (err) {
      window.alert(err?.response?.data?.detail || t('plan.errors.actionFailed'));
    } finally {
      setActing(s => ({ ...s, [id]: false }));
    }
  };

  const pendingJobs = jobs.filter(j => ['scheduled', 'paused'].includes(j.status));
  const finishedJobs = jobs.filter(j => !['scheduled', 'paused'].includes(j.status));
  const visibleJobs = showFinished ? [...pendingJobs, ...finishedJobs] : pendingJobs;
  const unread = notifications.filter(n => !n.read).length;

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={CalendarClock}
        title={t('plan.plan')}
        description={t('plan.scheduledJobsFutureRemindersAnd')}
        actions={<>
          <button
            onClick={fetchData}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" /> {t('plan.refresh')}
          </button>
          <button
            onClick={() => setModalJob(null)}
            className="flex items-center gap-2 px-3 py-2 text-sm text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
          >
            <Plus className="w-4 h-4" /> {t('plan.schedule')}
          </button>
        </>}
      />

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200">
        {[
          { id: 'jobs', label: 'Scheduled', icon: CalendarClock, count: pendingJobs.length },
          { id: 'notifications', label: 'Notifications', icon: BellRing, count: unread },
        ].map(({ id, label, icon: Icon, count }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${
              tab === id
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <Icon className="w-4 h-4" />
            {label}
            {count > 0 && (
              <span className={`px-1.5 py-0.5 rounded-full text-[10px] font-semibold ${
                tab === id ? 'bg-indigo-100 text-indigo-700' : 'bg-gray-100 text-gray-500'
              }`}>{count}</span>
            )}
          </button>
        ))}
        {tab === 'jobs' && (
          <label className="ml-auto flex items-center gap-2 text-xs text-gray-500 pb-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={showFinished}
              onChange={e => setShowFinished(e.target.checked)}
              className="h-3.5 w-3.5 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
            />
            {t('plan.showFiredCancelled')}
          </label>
        )}
        {tab === 'notifications' && unread > 0 && (
          <button
            onClick={() => act('all', () => markAllNotificationsRead(workspaceFilter))}
            className="ml-auto flex items-center gap-1.5 text-xs text-indigo-600 hover:text-indigo-800 pb-2"
          >
            <MailOpen className="w-3.5 h-3.5" /> {t('plan.markAllRead')}
          </button>
        )}
      </div>

      {loading ? (
        <div className="bg-white rounded-xl border border-gray-200 flex justify-center py-16">
          <Loader className="w-6 h-6 animate-spin text-indigo-500" />
        </div>
      ) : tab === 'jobs' ? (
        visibleJobs.length === 0 ? (
          <div className="bg-white rounded-xl border border-gray-200 text-center py-16">
            <CalendarClock className="w-10 h-10 text-gray-300 mx-auto mb-3" />
            <p className="text-gray-500 text-sm">{t('plan.nothingScheduled')}</p>
            <p className="text-gray-400 text-xs mt-1">
              {t('plan.emptyHint')}
            </p>
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <div className="hidden md:grid grid-cols-[minmax(0,2fr)_110px_1.2fr_0.8fr_1fr_0.9fr_190px] gap-3 px-4 py-3 text-[11px] font-semibold uppercase tracking-wide text-gray-500 bg-gray-50 border-b border-gray-200">
              <div>{t('plan.job')}</div>
              <div className="text-center">{t('plan.kind')}</div>
              <div className="text-center">{t('plan.nextRun')}</div>
              <div className="text-center">{t('plan.repeat')}</div>
              <div className="text-center">{t('plan.agent')}</div>
              <div className="text-center">{t('plan.status')}</div>
              <div className="text-right">{t('plan.actions')}</div>
            </div>
            <div className="divide-y divide-gray-100">
              {visibleJobs.map(job => {
                const pending = ['scheduled', 'paused'].includes(job.status);
                const lastTask = (job.created_task_ids || [])[job.created_task_ids?.length - 1];
                return (
                  <div
                    key={job.id}
                    ref={job.id === linkedJobId ? linkedRowRef : null}
                    className={'grid grid-cols-1 md:grid-cols-[minmax(0,2fr)_110px_1.2fr_0.8fr_1fr_0.9fr_190px] gap-3 px-4 py-3 items-center'
                      + (job.id === linkedJobId ? ' bg-indigo-50/70 ring-1 ring-inset ring-indigo-200' : '')}
                  >
                    <div className="min-w-0">
                      <div className="font-medium text-gray-900 truncate">{job.title}</div>
                      {job.message && <div className="text-xs text-gray-500 mt-0.5 truncate">{job.message}</div>}
                      <div className="text-[11px] text-gray-400 mt-0.5 flex items-center gap-2">
                        <span>{t('plan.by', { user: job.created_by })}</span>
                        {job.last_error && <span className="text-red-500 truncate" title={job.last_error}>{t('common.error')}: {job.last_error}</span>}
                        {lastTask && (
                          <Link to={`/tasks/${lastTask}`} className="text-indigo-500 hover:underline" onClick={e => e.stopPropagation()}>
                            {t('plan.createdTask')}
                          </Link>
                        )}
                      </div>
                    </div>
                    <div className="md:text-center"><KindBadge kind={job.kind} /></div>
                    <div className="md:text-center">
                      <div className="text-sm text-gray-700">{formatLocal(job.run_at)}</div>
                      {pending && <div className="text-[11px] text-gray-400">{relativeTime(job.run_at, t)}</div>}
                    </div>
                    <div className="md:text-center text-sm text-gray-600">
                      {job.recurrence !== 'none' ? (
                        <span className="inline-flex items-center gap-1"><Repeat className="w-3 h-3" />{job.recurrence}</span>
                      ) : <span className="text-gray-400">{t('plan.once')}</span>}
                    </div>
                    <div className="md:text-center text-sm text-gray-600 truncate">
                      {job.kind === 'agent_task'
                        ? (job.agent_id || <span className="text-gray-400 italic">{t('plan.orchestrator')}</span>)
                        : job.kind === 'flow'
                          ? (job.flow_id || '—')
                          : '—'}
                    </div>
                    <div className="md:text-center"><StatusBadge status={job.status} /></div>
                    <div className="flex md:justify-end items-center gap-1.5">
                      {acting[job.id] ? (
                        <Loader className="w-4 h-4 animate-spin text-gray-400" />
                      ) : pending ? (
                        <>
                          <button
                            title={t('plan.runNow')}
                            onClick={() => act(job.id, () => runPlanJobNow(job.id))}
                            className="p-1.5 text-gray-400 hover:text-green-600 hover:bg-green-50 rounded-md"
                          ><Zap className="w-4 h-4" /></button>
                          <button
                            title={t('plan.edit')}
                            onClick={() => setModalJob(job)}
                            className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-md"
                          ><Pencil className="w-4 h-4" /></button>
                          {job.status === 'scheduled' ? (
                            <button
                              title={t('plan.pause')}
                              onClick={() => act(job.id, () => pausePlanJob(job.id))}
                              className="p-1.5 text-gray-400 hover:text-amber-600 hover:bg-amber-50 rounded-md"
                            ><Pause className="w-4 h-4" /></button>
                          ) : (
                            <button
                              title={t('plan.resume')}
                              onClick={() => act(job.id, () => resumePlanJob(job.id))}
                              className="p-1.5 text-gray-400 hover:text-green-600 hover:bg-green-50 rounded-md"
                            ><Play className="w-4 h-4" /></button>
                          )}
                          <button
                            title={t('plan.cancel')}
                            onClick={() => act(job.id, () => cancelPlanJob(job.id))}
                            className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-md"
                          ><X className="w-4 h-4" /></button>
                        </>
                      ) : (
                        <button
                          title={t('plan.delete')}
                          onClick={() => {
                            if (window.confirm(`Delete job "${job.title}"?`)) act(job.id, () => deletePlanJob(job.id));
                          }}
                          className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-md"
                        ><Trash2 className="w-4 h-4" /></button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )
      ) : (
        notifications.length === 0 ? (
          <div className="bg-white rounded-xl border border-gray-200 text-center py-16">
            <Bell className="w-10 h-10 text-gray-300 mx-auto mb-3" />
            <p className="text-gray-500 text-sm">{t('plan.noNotificationsYet')}</p>
            <p className="text-gray-400 text-xs mt-1">{t('plan.scheduledRemindersAndFiredJobs')}</p>
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden divide-y divide-gray-100">
            {notifications.map(n => (
              <div key={n.id} className={`flex items-start gap-3 px-4 py-3 ${n.read ? '' : 'bg-indigo-50/40'}`}>
                <div className={`mt-0.5 p-1.5 rounded-full ${n.read ? 'bg-gray-100 text-gray-400' : 'bg-indigo-100 text-indigo-600'}`}>
                  <Bell className="w-4 h-4" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className={`text-sm truncate ${n.read ? 'text-gray-600' : 'font-semibold text-gray-900'}`}>{n.title}</div>
                  {n.body && <div className="text-xs text-gray-500 mt-0.5">{n.body}</div>}
                  <div className="text-[11px] text-gray-400 mt-0.5 flex items-center gap-2">
                    <span>{formatLocal(n.created_at)}</span>
                    {n.source?.task_id && (
                      <Link to={`/tasks/${n.source.task_id}`} className="text-indigo-500 hover:underline">{t('plan.viewTask')}</Link>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <button
                    title={n.read ? t('plan.markUnread') : t('plan.markRead')}
                    onClick={() => act(n.id, () => markNotificationRead(n.id, !n.read))}
                    className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 rounded-md"
                  >
                    {n.read ? <Bell className="w-4 h-4" /> : <MailOpen className="w-4 h-4" />}
                  </button>
                  <button
                    title={t('plan.delete')}
                    onClick={() => act(n.id, () => deleteNotification(n.id))}
                    className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-md"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )
      )}

      {modalJob !== undefined && (
        <JobModal
          job={modalJob}
          agents={agents}
          flows={flows}
          workspace={workspaceFilter}
          telegram={telegram}
          onClose={() => setModalJob(undefined)}
          onSaved={() => { setModalJob(undefined); fetchData(); }}
        />
      )}
    </PageContainer>
  );
}
