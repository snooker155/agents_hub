/**
 * What the agent of this chat started in the background: flows, loops and
 * teams (chat/background_runs.py).
 *
 * The composer's footer says what runs and how far it got, in place of the
 * typing hint; a click opens a modal with each run, who works in it and on
 * what. The Build view also shows the work itself: live while the run goes
 * (the node or member writing now drawn as a reply being written), and once
 * it ended from the run's stored record, so a reload keeps it. The chat view
 * keeps to the agent's own replies. When a run ends the hub starts the
 * agent's next turn here with its result.
 */
import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';
import { Link } from 'react-router-dom';
import { CheckCircle2, ExternalLink, Layers, Loader2, Repeat, UsersRound, Workflow, X, XCircle } from 'lucide-react';
import { useI18n } from '../../i18n';
import { getFlowRunRecord } from '../../api';
import { useLiveTurns } from '../runs/liveTurns';
import { reconstructRunMessages, routeText } from '../flowEditor/runFormat';
import { activityText, footerText, progressText, runPagePath, turnKind, workChannels } from './backgroundRunInfo';
import { useMessagesWithTrails } from './runTrail';
import { BuildMessage } from './BuildMessage';
import RunApprovals from '../runs/RunApprovals';

const KIND_ICON = { flow: Workflow, loop: Repeat, team: UsersRound };

// ── In the transcript ─────────────────────────────────────────────────────────

/** One run's card in the Build view: its header, the reply of each node or
 * member, and, once it ended, a line until the agent's turn with its result
 * begins. */
export function BackgroundRunCard({ run }) {
  const { t } = useI18n();
  const turns = useLiveTurns(workChannels(run), turnKind(run));
  const entries = Object.entries(turns);
  const Icon = KIND_ICON[run.kind] || Layers;
  const progress = progressText(t, run);
  return (
    <div className="mb-4" data-testid="background-run-live">
      <RunHeader run={run} icon={Icon} text={run.finished
        ? t('chat.background.workFinished')
        : `${t('chat.background.inBackground')}${progress ? ` · ${progress}` : ''}`} />
      {/* A node's tool call that waits for a person: answered right here. */}
      {approvalsRun(run) ? <RunApprovals {...approvalsRun(run)} className="mb-2" /> : null}
      {entries.map(([key, { meta, turn, done, ok }]) => {
        // A node or member that finished keeps its reply, marked so, instead
        // of vanishing when the next one starts.
        const msg = {
          ...turn, id: `bg-${run.run_id}-${key}`, role: 'agent', streaming: !done,
          working_label: t('chat.background.working'),
          ...(turn.route && !turn.content ? { content: routeText(t, turn.route) } : {}),
        };
        const name = meta?.label || run.name;
        return (
          <div key={key}>
            <BuildMessage msg={msg} isStreaming={!done} agentName={name} />
            {done && <FinishedMark ok={ok !== false} name={name} />}
          </div>
        );
      })}
      {/* The run is over: its card stays until the agent's turn with the
          result begins, so nothing blinks out in between. */}
      {run.finished && !run.woken && (
        <p className="flex items-center justify-center gap-1.5 text-[11px] text-gray-400" data-testid="background-run-finished">
          <Loader2 className="h-3 w-3 animate-spin" />
          {t(`chat.background.handingBack.${run.kind}`, { name: run.name })}
        </p>
      )}
    </div>
  );
}

// Whose waiting calls the card shows: the flow run itself, a loop's pass
// under way, or the team run.
function approvalsRun(run) {
  if (run.finished) return null;
  if (run.kind === 'flow') return run.run_id ? { flowRunId: run.run_id } : null;
  if (run.kind === 'team') return run.run_id ? { teamRunId: run.run_id } : null;
  const pass = (workChannels(run)[0] || '').replace(/^run:/, '');
  return run.kind === 'loop' && pass ? { flowRunId: pass } : null;
}

function RunHeader({ run, icon: Icon, text }) {
  const { t } = useI18n();
  return (
    <div className="mb-2 flex items-center justify-center gap-1.5 text-[11px] text-gray-400">
      <Icon className="h-3 w-3" />
      <span className="truncate">{t(`chat.background.kind.${run.kind}`)} «{run.name}» · {text}</span>
      <Link to={runPagePath(run)} className="text-indigo-500 hover:text-indigo-700" title={t('chat.background.open')}>
        <ExternalLink className="h-3 w-3" />
      </Link>
    </div>
  );
}

/** The replies of a flow run's nodes, from its stored record (null until loaded). */
function useStoredFlowReplies(run) {
  const { t } = useI18n();
  const isFlow = run.kind === 'flow' && run.entity_id && run.run_id;
  const [loaded, setLoaded] = useState({ id: null, record: null });
  useEffect(() => {
    if (!isFlow) return undefined;
    let live = true;
    getFlowRunRecord(run.entity_id, run.run_id)
      .then((r) => { if (live) setLoaded({ id: run.run_id, record: r.data }); })
      .catch(() => { if (live) setLoaded({ id: run.run_id, record: { events: [] } }); });
    return () => { live = false; };
  }, [isFlow, run.entity_id, run.run_id]);
  const record = loaded.id === run.run_id ? loaded.record : null;
  const replies = useMemo(() => (record
    ? reconstructRunMessages(record)
      .filter((m) => m.role === 'agent' && !m.summary)
      .map((m) => (m.route && !m.content ? { ...m, content: routeText(t, m.route) } : m))
    : []), [record, t]);
  return useMessagesWithTrails(replies, Boolean(record));
}

/**
 * A run that ended before this page was opened, in the Build view, above
 * the notice of the turn it woke: its nodes' replies from the stored record
 * (a flow), or its header with a link to its page (a loop, a team).
 */
