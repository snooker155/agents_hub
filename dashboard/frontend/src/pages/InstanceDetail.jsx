import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { Link } from 'react-router-dom';
import {
  Activity, Box, Brain, Check, Clock, FileText, History, Layers, MessageSquare,
  Pencil, Send, Server, Square, Trash2, Wrench, X,
} from 'lucide-react';

import {
  deleteInstance, getInstance, getInstanceContext, getInstanceLogs,
  getInstanceRuns, getInstanceTimeline, messageInstance, renameInstance,
  stopInstance,
} from '../api';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useStream, useChannel } from '../components/stream';
import { StateBadge } from '../components/InstanceList';
import { formatDuration, relativeTime } from '../components/instanceUtils';
import MarkdownRenderer from '../components/MarkdownRenderer';
import { useI18n } from '../i18n';

/*
 * One live agent copy.
 *
 * The page is a conversation, not a log dump: what this copy was asked, what it
 * answered, what it used to get there — and a box to write to it. The box works
 * whatever state the copy is in. A copy that finished hours ago is revived with
 * its own history rebuilt from its journal; a node in standby is handed the
 * message through its mailbox and answers in its own process.
 */

const TABS = [
  { id: 'timeline', icon: MessageSquare, key: 'timeline' },
  { id: 'runs', icon: History, key: 'runs' },
  { id: 'context', icon: Brain, key: 'context' },
  { id: 'logs', icon: FileText, key: 'logs' },
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

export default function InstanceDetail() {
  const { instanceId } = useParams();
  const { t } = useI18n();
  const { clientId } = useStream();

  const [instance, setInstance] = useState(null);
  const [timeline, setTimeline] = useState(null);
  const [runs, setRuns] = useState({ items: [], total: 0 });
  const [context, setContext] = useState(null);
  const [logs, setLogs] = useState('');
  const [activeTab, setActiveTab] = useState('timeline');
  const [loading, setLoading] = useState(true);

  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [queuedNote, setQueuedNote] = useState('');
  const [liveText, setLiveText] = useState('');
  const [liveActivity, setLiveActivity] = useState('');
  const [streaming, setStreaming] = useState(false);

  const [renaming, setRenaming] = useState(false);
  const [labelDraft, setLabelDraft] = useState('');

  const load = useCallback(async () => {
    try {
      const [inst, tl] = await Promise.all([
        getInstance(instanceId),
        getInstanceTimeline(instanceId),
      ]);
      setInstance(inst.data);
      setTimeline(tl.data);
    } catch (e) {
      console.error('Failed to load instance', e);
      setInstance(null);
    } finally {
      setLoading(false);
    }
  }, [instanceId]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (activeTab === 'runs') getInstanceRuns(instanceId, { limit: 100 })
      .then((r) => setRuns(r.data)).catch(() => setRuns({ items: [], total: 0 }));
    if (activeTab === 'context') getInstanceContext(instanceId)
      .then((r) => setContext(r.data)).catch(() => setContext(null));
    if (activeTab === 'logs') getInstanceLogs(instanceId)
      .then((r) => setLogs(r.data?.logs || '')).catch(() => setLogs(''));
  }, [activeTab, instanceId]);

  // The instance's own channel carries a revived turn as it streams. A message
  // handed to a node's mailbox streams here too, over the run's session.
  useChannel(instanceId ? `instance:${instanceId}` : null, useCallback((ev) => {
    if (!ev || !ev.type) return;
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
      load();
    }
  }, [load]));

  const handleSend = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    setQueuedNote('');
    try {
      const res = await messageInstance(instanceId, { message: text, client_id: clientId });
      setDraft('');
      if (res.data?.mode === 'running') {
        setStreaming(true);
        setLiveText('');
      } else {
        setQueuedNote(t('instanceDetail.message.queued'));
      }
      load();
    } catch (e) {
      setQueuedNote(e.response?.data?.detail || t('instanceDetail.message.failed'));
    } finally {
      setSending(false);
    }
  };

  const handleStop = async () => {
    try { await stopInstance(instanceId); await load(); }
    catch (e) { console.error(e); }
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

  const textareaRef = useRef(null);
  const onKeyDown = (e) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); handleSend(); }
  };

  if (loading) {
    return (
      <PageContainer>
        <div className="p-8 text-center text-sm text-gray-400">{t('instanceDetail.loading')}</div>
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
          agent: instance.agent_id,
          kind: t(`instances.kinds.${instance.kind}`, { defaultValue: instance.kind }),
        })}
        actions={(
          <div className="flex items-center gap-2">
            <StateBadge state={instance.state} t={t} />
            {isBusy && (
              <button type="button" onClick={handleStop}
                      className="px-3 py-1.5 text-sm rounded-lg border border-red-200 text-red-600 hover:bg-red-50 inline-flex items-center gap-1.5">
                <Square className="w-3.5 h-3.5" />{t('instanceDetail.stop')}
              </button>
            )}
            <button type="button" onClick={handleDelete}
                    className="p-2 rounded-lg border border-gray-200 text-gray-400 hover:text-red-600 hover:bg-red-50"
                    title={t('instanceDetail.delete')}>
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      />

      <div className="grid grid-cols-2 md:grid-cols-6 gap-2 mb-4">
        <Stat label={t('instanceDetail.stats.agent')} value={
          <Link to={`/agents/${instance.agent_id}`} className="hover:text-indigo-600">{instance.agent_id}</Link>
        } />
        <Stat label={t('instanceDetail.stats.workspace')} value={instance.workspace || 'default'} />
        <Stat label={t('instanceDetail.stats.runs')} value={instance.runs_count || 0} />
        <Stat label={t('instanceDetail.stats.tokens')} value={(instance.total_tokens || 0).toLocaleString()} />
        <Stat label={t('instanceDetail.stats.duration')} value={formatDuration(instance.total_duration_ms)} />
        <Stat label={t('instanceDetail.stats.lastActivity')} value={relativeTime(instance.last_activity_at, t)} />
      </div>

      {(instance.node_id || instance.container_name) && (
        <div className="mb-4 px-3 py-2 rounded-lg bg-blue-50 border border-blue-100 text-xs text-blue-800 flex items-center gap-2">
          {instance.container_name ? <Box className="w-3.5 h-3.5" /> : <Server className="w-3.5 h-3.5" />}
          {t('instanceDetail.carrierNote')}
          {instance.node_id && (
            <Link to={`/nodes/${instance.node_id}`} className="underline">{t('instanceDetail.openCarrier')}</Link>
          )}
        </div>
      )}

      {/* The message box: the reason an instance is not just a run log. */}
      <div className="bg-white border border-gray-200 rounded-xl p-3 mb-4">
        <div className="text-xs text-gray-500 mb-2">
          {instance.delivery === 'direct'
            ? t('instanceDetail.message.directHint')
            : t('instanceDetail.message.queuedHint')}
        </div>
        <div className="flex gap-2 items-end">
          <textarea
            ref={textareaRef}
            rows={2}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={t('instanceDetail.message.placeholder')}
            className="flex-1 px-3 py-2 text-sm border border-gray-200 rounded-lg resize-y bg-white"
          />
          <button
            type="button"
            onClick={handleSend}
            disabled={sending || !draft.trim()}
            className="px-3 py-2 rounded-lg bg-indigo-600 text-white text-sm disabled:opacity-40 inline-flex items-center gap-1.5"
          >
            <Send className="w-3.5 h-3.5" />{t('instanceDetail.message.send')}
          </button>
        </div>
        {queuedNote && <div className="mt-2 text-xs text-amber-700">{queuedNote}</div>}
        {pending.length > 0 && (
          <div className="mt-2 text-xs text-gray-500 inline-flex items-center gap-1.5">
            <Clock className="w-3 h-3" />
            {t('instanceDetail.message.pending', { count: pending.length })}
          </div>
        )}
      </div>

      {streaming && (
        <div className="bg-white border border-indigo-200 rounded-xl p-3 mb-4">
          <div className="text-xs text-indigo-600 mb-1 inline-flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
            {liveActivity || t('instanceDetail.message.working')}
          </div>
          <div className="text-sm text-gray-800 whitespace-pre-wrap">{liveText}</div>
        </div>
      )}

      <div className="flex gap-1 border-b border-gray-200 mb-3">
        {TABS.map((tab) => (
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
        <pre className="bg-gray-900 text-gray-100 text-xs rounded-xl p-4 overflow-auto max-h-[600px] whitespace-pre-wrap">
          {logs || t('instanceDetail.logs.empty')}
        </pre>
      )}
    </PageContainer>
  );
}
