import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity, AlertTriangle, CheckCircle, Database, Download, Gauge, GitBranch, HardDrive,
  KeyRound, Loader, Pause, Play, RefreshCw, Server, Stethoscope, Trash2, ChevronDown, ChevronRight,
} from 'lucide-react';
import {
  getHealth, getServiceChat, clearServiceChat, stopServiceChat, serviceChatUrl,
} from '../api';
import {
  getDoctor, getSystem, syncSystem, setSystemSchedule, pruneSystemBranches, getSystemBranches,
} from '../api/system';
import { getSlo, getSupportBundle } from '../api/support';
import { saveBlobAs } from '../api/files';
import EntityChat from '../components/EntityChat';
import { ChatColumn, ChatToggle, FILL_COLUMN, useChatColumn } from '../components/ChatColumn';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { usePageChat } from '../components/pageChat/pageChat';
import { useI18n } from '../i18n';
import { useToast, errorDetail } from '../components/toast';
import PageLoader from '../components/PageLoader';

/**
 * Health — the service looking at itself.
 *
 * Two halves that answer different questions. The snapshot answers "is anything
 * obviously wrong", at a glance, without reading logs. The chat answers "why",
 * because the Service Agent beside it can follow a symptom down through nodes,
 * runs and logs, which no static panel can do.
 *
 * The snapshot deliberately shows `null` differently from `false`: a probe that
 * could not run is not a service that is down, and conflating the two sends
 * people hunting for a problem that does not exist.
 */

function bytes(n) {
  if (n === null || n === undefined) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = Number(n);
  let i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${v < 10 && i > 0 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

/** Three states, not two: on, off, and "could not tell". */
function ServiceDot({ state }) {
  const { t } = useI18n();
  if (state === true) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-green-700">
        <span className="w-2 h-2 rounded-full bg-green-500" /> {t('health.running')}
      </span>
    );
  }
  if (state === false) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-red-700">
        <span className="w-2 h-2 rounded-full bg-red-500" /> {t('health.stopped')}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-gray-500"
          title={t('health.unknownHint')}>
      <span className="w-2 h-2 rounded-full bg-gray-300" /> {t('health.unknown')}
    </span>
  );
}

// `badge` and `meta` sit right after the title, `actions` at the far end of
// the same line, so a card's state and its button read with its heading.
function Card({ icon: Icon, title, children, tone = 'default', badge, meta, actions }) {
  const ring = tone === 'bad' ? 'border-red-200' : 'border-gray-200';
  return (
    <div className={`bg-white rounded-xl border ${ring} p-4 shadow-sm`}>
      <div className="flex items-center gap-2 mb-3 min-h-[1.75rem]">
        <div className="flex flex-1 min-w-0 items-center gap-x-2 gap-y-1 flex-wrap">
          <h3 className="text-sm font-bold text-gray-800 flex items-center gap-2">
            <Icon className="w-4 h-4 text-indigo-500" /> {title}
          </h3>
          {badge}
          {meta && <span className="text-xs text-gray-400">{meta}</span>}
        </div>
        {actions && <div className="shrink-0">{actions}</div>}
      </div>
      {children}
    </div>
  );
}

function Row({ label, value, hint }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-sm">
      <span className="text-gray-500" title={hint}>{label}</span>
      <span className="font-semibold text-gray-900 tabular-nums">{value}</span>
    </div>
  );
}

// Four states, not the three of ServiceDot: a doctor check can also be
// skipped (the probe chose not to run, which is not the same as it failing).
const CHECK_TONES = {
  ok: { dot: 'bg-green-500', text: 'text-green-700' },
  warn: { dot: 'bg-amber-500', text: 'text-amber-700' },
  fail: { dot: 'bg-red-500', text: 'text-red-700' },
  skip: { dot: 'bg-gray-300', text: 'text-gray-500' },
};

