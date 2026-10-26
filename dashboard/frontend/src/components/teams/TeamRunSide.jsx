import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Brain, ChevronDown, ChevronRight, Crown, DoorOpen, ExternalLink, FileText, Loader, Users, X,
} from 'lucide-react';
import { useI18n } from '../../i18n';
import { ColumnTab } from '../PageTabs';
import ToolStatusMark from '../ToolStatusMark';
import ProcessStepModal from '../ProcessStepModal';
import StepText from '../StepText';
import { toolActivity } from '../chat/toolActivity';
import { parseReasoningContent } from '../chat/reasoningText';
import { useRunTrail } from '../chat/runTrail';
import { StatusBadge } from '../loops/loopUi';
import { durationText } from './teamFormat';
import { isTurnLive, memberActivity, turnRounds } from './teamBoard';

const MEMBER_STATUS = {
  running: 'bg-blue-100 text-blue-700',
  completed: 'bg-green-100 text-green-700',
  failed: 'bg-red-100 text-red-700',
  stopped: 'bg-amber-100 text-amber-700',
  idle: 'bg-gray-100 text-gray-500',
};

/**
 * The steps of one turn: a finished one's fetched once when opened, a live
 * one's from the page's poll (liveTrails.js), which also feeds the member's
 * reply being written in the chat.
 */
function useTurnTrail(turn, open, liveTrail) {
  const live = isTurnLive(turn);
  const finished = useRunTrail(turn?.run_id, open && !live);
  return live ? liveTrail || null : finished;
}

function TurnSteps({ trail }) {
  const { t } = useI18n();
  // The thought opened over the page: the log keeps two lines of each.
  const [thought, setThought] = useState(null);
  const steps = (trail?.timeline || []).filter((e) => e.type === 'tool' || e.type === 'reasoning');
  const files = trail?.files || [];
  if (!steps.length && !files.length) {
    return <p className="text-[11px] italic text-gray-400">{t('teamDetails.noSteps')}</p>;
  }
  return (
    <ul className="space-y-1">
      {steps.map((e, i) => (
        <li key={i} className="flex items-start gap-1.5 text-[11px] leading-snug text-gray-700">
          {e.type === 'tool' ? (
            <>
              <span className="mt-0.5 shrink-0"><ToolStatusMark entry={e} /></span>
              <span className="min-w-0 break-words" title={e.tool}>{toolActivity(t, e.tool, e.input)}</span>
            </>
          ) : (
            <button
              type="button" onClick={() => setThought(e.content || '')}
              title={t('processGraph.openFull')}
              className="flex min-w-0 flex-1 items-start gap-1.5 rounded text-left hover:bg-gray-50"
              data-testid="team-turn-thought"
            >
              <Brain className="mt-0.5 h-3 w-3 shrink-0 text-purple-400" />
              <span className="min-w-0 line-clamp-2 italic text-gray-500">{e.content}</span>
            </button>
          )}
        </li>
      ))}
      {files.map((f) => (
        <li key={f.path} className="flex items-start gap-1.5 text-[11px] text-indigo-700">
          <FileText className="mt-0.5 h-3 w-3 shrink-0" />
          <span className="min-w-0 break-all">{f.path}</span>
        </li>
      ))}
      {thought !== null && (
        <ProcessStepModal title={t('teamDetails.thought')} labelColor="text-violet-700" onClose={() => setThought(null)}>
          <StepText text={parseReasoningContent(thought)} className="text-sm leading-relaxed text-gray-700" />
        </ProcessStepModal>
      )}
    </ul>
  );
}

/** One member turn in the run log: who, which round, how it ended, its steps. */
function TurnEntry({ turn, round, defaultOpen, liveTrail }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(defaultOpen);
  const trail = useTurnTrail(turn, open, liveTrail);
  const live = isTurnLive(turn);
  const Chevron = open ? ChevronDown : ChevronRight;
  const roundLabel = round?.kind === 'result'
    ? t('teamDetails.closingTurn')
    : round ? t('teamDetails.roundN', { n: round.round }) : '';
  return (
    <div className="border-b border-slate-200" data-testid="team-turn">
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-gray-50">
        <Chevron className="h-3.5 w-3.5 shrink-0 text-gray-400" />
        <span className="min-w-0 flex-1">
          <span className="block truncate text-xs font-semibold text-gray-900">{turn.speaker}</span>
          <span className="block truncate text-[11px] text-gray-400">
            {[roundLabel, durationText(turn.duration_ms)].filter(Boolean).join(' · ')}
          </span>
        </span>
        {live ? <Loader className="h-3.5 w-3.5 shrink-0 animate-spin text-blue-500" /> : <StatusBadge status={turn.status} />}
      </button>
      {open && (
        <div className="space-y-2 px-3 pb-3">
          {turn.error && <p className="break-words text-[11px] text-red-700">{turn.error}</p>}
          {trail ? <TurnSteps trail={trail} /> : <p className="text-[11px] text-gray-400">{t('common.loading')}</p>}
          <Link to={`/messages/${turn.run_id}`} className="inline-flex items-center gap-1 text-[11px] text-indigo-600 hover:text-indigo-800">
            <ExternalLink className="h-3 w-3" /> {t('teamDetails.fullRun')}
          </Link>
        </div>
      )}
    </div>
  );
}

