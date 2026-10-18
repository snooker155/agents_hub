import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Activity, AlertTriangle, CheckCircle, Database, HardDrive,
  KeyRound, Loader, RefreshCw, Server, Stethoscope, ChevronDown, ChevronRight,
} from 'lucide-react';
import { getHealth } from '../api';
import { getDoctor } from '../api/system';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
import { useToast, errorDetail } from '../components/toast';
import PageLoader from '../components/PageLoader';

/**
 * Health: the service looking at itself.
 *
 * The snapshot answers "is anything obviously wrong", at a glance, without
 * reading logs; the doctor's checks judge it and link the docs.
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

// The doctor answers in English (common/doctor.py), which `ah doctor` reads
// as is. Each summary also carries its message key and
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

export default function Health() {
  const { t } = useI18n();
  const [health, setHealth] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

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
        actions={
          <button
            onClick={load}
            className="inline-flex items-center px-3 py-2 text-xs font-semibold text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-3.5 h-3.5 mr-1" /> {t('health.refresh')}
          </button>
        }
      />

      {error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {error}
        </div>
      )}

      {/* Two independent stacks rather than one grid: grid rows stretch each
          card to its neighbour's height, and a card that grows (diagnostics
          loading its checks) would drag the cards beside it along. The long
          diagnostics list gets a column of its own; everything else stacks
          beside it, so neither column leaves a hole under a short card. */}
      <div className="grid grid-cols-1 gap-4 items-start lg:grid-cols-2">
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
    </PageContainer>
  );
}