function CheckDot({ status }) {
  const { t } = useI18n();
  const tone = CHECK_TONES[status] || CHECK_TONES.skip;
  const label = t(`health.diagnostics.status${status ? status[0].toUpperCase() + status.slice(1) : 'Skip'}`);
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-semibold ${tone.text}`}>
      <span className={`w-2 h-2 rounded-full ${tone.dot}`} /> {label}
    </span>
  );
}

// The same four tones as CheckDot, drawn as the section's overall badge
// (top-level `doctor.status` is only ever ok/warn/fail, never skip).
function DoctorBadge({ status }) {
  const { t } = useI18n();
  const box = {
    ok: 'bg-green-50 text-green-700 border-green-200',
    warn: 'bg-amber-50 text-amber-700 border-amber-200',
    fail: 'bg-red-50 text-red-700 border-red-200',
  }[status] || 'bg-gray-50 text-gray-500 border-gray-200';
  const label = t(`health.diagnostics.status${status ? status[0].toUpperCase() + status.slice(1) : 'Skip'}`);
  return (
    <span className={`inline-flex items-center gap-1.5 text-[11px] font-semibold px-2 py-0.5 rounded-full border ${box}`}>
      {label}
    </span>
  );
}

// The doctor answers in English (common/doctor.py), which `ah doctor` and the
// Service Agent read as is. Each summary also carries its message key and
// values, so the page says it in the reader's language; a key this build's
// locales do not have yet keeps the English sentence.
function doctorParams(params) {
  const out = {};
  for (const [name, value] of Object.entries(params || {})) {
    out[name] = Array.isArray(value) ? value.join(', ') : value;
  }
  return out;
}

function doctorMessage(t, msg) {
  if (!msg?.key) return null;
  const key = `health.doctor.summaries.${msg.key}`;
  const text = t(key, doctorParams(msg.params));
  return text === key ? null : text;
}

function checkSummary(t, check) {
  const i18n = check.summary_i18n;
  if (i18n?.parts?.length) {
    // Several findings in one sentence: "a; b." with the first letter raised,
    // the same shape the backend gives the English.
    const parts = i18n.parts.map((part) => doctorMessage(t, part));
    if (!parts.every(Boolean)) return check.summary;
    const text = parts.join('; ');
    return `${text[0].toUpperCase()}${text.slice(1)}.`;
  }
  return doctorMessage(t, i18n) || check.summary;
}

function detailValue(t, language, key, value) {
  if (value === null || value === undefined || (Array.isArray(value) && value.length === 0)) return '—';
  if (typeof value === 'boolean') return t(value ? 'health.doctor.yes' : 'health.doctor.no');
  if (key.endsWith('_bytes') && typeof value === 'number') return bytes(value);
  if (key.endsWith('_at') && typeof value === 'string') {
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) return date.toLocaleString(language);
  }
  if (Array.isArray(value) && value.every((v) => v === null || typeof v !== 'object')) {
    return value.join(', ');
  }
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}

// One self check, expandable to its detail map. `doc`/`anchor` point at the
// service-health doc's matching section rather than duplicating the
// explanation here, so the two never drift apart.
function CheckRow({ check }) {
  const { t, language } = useI18n();
  const [open, setOpen] = useState(false);
  const detailEntries = check.detail && typeof check.detail === 'object'
    ? Object.entries(check.detail) : [];
  const hasDetail = detailEntries.length > 0;
  const summary = checkSummary(t, check);

  return (
    <div className="border-b border-gray-100 last:border-0 py-2">
      <div className="flex items-start gap-2">
        <button
          type="button"
          onClick={() => hasDetail && setOpen((o) => !o)}
          disabled={!hasDetail}
          className="flex flex-1 min-w-0 items-start gap-2 text-left"
        >
          <span className="mt-0.5 shrink-0 text-gray-300">
            {hasDetail ? (open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />) : <span className="inline-block w-3.5" />}
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-semibold text-gray-800">
                {t(`health.doctor.titles.${check.id}`, { defaultValue: check.title })}
              </span>
              <CheckDot status={check.status} />
            </span>
            {summary && <span className="block text-xs text-gray-500 mt-0.5">{summary}</span>}
          </span>
        </button>
        {check.doc && (
          <Link
            to={`/docs/${check.doc}${check.anchor ? `#${check.anchor}` : ''}`}
            className="shrink-0 text-xs text-indigo-600 hover:text-indigo-800 mt-0.5"
          >
            {t('health.diagnostics.readMore')}
          </Link>
        )}
      </div>
      {open && hasDetail && (
        <div className="mt-2 ml-6 space-y-0.5">
          {detailEntries.map(([key, value]) => (
            <div key={key} className="flex items-baseline justify-between gap-3 text-xs">
              <span className="text-gray-400" title={key}>
                {t(`health.doctor.detail.${key}`, { defaultValue: key })}
              </span>
              <span className="text-gray-700 font-mono break-all text-right">
                {detailValue(t, language, key, value)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Self checks: loads on mount and again on Run, independent of the plain
// snapshot above it since the two answer different questions (see the module
// docblock).
function DiagnosticsSection() {
  const { t, language } = useI18n();
  const toast = useToast();
  const [doctor, setDoctor] = useState(null);
  const [loading, setLoading] = useState(true);
  // Running from the first render: the page opens with a run under way.
  const [running, setRunning] = useState(true);

  // A promise chain rather than an async body, so the effect below may call it
  // without a synchronous setState.
  const load = useCallback(() => getDoctor()
    .then(({ data }) => setDoctor(data))
    .catch((e) => toast.error(t('health.diagnostics.unreachable'), errorDetail(e)))
    .finally(() => {
      setLoading(false);
      setRunning(false);
    }), [t, toast]);

  useEffect(() => { load(); }, [load]);

  const run = () => { setRunning(true); load(); };

  const checks = doctor?.checks || [];

  return (
    <Card
      icon={Stethoscope}
      title={t('health.diagnostics.title')}
      badge={doctor && <DoctorBadge status={doctor.status} />}
      meta={doctor?.checked_at
        && t('health.diagnostics.checkedAt', { time: new Date(doctor.checked_at).toLocaleString(language) })}
      actions={(
        <button
          type="button"
          onClick={run}
          disabled={running}
          className="inline-flex items-center px-2 py-1 text-xs font-semibold text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
        >
          {running
            ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" />
            : <RefreshCw className="w-3.5 h-3.5 mr-1" />}
          {t('health.diagnostics.run')}
        </button>
      )}
    >
      {loading ? (
        <PageLoader size="sm" label={t('health.diagnostics.running')} />
      ) : checks.length === 0 ? (
        <p className="text-sm text-gray-400 italic">{t('health.diagnostics.noChecks')}</p>
      ) : (
        <div>{checks.map((c) => <CheckRow key={c.id} check={c} />)}</div>
      )}
    </Card>
  );
}

// The two SLO objectives (common/slo.py): run start time p95 and error rate,
// each ok/breach/no_data with the number behind it. Reads GET /api/support/slo
// on mount and on Refresh; the same numbers the hub's own alert rules
// (notify/rules.py's slo_start_latency / slo_error_rate) act on, so this card
// never disagrees with what fired.
const SLO_TONES = {
  ok: { dot: 'bg-green-500', text: 'text-green-700' },
  breach: { dot: 'bg-red-500', text: 'text-red-700' },
  no_data: { dot: 'bg-gray-300', text: 'text-gray-500' },
};

function SloStatusDot({ status }) {
  const { t } = useI18n();
  const tone = SLO_TONES[status] || SLO_TONES.no_data;
  const key = status === 'breach' ? 'statusBreach' : status === 'ok' ? 'statusOk' : 'statusNoData';
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-semibold ${tone.text}`}>
      <span className={`w-2 h-2 rounded-full ${tone.dot}`} /> {t(`health.slo.${key}`)}
    </span>
  );
}