/**
 * The right column of the team page, as the flow page has its nodes and its
 * log: **Participants** is the roster with each member's part in the run on
 * screen (working now, how its last turn ended, its turns and their cost),
 * **Log** is every member turn of the run with its steps. Picking a member
 * shows only its turns in the log.
 */
export default function TeamRunSide({ team, run, turns, messages, liveTrails = {}, className = '' }) {
  const { t } = useI18n();
  const [tab, setTab] = useState('members');
  const [only, setOnly] = useState(null);
  const activity = useMemo(() => memberActivity(team.members, turns, messages), [team.members, turns, messages]);
  const rounds = useMemo(() => turnRounds(messages), [messages]);
  const shownTurns = useMemo(
    () => (only ? turns.filter((tn) => tn.speaker === only) : turns),
    [turns, only],
  );
  const lastId = turns.length ? turns[turns.length - 1].run_id : null;
  const entryName = team.entry_display_name || team.members[0]?.display_name;

  return (
    <div className={`flex-col overflow-hidden border-l border-slate-200 bg-white ${className}`} data-testid="team-run-side">
      <div className="grid shrink-0 grid-cols-2 border-b border-gray-200">
        <ColumnTab active={tab === 'members'} onClick={() => setTab('members')} icon={Users} label={t('teamDetails.participants')} />
        <ColumnTab active={tab === 'log'} onClick={() => setTab('log')} icon={FileText} label={t('teamDetails.runLog')} />
      </div>

      {tab === 'members' ? (
        <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-3">
          {team.members.map((m) => {
            const a = activity[m.display_name] || { status: 'idle', turns: 0, cost: 0 };
            const status = run ? a.status : 'idle';
            return (
              <button
                key={m.agent_id} type="button"
                onClick={() => { setOnly(m.display_name); setTab('log'); }}
                title={t('teamDetails.showTurnsOf', { name: m.display_name })}
                className="w-full rounded-xl border border-slate-100 bg-slate-50 px-3 py-2 text-left transition hover:border-indigo-300 hover:bg-indigo-50"
              >
                <div className="flex items-center gap-1.5">
                  <span className="truncate text-sm font-semibold text-slate-800">{m.display_name}</span>
                  {team.mode === 'centralized' && team.leader_agent_id === m.agent_id && <Crown className="h-3.5 w-3.5 shrink-0 text-amber-600" />}
                  {team.mode === 'autonomous' && m.display_name === entryName && <DoorOpen className="h-3.5 w-3.5 shrink-0 text-indigo-600" />}
                  <span className={`ml-auto inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold ${MEMBER_STATUS[status] || MEMBER_STATUS.idle}`}>
                    {status === 'running' && <Loader className="h-2.5 w-2.5 animate-spin" />}
                    {t(`teamDetails.memberStatus.${status}`, { defaultValue: status })}
                  </span>
                </div>
                {m.role && <div className="truncate text-[11px] text-slate-500">{m.role}</div>}
                {m.manifest && <p className="mt-0.5 line-clamp-2 text-[11px] text-slate-500">{m.manifest}</p>}
                {run && a.turns > 0 && (
                  <div className="mt-1 text-[11px] text-slate-400">
                    {t('teamDetails.turnsCount', { count: a.turns })}{a.cost ? ` · $${a.cost.toFixed(4)}` : ''}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          {only && (
            <div className="flex shrink-0 items-center gap-2 border-b border-slate-200 bg-indigo-50/60 px-3 py-1.5 text-[11px] text-indigo-800">
              <span className="min-w-0 flex-1 truncate">{t('teamDetails.onlyTurnsOf', { name: only })}</span>
              <button type="button" onClick={() => setOnly(null)} title={t('teamDetails.allMembers')} aria-label={t('teamDetails.allMembers')}
                className="rounded p-0.5 text-indigo-500 hover:bg-indigo-100">
                <X className="h-3 w-3" />
              </button>
            </div>
          )}
          <div className="min-h-0 flex-1 overflow-y-auto">
            {shownTurns.length === 0 ? (
              <p className="px-3 py-4 text-xs italic text-gray-500">{t('teamDetails.noTurnsYet')}</p>
            ) : shownTurns.map((turn) => (
              <TurnEntry
                // Picking a member opens every turn of theirs: that is what
                // the click asked to see. A new pick lays them out afresh.
                key={`${only || ''}:${turn.run_id}`}
                turn={turn}
                round={rounds[turn.run_id] || (isTurnLive(turn) ? { round: (run?.rounds_done || 0) + 1 } : null)}
                defaultOpen={Boolean(only) || isTurnLive(turn) || turn.run_id === lastId || turn.status === 'failed'}
                liveTrail={liveTrails[String(turn.run_id)]}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
