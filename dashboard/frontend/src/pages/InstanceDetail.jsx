import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  Activity, Box, Brain, Check, ChevronDown, Cpu, FileText, History, MessageSquare,
  Pencil, Plus, RefreshCw, RotateCw, Square, Trash2, Wrench, X,
} from 'lucide-react';

import {
  deleteInstance, getAgents, getInstance, getInstanceContext, getInstanceConversations,
  getInstanceLogs, getInstanceRuns, getInstanceTimeline, interruptInstance,
  messageInstance, renameInstance, restartInstance, stopInstance,
} from '../api';
import { PageContainer, PageHeader } from '../components/PageLayout';
import ComposerDock from '../components/ComposerDock';
import InstanceComposer from '../components/instances/InstanceComposer';
import { useStream, useChannel } from '../components/stream';
import { StateBadge } from '../components/InstanceList';
import { formatDuration, relativeTime } from '../components/instanceUtils';
import MarkdownRenderer from '../components/MarkdownRenderer';
import ProcessTab from '../components/instances/ProcessTab';
import AccessTab from '../components/instances/AccessTab';
import { useI18n } from '../i18n';
import PageLoader from '../components/PageLoader';

/*
 * One live agent copy.
 *
 * The page is a conversation, not a log dump: what this copy was asked, what it
 * answered, what it used to get there, and a box to write to it. The box works
 * whatever state the copy is in. A resident instance is a process or container
 * of its own (instances/carrier.py): it holds several conversations at once,
 * the Process tab is its carrier, and the Access tab is its public address.
 */

const CORE_TABS = [
  { id: 'timeline', icon: MessageSquare, key: 'timeline' },
  { id: 'runs', icon: History, key: 'runs' },
  { id: 'context', icon: Brain, key: 'context' },
  { id: 'logs', icon: FileText, key: 'logs' },
];
const RESIDENT_TABS = [
  { id: 'process', icon: Activity, key: 'process' },
  { id: 'access', icon: Box, key: 'access' },
];

function Stat({ label, value }) {
  return (
    <div className="px-3 py-2 rounded-lg bg-gray-50 border border-gray-100">
      <div className="text-[11px] uppercase tracking-wide text-gray-400">{label}</div>
      <div className="text-sm font-medium text-gray-800 mt-0.5">{value}</div>
    </div>
  );
}

function Turn({ turn, t }) {
  return (
    <div className="border-b border-gray-100 last:border-0 py-3 space-y-2">
      {turn.user_message && (
        <div className="flex gap-2">
          <div className="w-16 shrink-0 text-[11px] uppercase tracking-wide text-gray-400 pt-0.5">
            {t('instanceDetail.turn.asked')}
          </div>
          <div className="text-sm text-gray-700 whitespace-pre-wrap min-w-0">{turn.user_message}</div>
        </div>
      )}
      {turn.tools && (
        <div className="flex gap-2">
          <div className="w-16 shrink-0 text-[11px] uppercase tracking-wide text-gray-400 pt-0.5">
            {t('instanceDetail.turn.used')}
          </div>
          <div className="text-xs text-gray-500 inline-flex items-center gap-1.5 min-w-0">
            <Wrench className="w-3 h-3 shrink-0" /><span className="truncate">{turn.tools}</span>
          </div>
        </div>
      )}
      {turn.response && (
        <div className="flex gap-2">
          <div className="w-16 shrink-0 text-[11px] uppercase tracking-wide text-gray-400 pt-0.5">
            {t('instanceDetail.turn.answered')}
          </div>
          <div className="min-w-0 text-sm text-gray-800">
            <MarkdownRenderer content={turn.response} />
          </div>
        </div>
      )}
      <div className="pl-[4.5rem] text-[11px] text-gray-300">
        <Link to={`/messages/${turn.run_id}`} className="hover:text-indigo-500">
          {t('instanceDetail.turn.openRun')}
        </Link>
      </div>
    </div>
  );
}

/**
 * "Main" first, then every other conversation the instance is holding: a tile
 * of the stats row that opens the list, so which thread the page shows reads
 * next to what the copy is and has done.
 */
