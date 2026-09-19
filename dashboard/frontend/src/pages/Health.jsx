import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity, AlertTriangle, CheckCircle, Database, HardDrive, KeyRound,
  Loader, RefreshCw, Server, Zap,
} from 'lucide-react';
import {
  getHealth, getServiceChat, clearServiceChat, stopServiceChat, serviceChatUrl,
} from '../api';
import EntityChat from '../components/EntityChat';
import { ChatColumn, ChatToggle, FILL_COLUMN, useChatColumn } from '../components/ChatColumn';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { usePageChat } from '../components/pageChat/pageChat';
import { useI18n } from '../i18n';

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

function Card({ icon: Icon, title, children, tone = 'default' }) {
  const ring = tone === 'bad' ? 'border-red-200' : 'border-gray-200';
  return (
    <div className={`bg-white rounded-xl border ${ring} p-4 shadow-sm`}>
      <h3 className="text-sm font-bold text-gray-800 flex items-center gap-2 mb-3">
        <Icon className="w-4 h-4 text-indigo-500" /> {title}
      </h3>
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

export default function Health() {
  const { t } = useI18n();
  const [health, setHealth] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const chat = useChatColumn(true);

  const load = useCallback(async () => {
    try {
      const { data } = await getHealth();
      setHealth(data);
      setError('');
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || t('health.unreachable'));
    } finally {
      setLoading(false);
    }
  }, [t]);

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
      {loading ? (
        <div className="p-6 text-sm text-gray-500 flex items-center gap-2">
          <Loader className="w-4 h-4 animate-spin" /> {t('health.loading')}
        </div>
      ) : (
        <div className={`grid grid-cols-1 gap-4 ${chat.open ? 'xl:grid-cols-2' : 'lg:grid-cols-2'}`}>
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
        </div>
      )}

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