export function StoredBackgroundRun({ notice }) {
  const { t } = useI18n();
  const run = { ...notice, finished: true };
  const replies = useStoredFlowReplies(run);
  const Icon = KIND_ICON[run.kind] || Layers;
  return (
    <div className="mb-4" data-testid="background-run-stored">
      <RunHeader run={run} icon={Icon} text={t('chat.background.workFinished')} />
      {replies.map((m) => (
        <div key={m.id}>
          <BuildMessage msg={m} agentName={m.agent_label} />
          <FinishedMark ok={!m.error} name={m.agent_label} />
        </div>
      ))}
    </div>
  );
}

function FinishedMark({ ok, name }) {
  const { t } = useI18n();
  const Mark = ok ? CheckCircle2 : XCircle;
  return (
    <p className={`-mt-4 mb-4 flex items-center gap-1 pl-16 text-[11px] ${ok ? 'text-emerald-600' : 'text-rose-600'}`}
      data-testid="background-turn-finished">
      <Mark className="h-3 w-3" />
      {t(ok ? 'chat.background.agentFinished' : 'chat.background.agentFailed', { name })}
    </p>
  );
}

/** The live work of every background run, at the end of a Build view transcript. */
export function BackgroundRunsLive({ runs }) {
  if (!runs?.length) return null;
  return runs.map((run) => <BackgroundRunCard key={run.run_id} run={run} />);
}

// ── The composer's footer and its modal ──────────────────────────────────────

/** The footer's line: what runs in the background, how many; opens the modal. */
export function BackgroundRunsButton({ runs: all }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  // Only what still runs; an ended run waiting to report is in the transcript.
  const runs = (all || []).filter((r) => !r.finished);
  if (!runs.length) return null;
  const label = runs.length === 1 ? footerText(t, runs[0]) : t('chat.background.many', { count: runs.length });
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex max-w-full items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium text-indigo-600 hover:bg-indigo-50"
        title={t('chat.background.showWhat')}
        data-testid="background-runs-button"
      >
        <Loader2 className="h-3 w-3 shrink-0 animate-spin" />
        <span className="truncate">{label}</span>
      </button>
      {open && <BackgroundRunsModal runs={runs} onClose={() => setOpen(false)} />}
    </>
  );
}

function WhoWorks({ run }) {
  const { t } = useI18n();
  const turns = useLiveTurns(workChannels(run), turnKind(run));
  const live = Object.values(turns);
  // Who works, from the stream when it speaks, else from the record.
  const p = run.progress || {};
  const fromRecord = run.kind === 'team'
    ? (p.working || []).map((name) => ({ label: name }))
    : ((run.kind === 'loop' ? p.pass?.current : p.current) || []).map((c) => ({ label: c.label }));
  const rows = live.length
    ? live.map(({ meta, turn }) => ({ label: meta?.label, doing: activityText(t, turn) }))
    : fromRecord;
  if (!rows.length) {
    return <p className="text-xs text-gray-400">{run.kind === 'team' ? t('chat.background.nobodyNow') : t('chat.background.between')}</p>;
  }
  return (
    <ul className="space-y-1">
      {rows.map((r, i) => (
        <li key={i} className="flex items-baseline gap-2 text-xs">
          <span className="shrink-0 font-semibold text-gray-800">{r.label}</span>
          <span className="min-w-0 truncate text-gray-500">{r.doing || t('chat.background.working')}</span>
        </li>
      ))}
    </ul>
  );
}

export function BackgroundRunsModal({ runs, onClose }) {
  const { t } = useI18n();
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose} role="dialog" aria-modal="true" aria-label={t('chat.background.title')}
      data-testid="background-runs-modal"
    >
      <div className="flex max-h-[85vh] w-full max-w-xl flex-col rounded-xl bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between gap-3 border-b border-gray-100 px-5 py-3">
          <h3 className="text-sm font-semibold text-gray-900">{t('chat.background.title')}</h3>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-gray-600" aria-label={t('common.close')}>
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="space-y-3 overflow-y-auto px-5 py-4">
          {runs.map((run) => {
            const Icon = KIND_ICON[run.kind] || Layers;
            const progress = progressText(t, run);
            return (
              <section key={run.run_id} className="rounded-lg border border-gray-200 p-3" data-testid="background-run">
                <div className="flex items-center gap-2">
                  <Icon className="h-4 w-4 shrink-0 text-indigo-500" />
                  <span className="min-w-0 truncate text-sm font-semibold text-gray-900">
                    {t(`chat.background.kind.${run.kind}`)} «{run.name}»
                  </span>
                  <span className="shrink-0 rounded-full bg-blue-50 px-2 py-0.5 text-[10px] font-semibold text-blue-700">
                    {t(`chat.background.status.${run.status}`, { defaultValue: run.status })}
                  </span>
                  <Link to={runPagePath(run)} onClick={onClose}
                    className="ml-auto inline-flex shrink-0 items-center gap-1 text-[11px] text-indigo-600 hover:text-indigo-800">
                    <ExternalLink className="h-3 w-3" /> {t('chat.background.open')}
                  </Link>
                </div>
                {progress && <p className="mt-1 text-xs text-gray-600">{progress}</p>}
                {run.goal && <p className="mt-1 line-clamp-2 text-[11px] text-gray-400" title={run.goal}>{run.goal}</p>}
                <div className="mt-2 border-t border-gray-100 pt-2">
                  <WhoWorks run={run} />
                </div>
              </section>
            );
          })}
        </div>
      </div>
    </div>,
    document.body,
  );
}
