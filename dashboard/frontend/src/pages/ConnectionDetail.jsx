import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowLeft,
  Ban,
  Eraser,
  MessageCircleQuestion,
  KeyRound,
  Play,
  RefreshCw,
  Share2,
  Trash2,
  Workflow,
} from 'lucide-react';

import {
  answerConnectionRun, deleteConnection, getConnection, pruneConnection,
  rotateConnectionToken, updateConnection,
} from '../api';
import GraphMirror from '../components/GraphMirror';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useWorkspace } from '../components/workspace';
import { CopyButton, SetupSnippet } from '../components/ConnectionSetup';
import { ImportedMark } from '../components/RunOriginBadges';
import { useI18n } from '../i18n';

/**
 * One connection: its shape, what it has been running, and its credential.
 *
 * Three tabs rather than one long page, because the three questions are asked
 * at different times: what does this thing look like (once), what has it been
 * doing (often), and how do I re-issue its token (rarely, and usually urgently).
 */

const TABS = ['overview', 'runs', 'setup'];

function fmtWhen(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function fmtDuration(ms) {
  if (!ms) return '—';
  const secs = Math.round(ms / 1000);
  if (secs < 60) return `${secs}s`;
  return `${Math.floor(secs / 60)}m ${secs % 60}s`;
}

const RUN_STATUS = {
  completed: 'bg-emerald-100 text-emerald-700',
  failed: 'bg-red-100 text-red-700',
  running: 'bg-blue-100 text-blue-700',
  awaiting_input: 'bg-amber-100 text-amber-800',
  answered: 'bg-amber-50 text-amber-700',
  stopped: 'bg-gray-100 text-gray-500',
};

/**
 * A run that stopped to ask something, and the box to answer it in.
 *
 * The graph is suspended in its own process with its state on its own
 * checkpointer; nothing here can push an answer to it. What this does is record
 * the answer, which the client collects the next time it asks. So the wording
 * says the answer has been stored, not that the graph has resumed: it resumes
 * when its own code next polls, which may be seconds or the next scheduled run.
 */
function PendingQuestion({ run, onAnswer }) {
  const { t } = useI18n();
  const pending = run.pending_question || {};
  const [value, setValue] = useState('');
  const [sending, setSending] = useState(false);

  const send = async (answer) => {
    setSending(true);
    try {
      await onAnswer(run.run_id, answer);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 p-4">
      <div className="flex items-center gap-2 mb-2">
        <MessageCircleQuestion className="w-4 h-4 text-amber-600" />
        <h3 className="text-sm font-semibold text-amber-900">{t('connections.waitingTitle')}</h3>
        {pending.node && (
          <span className="text-[11px] font-semibold text-amber-700 bg-amber-100 px-2 py-0.5 rounded-full">
            {pending.node}
          </span>
        )}
      </div>
      <p className="text-sm text-amber-900 mb-3 whitespace-pre-wrap">
        {pending.question || t('connections.waitingNoQuestion')}
      </p>

      {pending.choices?.length > 0 ? (
        <div className="flex flex-wrap gap-2">
          {pending.choices.map((choice) => (
            <button
              key={choice}
              type="button"
              disabled={sending}
              onClick={() => send(choice)}
              className="px-3 py-1.5 text-sm font-semibold text-amber-900 bg-white border border-amber-300 rounded-lg hover:bg-amber-100 disabled:opacity-50"
            >
              {choice}
            </button>
          ))}
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && value.trim() && send(value.trim())}
            placeholder={t('connections.answerPlaceholder')}
            className="flex-1 min-w-[200px] border border-amber-300 rounded-lg px-3 py-2 text-sm focus:ring-amber-500 focus:border-amber-500"
          />
          <button
            type="button"
            disabled={sending || !value.trim()}
            onClick={() => send(value.trim())}
            className="px-4 py-2 text-sm font-bold text-white bg-amber-600 rounded-lg hover:bg-amber-700 disabled:opacity-50"
          >
            {t('connections.answer')}
          </button>
        </div>
      )}
      <p className="text-[11px] text-amber-700 mt-2">{t('connections.answerHint')}</p>
    </div>
  );
}

function RunsTable({ runs }) {
  const { t } = useI18n();
  if (!runs.length) {
    return <p className="text-sm text-gray-400 italic">{t('connections.noRuns')}</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wider text-gray-400">
            <th className="py-2 pr-4 font-semibold">{t('connections.runStarted')}</th>
            <th className="py-2 pr-4 font-semibold">{t('connections.runStatus')}</th>
            <th className="py-2 pr-4 font-semibold">{t('connections.runPath')}</th>
            <th className="py-2 pr-4 font-semibold">{t('connections.runTokens')}</th>
            <th className="py-2 font-semibold">{t('connections.runDuration')}</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.run_id} className="border-t border-gray-100">
              <td className="py-2 pr-4 text-gray-600 whitespace-nowrap">
                <Link to={`/messages/${run.run_id}`} className="hover:text-indigo-600">
                  {fmtWhen(run.started_at || run.created_at)}
                </Link>
              </td>
              <td className="py-2 pr-4">
                <span className={`px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                  RUN_STATUS[run.status] || RUN_STATUS.stopped}`}
                >
                  {run.status}
                </span>
                <ImportedMark run={run} />
              </td>
              {/* The path through the graph is the one column a list of graph
                  runs wants, and the reason it is stored on the run record. */}
              <td className="py-2 pr-4 text-gray-500 text-[11px] truncate max-w-[220px]">
                {(run.graph_path || []).join(' → ') || '—'}
              </td>
              <td className="py-2 pr-4 text-gray-500 text-[11px]">
                {run.process?.token_usage?.total_tokens || 0}
              </td>
              <td className="py-2 text-gray-500 text-[11px]">
                {fmtDuration(run.process?.duration_ms)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function ConnectionDetail() {
  const { t } = useI18n();
  const { connectionId } = useParams();
  const navigate = useNavigate();
  // Sent with every call on this page: a connection that belongs to another
  // workspace answers 404, which is what stops this page acting on one.
  const { selectedWorkspace } = useWorkspace();
  const [data, setData] = useState(null);
  const [runs, setRuns] = useState([]);
  const [tab, setTab] = useState('overview');
  const [error, setError] = useState('');
  const [issuedToken, setIssuedToken] = useState(null);
  const [busy, setBusy] = useState(false);
  const [keep, setKeep] = useState('');
  const [pruned, setPruned] = useState(null);

  const load = useCallback(async () => {
    try {
      const resp = await getConnection(connectionId, selectedWorkspace);
      setData(resp.data.connection);
      setRuns(resp.data.runs || []);
      setKeep(String(resp.data.connection?.retention?.runs ?? ''));
      setError('');
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    }
  }, [connectionId, selectedWorkspace]);

  useEffect(() => { load(); }, [load]);

  const rotate = async () => {
    setBusy(true);
    try {
      const { data: body } = await rotateConnectionToken(connectionId, selectedWorkspace);
      setIssuedToken(body.token);
      setTab('setup');
      await load();
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setBusy(false);
    }
  };

  const toggleDisabled = async () => {
    setBusy(true);
    try {
      await updateConnection(connectionId, { disabled: !data.disabled }, selectedWorkspace);
      await load();
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setBusy(false);
    }
  };

  const saveRetention = async () => {
    setBusy(true);
    try {
      await updateConnection(connectionId, { retention_runs: Math.max(0, Number(keep) || 0) }, selectedWorkspace);
      await load();
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setBusy(false);
    }
  };

  const pruneNow = async () => {
    setBusy(true);
    setPruned(null);
    try {
      const { data: body } = await pruneConnection(connectionId, selectedWorkspace);
      setPruned(body);
      await load();
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setBusy(false);
    }
  };

  const answer = async (runId, value) => {
    try {
      await answerConnectionRun(connectionId, runId, { value }, selectedWorkspace);
      await load();
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    }
  };

  const remove = async () => {
    // The runs survive, and saying so is the difference between a confident
    // click and a support question about lost history.
    if (!window.confirm(t('connections.confirmDelete', { name: data?.name || connectionId }))) return;
    await deleteConnection(connectionId, selectedWorkspace);
    navigate('/connections');
  };

  if (error && !data) {
    return (
      <PageContainer>
        <p className="text-sm text-red-600">{error}</p>
        <Link to="/connections" className="text-sm text-indigo-600">{t('connections.back')}</Link>
      </PageContainer>
    );
  }
  if (!data) {
    return <PageContainer><p className="text-sm text-gray-400">{t('connections.loading')}</p></PageContainer>;
  }

  const stats = data.stats || {};
  const topology = data.topology || null;
  const waiting = runs.filter((run) => run.status === 'awaiting_input');
  // Where the newest paused run stopped, so the picture shows it.
  const pausedNode = waiting[0]?.pending_question?.node || null;

  return (
    <PageContainer>
      <PageHeader
        icon={Share2}
        title={data.name}
        description={t('connections.detailDescription')}
        actions={<>
          <Link
            to="/connections"
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <ArrowLeft className="w-4 h-4" />
            {t('connections.back')}
          </Link>
          <button
            onClick={load}
            className="flex items-center gap-2 px-3 py-2 text-sm text-gray-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50"
          >
            <RefreshCw className="w-4 h-4" />
            {t('connections.refresh')}
          </button>
        </>}
      />

      {data.disabled && (
        <div className="flex items-center gap-2 mb-4 rounded-lg bg-amber-50 border border-amber-200 px-3 py-2 text-sm text-amber-800">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          {t('connections.disabledNotice')}
        </div>
      )}

      {/* Above the tabs, because a run waiting on a person is the only thing on
          this page that is waiting on the person reading it. */}
      {waiting.map((run) => (
        <PendingQuestion key={run.run_id} run={run} onAnswer={answer} />
      ))}

      <div className="grid gap-3 sm:grid-cols-4 mb-5">
        <Stat label={t('connections.statRuns')} value={stats.runs || 0} />
        <Stat label={t('connections.statFailed')} value={stats.failed || 0} tone={stats.failed ? 'bad' : null} />
        <Stat label={t('connections.statRunning')} value={stats.running || 0} />
        <Stat label={t('connections.statLastSeen')} value={fmtWhen(data.last_seen)} small />
      </div>

      <div className="flex items-center gap-1 mb-4 border-b border-gray-200">
        {TABS.map((name) => (
          <button
            key={name}
            onClick={() => setTab(name)}
            className={`px-3 py-2 text-sm font-semibold border-b-2 -mb-px ${
              tab === name ? 'border-indigo-600 text-indigo-700' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            {t(`connections.tabs.${name}`)}
          </button>
        ))}
      </div>

      {error && <p className="text-sm text-red-600 mb-3">{error}</p>}

      {tab === 'overview' && (
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <Workflow className="w-4 h-4 text-indigo-500" />
            <h3 className="text-sm font-semibold text-gray-700">{t('connections.graphTitle')}</h3>
            {topology?.framework && topology.framework !== 'unknown' && (
              <span className="text-[10px] uppercase tracking-wider font-bold text-indigo-600 bg-indigo-50 px-2 py-0.5 rounded-full">
                {topology.framework}
              </span>
            )}
          </div>
          {topology?.nodes?.length ? (
            <GraphMirror topology={topology} pausedNode={pausedNode} />
          ) : (
            // Nothing here can go and fetch the shape, so an absent picture is
            // a thing the client has not done rather than a failure here.
            <p className="text-sm text-gray-400 italic">{t('connections.noTopology')}</p>
          )}
        </div>
      )}

      {tab === 'runs' && (
        <div className="bg-white border border-gray-200 rounded-xl p-4">
          <RunsTable runs={runs} />
        </div>
      )}

      {tab === 'setup' && (
        <div className="bg-white border border-gray-200 rounded-xl p-4 space-y-5">
          {issuedToken ? (
            <div>
              <p className="text-sm text-gray-500 mb-2">{t('connections.tokenOnce')}</p>
              <div className="flex items-center gap-2">
                <code className="flex-1 text-xs bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 break-all">
                  {issuedToken}
                </code>
                <CopyButton value={issuedToken} />
              </div>
            </div>
          ) : (
            <p className="text-sm text-gray-500">
              {t('connections.tokenHidden', { hint: data.token_hint || '????' })}
            </p>
          )}

          <SetupSnippet token={issuedToken} connectionId={data.id} />

          {/* What this connection keeps. A reporting graph outgrows an age
              limit, so the cap is a count, and it is on screen because a limit
              nobody can see is one people discover by missing data. */}
          <div className="pt-2 border-t border-gray-100">
            <label className="block text-[10px] text-gray-400 uppercase tracking-wider font-semibold mb-1">
              {t('connections.retentionLabel')}
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="number"
                min="0"
                value={keep}
                onChange={(e) => setKeep(e.target.value)}
                className="w-28 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-indigo-500 focus:border-indigo-500"
              />
              <button
                onClick={saveRetention}
                disabled={busy}
                className="px-3 py-2 text-sm font-semibold text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
              >
                {t('connections.save')}
              </button>
              <button
                onClick={pruneNow}
                disabled={busy}
                className="flex items-center gap-2 px-3 py-2 text-sm font-semibold text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
              >
                <Eraser className="w-4 h-4" />
                {t('connections.pruneNow')}
              </button>
              {pruned && (
                <span className="text-xs text-gray-500">
                  {t('connections.pruned', { runs: pruned.removed_runs })}
                </span>
              )}
            </div>
            <p className="text-[11px] text-gray-400 mt-2">
              {data.retention?.source === 'connection'
                ? t('connections.retentionOwn')
                : t('connections.retentionDefault', { runs: data.retention?.runs ?? 0 })}
            </p>
          </div>

          <div className="flex flex-wrap gap-2 pt-2 border-t border-gray-100">
            <button
              onClick={rotate}
              disabled={busy}
              className="flex items-center gap-2 px-3 py-2 text-sm font-semibold text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
            >
              <KeyRound className="w-4 h-4" />
              {t('connections.rotate')}
            </button>
            <button
              onClick={toggleDisabled}
              disabled={busy}
              className="flex items-center gap-2 px-3 py-2 text-sm font-semibold text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50"
            >
              {data.disabled ? <Play className="w-4 h-4" /> : <Ban className="w-4 h-4" />}
              {data.disabled ? t('connections.enable') : t('connections.disable')}
            </button>
            <button
              onClick={remove}
              className="flex items-center gap-2 px-3 py-2 text-sm font-semibold text-red-600 bg-white border border-red-200 rounded-lg hover:bg-red-50 ml-auto"
            >
              <Trash2 className="w-4 h-4" />
              {t('connections.delete')}
            </button>
          </div>
          <p className="text-[11px] text-gray-400">{t('connections.rotateHint')}</p>
        </div>
      )}
    </PageContainer>
  );
}

function Stat({ label, value, tone, small }) {
  return (
    <div className="bg-white border border-gray-200 rounded-xl px-4 py-3">
      <div className="text-[10px] uppercase tracking-wider text-gray-400 font-semibold">{label}</div>
      <div className={`${small ? 'text-sm' : 'text-xl'} font-bold ${tone === 'bad' ? 'text-red-600' : 'text-gray-800'}`}>
        {value}
      </div>
    </div>
  );
}