function SloObjectiveRow({ label, objective, format }) {
  const { t } = useI18n();
  if (!objective) return null;
  return (
    <div className="py-1.5 border-b border-gray-100 last:border-0">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm text-gray-700">{label}</span>
        <SloStatusDot status={objective.status} />
      </div>
      <div className="text-xs text-gray-400 mt-0.5">
        {format(objective)}
        {objective.sample != null && ` · ${t('health.slo.sample', { count: objective.sample })}`}
      </div>
    </div>
  );
}

function SloCard() {
  const { t } = useI18n();
  const toast = useToast();
  const [slo, setSlo] = useState(null);
  const [loading, setLoading] = useState(true);

  // A promise chain rather than an async body, so the effect below may call it
  // without a synchronous setState.
  const load = useCallback(() => getSlo()
    .then(({ data }) => setSlo(data))
    .catch((e) => toast.error(t('health.slo.unreachable'), errorDetail(e)))
    .finally(() => setLoading(false)), [t, toast]);

  useEffect(() => { load(); }, [load]);

  const refresh = () => { setLoading(true); load(); };

  const objectives = slo?.objectives || {};

  return (
    <Card
      icon={Gauge}
      title={t('health.slo.title')}
      badge={slo && <SloStatusDot status={slo.status} />}
      actions={(
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          className="inline-flex items-center px-2 py-1 text-xs font-semibold text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
        >
          {loading ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5 mr-1" />}
          {t('health.refresh')}
        </button>
      )}
    >
      <SloObjectiveRow
        label={t('health.slo.startP95')}
        objective={objectives.start_p95}
        format={(o) => (o.value_seconds == null
          ? t('health.slo.noValue')
          : `${o.value_seconds.toFixed(1)}s / ${o.threshold_seconds}s`)}
      />
      <SloObjectiveRow
        label={t('health.slo.errorRate')}
        objective={objectives.error_rate}
        format={(o) => (o.value == null
          ? t('health.slo.noValue')
          : `${(o.value * 100).toFixed(1)}% / ${(o.threshold * 100).toFixed(1)}%`)}
      />
    </Card>
  );
}

// The system workspace: its clone, its scheduled loop and its recent
// branches. Reads GET /api/system once on mount; a 404 means the feature is
// off entirely, which is shown as one line rather than an error.
function SystemWorkspaceCard() {
  const { t } = useI18n();
  const toast = useToast();
  const [system, setSystem] = useState(null);
  const [disabled, setDisabled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [scheduleSaving, setScheduleSaving] = useState(false);
  const [pruning, setPruning] = useState(false);
  const [everyHours, setEveryHours] = useState(24);

  // A promise chain rather than an async body, so the effect below may call it
  // without a synchronous setState.
  const load = useCallback(() => getSystem()
    .then(({ data }) => {
      setSystem(data);
      setDisabled(false);
      if (data?.loop?.every_hours) setEveryHours(data.loop.every_hours);
    })
    .catch((e) => {
      if (e?.response?.status === 404) {
        setDisabled(true);
        setSystem(null);
      } else {
        toast.error(t('health.system.unreachable'), errorDetail(e));
      }
    })
    .finally(() => setLoading(false)), [t, toast]);

  useEffect(() => { load(); }, [load]);

  const handleSync = async () => {
    setSyncing(true);
    try {
      const { data } = await syncSystem();
      setSystem((prev) => (prev ? { ...prev, clone: data } : prev));
      toast.success(t('health.system.syncDone'));
    } catch (e) {
      toast.error(t('health.system.syncFailed'), errorDetail(e));
    } finally {
      setSyncing(false);
    }
  };

  const pushSchedule = async (enabled, hours) => {
    setScheduleSaving(true);
    try {
      const { data } = await setSystemSchedule(enabled, hours);
      setSystem((prev) => (prev ? { ...prev, loop: data } : prev));
    } catch (e) {
      toast.error(t('health.system.scheduleFailed'), errorDetail(e));
    } finally {
      setScheduleSaving(false);
    }
  };

  const handleToggleSchedule = () => pushSchedule(!system?.loop?.scheduled, everyHours);

  // The interval only reaches the backend once a schedule exists to apply it
  // to — typing a number while paused just holds it for the next Enable.
  const handleEveryHoursBlur = (raw) => {
    const n = Number(raw);
    if (!Number.isFinite(n) || n <= 0) return;
    setEveryHours(n);
    if (system?.loop?.scheduled) pushSchedule(true, n);
  };

  const handlePrune = async () => {
    const raw = window.prompt(t('health.system.prunePrompt'), '14');
    if (raw === null) return;
    const days = Number(raw);
    if (!Number.isFinite(days) || days < 0) return;
    if (!window.confirm(t('health.system.pruneConfirm', { days }))) return;
    setPruning(true);
    try {
      const { data } = await pruneSystemBranches(days);
      const deleted = data?.deleted || [];
      toast.success(t('health.system.pruneDone', { count: deleted.length }));
      const { data: branches } = await getSystemBranches();
      setSystem((prev) => (prev ? { ...prev, branches } : prev));
    } catch (e) {
      toast.error(t('health.system.pruneFailed'), errorDetail(e));
    } finally {
      setPruning(false);
    }
  };

  if (loading) {
    return (
      <Card icon={GitBranch} title={t('health.system.title')}>
        <PageLoader size="sm" label={t('health.loading')} />
      </Card>
    );
  }

  if (disabled) {
    return (
      <Card icon={GitBranch} title={t('health.system.title')}>
        <p className="text-sm text-gray-500">{t('health.system.disabled')}</p>
      </Card>
    );
  }

  const clone = system?.clone || {};
  const loop = system?.loop || {};
  const branches = system?.branches || [];

  return (
    <Card icon={GitBranch} title={t('health.system.title')}>
      <div className="mb-3 pb-3 border-b border-gray-100">
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{t('health.system.clone')}</span>
          <button
            type="button"
            onClick={handleSync}
            disabled={syncing}
            className="inline-flex items-center px-2 py-1 text-xs font-semibold text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
          >
            {syncing
              ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" />
              : <RefreshCw className="w-3.5 h-3.5 mr-1" />}
            {t('health.system.sync')}
          </button>
        </div>
        <Row label={t('health.system.status')}
             value={clone.exists ? t('health.system.cloned') : t('health.system.notCloned')} />
        {clone.branch && <Row label={t('health.system.branch')} value={clone.branch} />}
        {clone.head && <Row label={t('health.system.head')} value={String(clone.head).slice(0, 7)} />}
        {clone.synced_at && <Row label={t('health.system.syncedAt')} value={new Date(clone.synced_at).toLocaleString()} />}
        {clone.error && <p className="text-xs text-red-600 mt-1">{clone.error}</p>}
      </div>

      <div className="mb-3 pb-3 border-b border-gray-100">
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{t('health.system.loop')}</span>
          <button
            type="button"
            onClick={handleToggleSchedule}
            disabled={scheduleSaving}
            className={`inline-flex items-center px-2 py-1 text-xs font-semibold rounded-lg border disabled:opacity-50 ${
              loop.scheduled
                ? 'text-amber-700 bg-amber-50 border-amber-200 hover:bg-amber-100'
                : 'text-green-700 bg-green-50 border-green-200 hover:bg-green-100'}`}
          >
            {loop.scheduled ? <Pause className="w-3.5 h-3.5 mr-1" /> : <Play className="w-3.5 h-3.5 mr-1" />}
            {loop.scheduled ? t('health.system.pause') : t('health.system.enable')}
          </button>
        </div>
        <Row label={t('health.system.state')}
             value={loop.scheduled ? t('health.system.scheduled') : t('health.system.paused')} />
        <div className="flex items-baseline justify-between gap-3 py-1 text-sm">
          <span className="text-gray-500">{t('health.system.everyHours')}</span>
          <span className="flex items-center gap-1.5">
            <input
              type="number"
              min={1}
              value={everyHours}
              onChange={(e) => setEveryHours(e.target.value)}
              onBlur={(e) => handleEveryHoursBlur(e.target.value)}
              className="w-16 text-right font-semibold text-gray-900 border border-gray-200 rounded px-1.5 py-0.5 text-sm tabular-nums"
            />
            <span className="text-xs text-gray-400">{t('health.system.hours')}</span>
          </span>
        </div>
        {loop.next_run_at && <Row label={t('health.system.nextRun')} value={new Date(loop.next_run_at).toLocaleString()} />}
        {loop.last_run && (
          <div className="flex items-baseline justify-between gap-3 py-1 text-sm">
            <span className="text-gray-500">{t('health.system.lastRun')}</span>
            <Link to={`/loops?run=${loop.last_run.loop_run_id}`} className="font-semibold text-indigo-600 hover:text-indigo-800">
              {loop.last_run.status}
              {loop.last_run.finished_at ? ` · ${new Date(loop.last_run.finished_at).toLocaleString()}` : ''}
            </Link>
          </div>
        )}
      </div>

      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">{t('health.system.branches')}</span>
          <button
            type="button"
            onClick={handlePrune}
            disabled={pruning}
            className="inline-flex items-center px-2 py-1 text-xs font-semibold text-red-700 bg-white border border-red-200 rounded-lg hover:bg-red-50 disabled:opacity-50"
          >
            <Trash2 className="w-3.5 h-3.5 mr-1" /> {t('health.system.prune')}
          </button>
        </div>
        {branches.length === 0 ? (
          <p className="text-sm text-gray-400 italic">{t('health.system.noBranches')}</p>
        ) : (
          <div className="space-y-1">
            {branches.map((b) => (
              <div key={b.name} className="flex items-center gap-2 text-sm py-1">
                <span className="font-mono text-xs text-gray-700 truncate flex-1 min-w-0">{b.name}</span>
                <span className="text-xs text-gray-400 shrink-0">{t('health.system.ageDays', { count: b.age_days ?? 0 })}</span>
                {b.task_id && (
                  <Link to={`/tasks/${b.task_id}`} className="text-xs text-indigo-600 hover:text-indigo-800 shrink-0">
                    {t('health.system.taskLink')}
                  </Link>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
}

export default function Health() {
  const { t } = useI18n();
  const toast = useToast();
  const [health, setHealth] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [bundling, setBundling] = useState(false);
  const chat = useChatColumn(true);

  // A promise chain rather than an async body, so the effect below may call it
  // without a synchronous setState.
  const load = useCallback(() => getHealth()
    .then(({ data }) => {
      setHealth(data);
      setError('');
    })
    .catch((e) => {
      setError(e?.response?.data?.detail || e.message || t('health.unreachable'));
    })
    .finally(() => setLoading(false)), [t]);

  useEffect(() => { load(); }, [load]);

  // The chat's own turn refreshes the snapshot: an action tool may have stopped
  // something, and a stale panel beside a fresh answer reads as a bug.
  const onEvent = useCallback((ev) => {
    if (ev.type === 'health' && ev.health) setHealth(ev.health);
  }, []);

  const loadChat = useCallback(() => getServiceChat(), []);
  const clearChat = useCallback(() => clearServiceChat(), []);
  const stopChat = useCallback(() => stopServiceChat(), []);

  // One descriptor, two places it can be drawn: the column beside the page, and
  // the floating panel. Built here rather than at each surface so the two can
  // never drift into being two different conversations.
  const chatDescriptor = useMemo(() => ({
    scope: 'service',
    path: serviceChatUrl(),
    loadChat, clearChat, stopChat, onEvent,
    title: t('health.agentChat'),
    emptyHint: t('health.agentChatHint'),
    suggestions: [
      t('health.suggestAnythingBroken'),
      t('health.suggestWhyFailed'),
      t('health.suggestCost'),
      t('health.suggestDisk'),
    ],
  }), [loadChat, clearChat, stopChat, onEvent, t]);
  usePageChat(chatDescriptor);

  // Content-Disposition carries the bundle's own timestamped name
  // (common.support_bundle.default_filename); a fallback covers a proxy that
  // strips the header.
  const handleDownloadBundle = async () => {
    setBundling(true);
    try {
      const res = await getSupportBundle();
      const disposition = res.headers?.['content-disposition'] || '';
      const match = /filename="?([^";]+)"?/i.exec(disposition);
      saveBlobAs(res.data, match ? match[1] : 'agents-hub-support-bundle.zip');
    } catch (e) {
      toast.error(t('health.supportBundle.failed'), errorDetail(e));
    } finally {
      setBundling(false);
    }
  };

  const db = health?.database || {};
  const counts = db.counts || {};
  const services = health?.services || {};
  const storage = health?.storage || {};
  const providers = health?.providers || {};
  const degraded = health && health.status !== 'ok';

  return (
    <PageContainer>
      <PageHeader
        icon={Activity}
        title={t('health.title')}
        description={t('health.subtitle')}
        badges={health && (
          <span className={`inline-flex items-center gap-1.5 text-[11px] font-semibold px-2 py-0.5 rounded-full border ${
            degraded ? 'bg-red-50 text-red-700 border-red-200'
                     : 'bg-green-50 text-green-700 border-green-200'}`}>
            {degraded ? <AlertTriangle className="w-3 h-3" /> : <CheckCircle className="w-3 h-3" />}
            {degraded ? t('health.degraded') : t('health.ok')}
          </span>
        )}
        actions={<>
          <ChatToggle open={chat.open} onToggle={chat.toggle} label={t('health.agentChat')} />
          <button
            onClick={handleDownloadBundle}
            disabled={bundling}
            className="inline-flex items-center px-3 py-2 text-xs font-semibold text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
          >
            {bundling ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Download className="w-3.5 h-3.5 mr-1" />}
            {t('health.supportBundle.download')}
          </button>
          <button
            onClick={load}
            className="inline-flex items-center px-3 py-2 text-xs font-semibold text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-3.5 h-3.5 mr-1" /> {t('health.refresh')}
          </button>
        </>}
      />

      {error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {error}
        </div>
      )}

      <div className={chat.gridClass}>
        <div className={chat.mainClass}>
      {/* Two independent stacks rather than one grid: grid rows stretch each
          card to its neighbour's height, and a card that grows (diagnostics
          loading its checks) would drag the cards beside it along. The long
          diagnostics list gets a column of its own; everything else stacks
          beside it, so neither column leaves a hole under a short card. */}
      <div className={`grid grid-cols-1 gap-4 items-start ${chat.open ? '2xl:grid-cols-2' : 'lg:grid-cols-2'}`}>
        <div className="space-y-4 min-w-0">
          <DiagnosticsSection />
          {!loading && (
            <Card icon={Database} title={t('health.database')} tone={db.reachable ? 'default' : 'bad'}>
              {db.reachable ? (
                <>
                  <Row label={t('health.runningRuns')} value={db.running_runs ?? '—'}
                       hint={t('health.runningRunsHint')} />
                  {Object.entries(counts).map(([table, n]) => (
                    <Row key={table} label={table} value={n ?? '—'} />
                  ))}
                </>
              ) : (
                <p className="text-sm text-red-700">{db.error || t('health.dbUnreachable')}</p>
              )}
            </Card>
          )}
        </div>
        <div className="space-y-4 min-w-0">
          <SloCard />
          <SystemWorkspaceCard />
          {loading ? (
            <PageLoader label={t('health.loading')} />
          ) : (
            <>
              <Card icon={Server} title={t('health.backgroundServices')}>
                {Object.entries(services).map(([name, state]) => (
                  <div key={name} className="flex items-center justify-between py-1">
                    <span className="text-sm text-gray-500">{name}</span>
                    <ServiceDot state={state} />
                  </div>
                ))}
                <p className="text-xs text-gray-400 mt-2">{t('health.unknownHint')}</p>
              </Card>

              <Card icon={HardDrive} title={t('health.storage')}>
                <Row label={t('health.dbSize')} value={bytes(storage.db_bytes)} />
                <Row label={t('health.walSize')} value={bytes(storage.db_wal_bytes)} />
                <Row label={t('health.runLogs')}
                     value={`${bytes(storage.run_logs_bytes)} · ${storage.run_logs_files ?? 0}`} />
                <Row label={t('health.totalState')} value={bytes(storage.agents_hub_bytes)} />
              </Card>

              <Card icon={KeyRound} title={t('health.providers')}>
                <Row label={t('health.defaultProvider')} value={providers.default_provider || '—'} />
                {['openai_key_set', 'anthropic_key_set', 'google_key_set'].map((k) => (
                  <Row key={k} label={k.replace('_key_set', '')}
                       value={providers[k] ? t('health.keySet') : t('health.keyMissing')} />
                ))}
                <Row label={t('health.apiAuth')}
                     value={providers.api_auth_enabled ? t('health.on') : t('health.off')} />
                <Row label={t('health.agentCache')}
                     value={health?.agent_cache?.enabled === null
                       ? '—'
                       : (health?.agent_cache?.enabled ? t('health.on') : t('health.off'))} />
              </Card>
            </>
          )}
        </div>
      </div>
        </div>

        {chat.open && (
          <ChatColumn>
            <EntityChat {...chatDescriptor} {...FILL_COLUMN} />
          </ChatColumn>
        )}
      </div>
    </PageContainer>
  );
}