function ConversationStat({ conversations, selected, onSelect, onNew, t }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const current = conversations.find((c) => c.conversation_id === selected);
  const name = (c) => (c?.conversation_id === 'main' ? t('instanceDetail.conversations.main') : (c?.conversation_id || selected));

  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={`w-full text-left px-3 py-2 rounded-lg border transition-colors ${
          open ? 'bg-indigo-50 border-indigo-200' : 'bg-gray-50 border-gray-100 hover:border-indigo-200 hover:bg-indigo-50/40'
        }`}
      >
        <div className="text-[11px] uppercase tracking-wide text-gray-400">{t('instanceDetail.stats.conversation')}</div>
        <div className="text-sm font-medium text-gray-800 mt-0.5 flex items-center justify-between gap-1 min-w-0">
          <span className="truncate">{name(current)}</span>
          <ChevronDown className={`w-3.5 h-3.5 text-gray-400 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
        </div>
      </button>
      {open && (
        <div className="absolute left-0 z-30 mt-1 w-64 bg-white border border-gray-200 rounded-lg shadow-lg py-1">
          {conversations.map((c) => (
            <button
              key={c.conversation_id}
              type="button"
              onClick={() => { onSelect(c.conversation_id); setOpen(false); }}
              className={`w-full text-left px-3 py-1.5 text-sm hover:bg-gray-50 flex items-center justify-between ${
                c.conversation_id === selected ? 'text-indigo-600 font-medium' : 'text-gray-700'
              }`}
            >
              <span className="truncate">{name(c)}</span>
              <span className="text-[11px] text-gray-400 ml-2">{c.messages ?? 0}</span>
            </button>
          ))}
          <div className="border-t border-gray-100 mt-1 pt-1">
            <button
              type="button"
              onClick={() => { onNew(); setOpen(false); }}
              className="w-full text-left px-3 py-1.5 text-sm text-indigo-600 hover:bg-indigo-50 flex items-center gap-1.5"
            >
              <Plus className="w-3.5 h-3.5" />
              {t('instanceDetail.conversations.new')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// The agent a runner answers as until the person picks another
// (workspace.storage.DEFAULT_CHAT_AGENT_ID on the backend).
const DEFAULT_RUNNER_AGENT = 'main-agent';

export default function InstanceDetail() {
  const { instanceId } = useParams();
  // `?agent=`: whom a runner replica opened from an agent's Run answers for.
  const [searchParams] = useSearchParams();
  const { t } = useI18n();
  const { clientId } = useStream();

  const [instance, setInstance] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [selectedConversation, setSelectedConversation] = useState('main');
  const [timeline, setTimeline] = useState(null);
  const [runs, setRuns] = useState({ items: [], total: 0 });
  const [context, setContext] = useState(null);
  const [logs, setLogs] = useState('');
  const [activeTab, setActiveTab] = useState('timeline');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [pageError, setPageError] = useState('');

  const [queuedNote, setQueuedNote] = useState('');
  const [liveText, setLiveText] = useState('');
  const [liveActivity, setLiveActivity] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [liveTaskId, setLiveTaskId] = useState(null);
  // The run the copy is answering with, from the stream's own `meta`: what a
  // message typed meanwhile is steered into (see InstanceComposer).
  const [liveRunId, setLiveRunId] = useState(null);

  const [renaming, setRenaming] = useState(false);
  const [labelDraft, setLabelDraft] = useState('');
  // The workspace's agents: a runner (a replica bound to no agent) answers
  // for the one picked here, and the composer reads any agent's commands.
  const [agentChoices, setAgentChoices] = useState([]);
  const [runnerAgent, setRunnerAgent] = useState(() => searchParams.get('agent') || '');

  const resident = !!instance?.resident;
  const isRunner = !!instance?.runner;

  useEffect(() => {
    if (!instance) return;
    getAgents(instance.workspace || undefined)
      .then((r) => {
        const list = r.data?.agents || r.data || [];
        setAgentChoices(list);
        // A runner answers as the main agent until another is picked (a
        // `?agent=` in the link wins); the first agent when there is no main.
        if (instance.runner && list.length) {
          setRunnerAgent((prev) => prev || (list.some((a) => a.id === DEFAULT_RUNNER_AGENT)
            ? DEFAULT_RUNNER_AGENT : list[0].id));
        }
      })
      .catch(() => setAgentChoices([]));
  }, [instance]);

  // Promise chain rather than try/await: the lint rule cannot tell that no
  // state is set before the first await of an async function with a catch.
  const load = useCallback(() => (
    getInstance(instanceId)
      .then((inst) => {
        setInstance(inst.data);
        let conversationsDone = Promise.resolve();
        if (inst.data.resident) {
          conversationsDone = getInstanceConversations(instanceId)
            .catch(() => ({ data: { items: [{ conversation_id: 'main' }] } }))
            .then((convResp) => {
              const items = convResp.data?.items?.length ? convResp.data.items : [{ conversation_id: 'main' }];
              setConversations((prev) => {
                // Keep a locally created, not-yet-used conversation in the list
                // (the server does not know about it until the first message).
                const extra = prev.filter((p) => !items.some((i) => i.conversation_id === p.conversation_id) && p.local);
                return [...items, ...extra];
              });
            });
        } else {
          setConversations([{ conversation_id: 'main' }]);
        }
        return conversationsDone
          .then(() => getInstanceTimeline(instanceId, inst.data.resident ? { conversation_id: selectedConversation } : undefined))
          .then((tl) => setTimeline(tl.data));
      })
      .catch((e) => {
        console.error('Failed to load instance', e);
        setInstance(null);
      })
      .finally(() => setLoading(false))
  ), [instanceId, selectedConversation]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (activeTab === 'runs') getInstanceRuns(instanceId, { limit: 100 })
      .then((r) => setRuns(r.data)).catch(() => setRuns({ items: [], total: 0 }));
    if (activeTab === 'context') getInstanceContext(instanceId, resident ? { conversation_id: selectedConversation } : undefined)
      .then((r) => setContext(r.data)).catch(() => setContext(null));
  }, [activeTab, instanceId, resident, selectedConversation]);

  // The first fetch on opening the tab shows as `logsLoadedFor` lagging; the
  // Refresh button raises `logsRefreshing` itself.
  const [logsRefreshing, setLogsRefreshing] = useState(false);
  const [logsLoadedFor, setLogsLoadedFor] = useState(null);
  const logsLoading = logsRefreshing || (activeTab === 'logs' && logsLoadedFor !== instanceId);
  const fetchLogs = useCallback(() => (
    getInstanceLogs(instanceId)
      .then((r) => setLogs(r.data?.logs || ''))
      .catch(() => setLogs(''))
      .finally(() => { setLogsRefreshing(false); setLogsLoadedFor(instanceId); })
  ), [instanceId]);
  const refreshLogs = () => { setLogsRefreshing(true); fetchLogs(); };

  useEffect(() => {
    if (activeTab === 'logs') fetchLogs();
  }, [activeTab, fetchLogs]);

  // Carrier log tail while the tab is open and the instance is live.
  const isLive = resident && ['starting', 'active', 'standby'].includes(instance?.state);
  useChannel(activeTab === 'logs' && isLive ? `logs:instance:${instanceId}` : null, (ev) => {
    if (ev.type === 'logs') setLogs(ev.content || '');
  });

  // The instance's own channel: a revived turn, a resident's queued reply, or
  // a task run it picked up while taking tasks.
  useChannel(instanceId ? `instance:${instanceId}` : null, useCallback((ev) => {
    if (!ev || !ev.type) return;
    if (ev.type === 'meta') {
      const matchesConversation = !resident || (ev.conversation_id || 'main') === selectedConversation;
      if (ev.task_id) {
        setLiveTaskId(ev.task_id);
        setLiveRunId(ev.run_id || null);
        setStreaming(true);
        setLiveText('');
        setQueuedNote('');
      } else if (matchesConversation) {
        setLiveRunId(ev.run_id || null);
        setStreaming(true);
        setLiveText('');
        setQueuedNote('');
      }
      return;
    }
    if (ev.type === 'token') { setLiveText((prev) => prev + (ev.token || '')); return; }
    if (ev.type === 'tool_start') { setLiveActivity(`${ev.tool || ''}`); return; }
    if (ev.type === 'thinking') { setLiveActivity(ev.message || ''); return; }
    if (ev.type === 'done') {
      setLiveActivity('');
      if (!ev.ok && ev.error) setQueuedNote(String(ev.error));
      return;
    }
    if (ev.type === 'instance_stream_end') {
      setStreaming(false);
      setLiveText('');
      setLiveActivity('');
      setLiveTaskId(null);
      setLiveRunId(null);
      load();
    }
  }, [load, resident, selectedConversation]));

  // The composer's delivery: true when the copy took the message.
  const sendMessage = useCallback(async ({ message, attachments = [], references = [] }) => {
    if (isRunner && !runnerAgent) {
      setQueuedNote(t('instanceDetail.message.runnerNeedsAgent'));
      return false;
    }
    setQueuedNote('');
    try {
      const res = await messageInstance(instanceId, {
        message,
        attachments,
        references,
        client_id: clientId,
        ...(resident ? { conversation_id: selectedConversation } : {}),
        ...(isRunner ? { agent_id: runnerAgent } : {}),
      });
      if (res.data?.mode === 'running') {
        setStreaming(true);
        setLiveText('');
      } else if (res.data?.mode === 'steered') {
        // Busy with a task run: the agent reads it before its next step.
        setQueuedNote(t('instanceDetail.message.steered'));
      } else if (res.data?.mode === 'queued') {
        setQueuedNote(res.data.started
          ? t('instanceDetail.message.startingAgain')
          : t('instanceDetail.message.queued'));
      } else {
        setQueuedNote(t('instanceDetail.message.queued'));
      }
      load();
      return true;
    } catch (e) {
      const detail = e.response?.data?.detail;
      setQueuedNote((typeof detail === 'string' && detail) || t('instanceDetail.message.failed'));
      return false;
    }
  }, [clientId, instanceId, isRunner, load, resident, runnerAgent, selectedConversation, t]);

  const handleStop = async () => {
    setBusy('stopping');
    try { await stopInstance(instanceId); await load(); }
    catch (e) { setPageError(e.response?.data?.detail || ''); }
    finally { setBusy(''); }
  };

  const handleInterrupt = async () => {
    setBusy('interrupting');
    try { await interruptInstance(instanceId); await load(); }
    catch (e) { setPageError(e.response?.data?.detail || ''); }
    finally { setBusy(''); }
  };

  const handleRestart = async () => {
    setBusy('restarting');
    try { const { data } = await restartInstance(instanceId); setInstance(data); await load(); }
    catch (e) { setPageError(e.response?.data?.detail || ''); }
    finally { setBusy(''); }
  };

  const handleDelete = async () => {
    if (!window.confirm(t('instanceDetail.deleteConfirm'))) return;
    try {
      await deleteInstance(instanceId);
      window.location.href = '/instances';
    } catch (e) {
      alert(e.response?.data?.detail || t('instanceDetail.deleteFailed'));
    }
  };

  const saveLabel = async () => {
    try {
      const res = await renameInstance(instanceId, labelDraft.trim());
      setInstance(res.data);
    } catch (e) { console.error(e); }
    finally { setRenaming(false); }
  };

  const handleNewConversation = () => {
    const id = `c_${Date.now().toString(36)}`;
    setConversations((prev) => [...prev, { conversation_id: id, messages: 0, local: true }]);
    setSelectedConversation(id);
  };

  if (loading) {
    return (
      <PageContainer>
        <PageLoader size="lg" label={t('instanceDetail.loading')} />
      </PageContainer>
    );
  }

  if (!instance) {
    return (
      <PageContainer>
        <div className="p-8 text-center">
          <div className="text-sm text-gray-500">{t('instanceDetail.notFound')}</div>
          <Link to="/instances" className="text-sm text-indigo-600 hover:underline mt-2 inline-block">
            {t('instanceDetail.backToList')}
          </Link>
        </div>
      </PageContainer>
    );
  }

  const isBusy = ['active', 'starting'].includes(instance.state);
  const turns = timeline?.turns || [];
  const pending = timeline?.pending || [];
  const tabs = resident ? [...CORE_TABS, ...RESIDENT_TABS] : CORE_TABS;

  return (
    <PageContainer>
      <PageHeader
        icon={Activity}
        backTo="/instances"
        title={renaming ? (
          <span className="inline-flex items-center gap-2">
            <input
              autoFocus
              value={labelDraft}
              onChange={(e) => setLabelDraft(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') saveLabel(); if (e.key === 'Escape') setRenaming(false); }}
              className="px-2 py-1 text-base border border-gray-300 rounded-lg"
            />
            <button type="button" onClick={saveLabel} className="p-1 text-green-600"><Check className="w-4 h-4" /></button>
            <button type="button" onClick={() => setRenaming(false)} className="p-1 text-gray-400"><X className="w-4 h-4" /></button>
          </span>
        ) : (
          <span className="inline-flex items-center gap-2">
            {instance.label || instance.instance_id}
            <button type="button"
                    onClick={() => { setLabelDraft(instance.label || ''); setRenaming(true); }}
                    className="p-1 text-gray-300 hover:text-gray-600" title={t('instanceDetail.rename')}>
              <Pencil className="w-3.5 h-3.5" />
            </button>
          </span>
        )}
        description={t('instanceDetail.description', {
          agent: instance.agent_id || t('instances.kinds.runner'),
          kind: t(`instances.kinds.${instance.kind}`, { defaultValue: instance.kind }),
        })}
        badges={instance.service_id && (
          <Link to={`/services/${instance.service_id}`}
                className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full border bg-indigo-50 text-indigo-700 border-indigo-200 hover:bg-indigo-100">
            <Cpu className="w-3 h-3" />
            {t('instances.replicaOf', { service: instance.service_name || instance.service_id })}
          </Link>
        )}
        actions={(
          <div className="flex items-center gap-2">
            <StateBadge state={instance.state} t={t} />
            {resident ? (
              <>
                {isLive ? (
                  <>
                    {instance.state === 'active' && (
                      <button type="button" onClick={handleInterrupt} disabled={!!busy}
                              className="px-3 py-1.5 text-sm rounded-lg border border-amber-200 text-amber-700 hover:bg-amber-50 inline-flex items-center gap-1.5 disabled:opacity-50">
                        <Square className="w-3.5 h-3.5" />{t('instanceDetail.interrupt')}
                      </button>
                    )}
                    <button type="button" onClick={handleRestart} disabled={!!busy}
                            className="px-3 py-1.5 text-sm rounded-lg border border-indigo-200 text-indigo-600 hover:bg-indigo-50 inline-flex items-center gap-1.5 disabled:opacity-50">
                      <RotateCw className="w-3.5 h-3.5" />{t('instanceDetail.restart')}
                    </button>
                    <button type="button" onClick={handleStop} disabled={!!busy}
                            className="px-3 py-1.5 text-sm rounded-lg border border-red-200 text-red-600 hover:bg-red-50 inline-flex items-center gap-1.5 disabled:opacity-50">
                      <Square className="w-3.5 h-3.5" />{t('instanceDetail.stop')}
                    </button>
                  </>
                ) : (
                  <button type="button" onClick={handleRestart} disabled={!!busy}
                          className="px-3 py-1.5 text-sm rounded-lg border border-indigo-200 text-indigo-600 hover:bg-indigo-50 inline-flex items-center gap-1.5 disabled:opacity-50">
                    <RotateCw className="w-3.5 h-3.5" />{t('instanceDetail.startAgain')}
                  </button>
                )}
              </>
            ) : (
              isBusy && (
                <button type="button" onClick={handleStop}
                        className="px-3 py-1.5 text-sm rounded-lg border border-red-200 text-red-600 hover:bg-red-50 inline-flex items-center gap-1.5">
                  <Square className="w-3.5 h-3.5" />{t('instanceDetail.stop')}
                </button>
              )
            )}
            <button type="button" onClick={handleDelete}
                    disabled={resident && isLive}
                    title={resident && isLive ? t('instanceDetail.deleteWhileLive') : t('instanceDetail.delete')}
                    className="p-2 rounded-lg border border-gray-200 text-gray-400 hover:text-red-600 hover:bg-red-50 disabled:opacity-30 disabled:cursor-not-allowed">
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      />

      {pageError && (
        <div className="mb-4 px-3 py-2 rounded-lg bg-red-50 border border-red-100 text-xs text-red-700">{pageError}</div>
      )}

      <div className={`grid grid-cols-2 md:grid-cols-4 gap-2 mb-4 ${resident ? 'xl:grid-cols-7' : 'xl:grid-cols-6'}`}>
        {resident && (
          <ConversationStat
            conversations={conversations}
            selected={selectedConversation}
            onSelect={setSelectedConversation}
            onNew={handleNewConversation}
            t={t}
          />
        )}
        <Stat label={t('instanceDetail.stats.agent')} value={
          <Link to={`/agents/${instance.agent_id}`} className="hover:text-indigo-600">{instance.agent_id}</Link>
        } />
        <Stat label={t('instanceDetail.stats.workspace')} value={instance.workspace || 'default'} />
        <Stat label={t('instanceDetail.stats.runs')} value={instance.runs_count || 0} />
        <Stat label={t('instanceDetail.stats.tokens')} value={(instance.total_tokens || 0).toLocaleString()} />
        <Stat label={t('instanceDetail.stats.duration')} value={formatDuration(instance.total_duration_ms)} />
        <Stat label={t('instanceDetail.stats.lastActivity')} value={relativeTime(instance.last_activity_at, t)} />
      </div>

      <div className="flex gap-1 border-b border-gray-200">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => setActiveTab(tab.id)}
            className={`px-3 py-2 text-sm inline-flex items-center gap-1.5 border-b-2 -mb-px ${
              activeTab === tab.id
                ? 'border-indigo-600 text-indigo-600'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <tab.icon className="w-3.5 h-3.5" />{t(`instanceDetail.tabs.${tab.key}`)}
          </button>
        ))}
      </div>

      {activeTab === 'timeline' && (
        <div className="bg-white border border-gray-200 rounded-xl px-4">
          {!turns.length ? (
            <div className="py-8 text-center text-sm text-gray-400">{t('instanceDetail.emptyTimeline')}</div>
          ) : turns.map((turn) => <Turn key={turn.run_id} turn={turn} t={t} />)}
        </div>
      )}

      {activeTab === 'runs' && (
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="text-[11px] uppercase tracking-wide text-gray-400 border-b border-gray-200">
                <th className="px-3 py-2 text-left font-medium">{t('instanceDetail.runs.title')}</th>
                <th className="px-3 py-2 text-left font-medium">{t('instanceDetail.runs.status')}</th>
                <th className="px-3 py-2 text-right font-medium">{t('instanceDetail.runs.tokens')}</th>
                <th className="px-3 py-2 text-right font-medium">{t('instanceDetail.runs.duration')}</th>
                <th className="px-3 py-2 text-left font-medium">{t('instanceDetail.runs.started')}</th>
              </tr>
            </thead>
            <tbody>
              {(runs.items || []).map((run) => (
                <tr key={run.run_id} className="border-b border-gray-100 last:border-0 hover:bg-gray-50">
                  <td className="px-3 py-2">
                    <Link to={`/messages/${run.run_id}`} className="text-sm text-gray-800 hover:text-indigo-600">
                      {run.title || run.run_id.slice(0, 8)}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-600">{run.status}</td>
                  <td className="px-3 py-2 text-xs text-gray-600 text-right">
                    {(run.total_tokens || 0).toLocaleString()}
                  </td>
                  <td className="px-3 py-2 text-xs text-gray-600 text-right">{formatDuration(run.duration_ms)}</td>
                  <td className="px-3 py-2 text-xs text-gray-400">{relativeTime(run.started_at, t)}</td>
                </tr>
              ))}
              {!(runs.items || []).length && (
                <tr><td colSpan={5} className="px-3 py-8 text-center text-sm text-gray-400">
                  {t('instanceDetail.runs.empty')}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {activeTab === 'context' && (
        <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-3">
          <div className="text-xs text-gray-500">
            {t('instanceDetail.context.hint', {
              turns: context?.turn_count ?? 0,
              chars: (context?.approx_chars ?? 0).toLocaleString(),
            })}
          </div>
          {(context?.turns || []).map((turn) => (
            <div key={turn.run_id} className="text-xs border border-gray-100 rounded-lg p-2 bg-gray-50">
              <div className="text-gray-500 truncate"><b>U:</b> {turn.user_message}</div>
              <div className="text-gray-500 truncate mt-1"><b>A:</b> {turn.response}</div>
              {turn.tools && <div className="text-gray-400 mt-1">{turn.tools}</div>}
            </div>
          ))}
          {!(context?.turns || []).length && (
            <div className="text-sm text-gray-400 text-center py-6">{t('instanceDetail.context.empty')}</div>
          )}
        </div>
      )}

      {activeTab === 'logs' && (
        <div className="bg-gray-900 rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2 border-b border-gray-800">
            <span className="text-xs text-gray-400">{resident ? t('instanceDetail.logs.carrierLog') : t('instanceDetail.logs.runLog')}</span>
            <button type="button" onClick={refreshLogs} className="p-1 rounded text-gray-500 hover:text-gray-300 hover:bg-gray-800">
              <RefreshCw className={`w-3.5 h-3.5 ${logsLoading ? 'animate-spin' : ''}`} />
            </button>
          </div>
          <pre className="text-gray-100 text-xs p-4 overflow-auto max-h-[600px] whitespace-pre-wrap">
            {logs || t('instanceDetail.logs.empty')}
          </pre>
        </div>
      )}

      {activeTab === 'process' && resident && (
        <ProcessTab instance={instance} onInstanceUpdated={setInstance} />
      )}

      {activeTab === 'access' && resident && (
        <AccessTab instance={instance} onInstanceUpdated={setInstance} />
      )}

      {streaming && (
        <div className="bg-white border border-indigo-200 rounded-xl p-3 mt-4">
          <div className="text-xs text-indigo-600 mb-1 inline-flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
            {liveTaskId ? t('instanceDetail.message.taskRun') : (liveActivity || t('instanceDetail.message.working'))}
          </div>
          <div className="text-sm text-gray-800 whitespace-pre-wrap">{liveText}</div>
        </div>
      )}

      {/* The message box: the reason an instance is not just a run log. The
          page's floor, always at the bottom of the screen, the conversation
          scrolling behind it (components/ComposerDock.jsx). */}
      <ComposerDock>
        <InstanceComposer
          agents={agentChoices}
          agentId={isRunner ? runnerAgent : (instance.agent_id || '')}
          isRunner={isRunner}
          runnerAgent={runnerAgent}
          onRunnerAgent={setRunnerAgent}
          canNewConversation={resident}
          onNewConversation={handleNewConversation}
          workspace={instance.workspace || 'default'}
          projectId={instance.project_id || ''}
          streaming={streaming}
          liveRunId={liveRunId}
          onSend={sendMessage}
          onStop={resident ? handleInterrupt : handleStop}
          hint={isRunner
            ? t('instanceDetail.message.runnerHint')
            : instance.delivery === 'direct'
              ? t('instanceDetail.message.directHint')
              : t('instanceDetail.message.queuedHint')}
          note={queuedNote}
          pendingCount={pending.length}
        />
      </ComposerDock>
    </PageContainer>
  );
}
