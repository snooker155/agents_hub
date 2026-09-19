import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import {
  ChevronLeft, Play, Square, Trash2, Loader, Save, AlertTriangle, X, Crown,
  Wand2, MessageSquare, DollarSign, Eye, ExternalLink, Radio, Plus, DoorOpen,
  History, Settings as SettingsIcon, UsersRound,
} from 'lucide-react';
import {
  getTeam, updateTeam, estimateTeam, startTeamRun, getTeamRuns, getTeamRun,
  getTeamMessages, stopTeamRun, suggestTeamManifest, getTeamBriefing, getAgents,
  getTeamChat, clearTeamChat, stopTeamChat, teamChatUrl,
} from '../api';
import EntityChat from '../components/EntityChat';
import { usePageChat } from '../components/pageChat/pageChat';
import { ChatColumn, ChatToggle, FILL_COLUMN, useChatColumn } from '../components/ChatColumn';
import { useWorkspace } from '../components/workspace';
import { useChannel } from '../components/stream';
import {
  MODE_BADGE, modeHelp, modeLabel, modeOptions, STATUS_STYLES, isLive,
} from '../components/teamModes';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
/**
 * One team: what it is asked to do, how it is set up, and everything it has done.
 *
 * Three tabs, because they are three different jobs. **Work** is the request box
 * and the live board — a team is a conversation, so it is shown as one.
 * **Settings** is the roster and the manifests, which are the load-bearing part
 * and need room. **History** is every run this team has done, so a finished run
 * is read back exactly the way it was watched.
 */

const KIND_STYLES = {
  goal: 'border-l-4 border-gray-400 bg-gray-50',
  system: 'border-l-4 border-gray-300 bg-gray-50',
  instruction: 'border-l-4 border-indigo-400 bg-indigo-50/50',
  message: 'border-l-4 border-gray-200 bg-white',
  result: 'border-l-4 border-green-500 bg-green-50/50',
  verdict: 'border-l-4 border-green-500 bg-green-50/50',
  error: 'border-l-4 border-red-400 bg-red-50/50',
};

const TABS = [
  { id: 'work', label: 'Work', icon: MessageSquare },
  { id: 'settings', label: 'Settings', icon: SettingsIcon },
  { id: 'history', label: 'History', icon: History },
];

/**
 * The team's own build chat, pinned to this team: the Team Creator edits the
 * roster in place and the page picks up the result.
 *
 * One descriptor, because the same conversation has two homes — the column
 * beside the roster, and the floating page chat. The callbacks are memoised on
 * the team id because EntityChat loads its transcript in an effect keyed on
 * them; fresh closures each render would refetch the conversation continuously.
 */
function useTeamChatDescriptor(teamId, onTeamChanged) {
  const { t } = useI18n();

  const loadChat = useCallback(() => getTeamChat(teamId), [teamId]);
  const clearChat = useCallback(() => clearTeamChat(teamId), [teamId]);
  const stopChat = useCallback(() => stopTeamChat(teamId), [teamId]);

  const onEvent = useCallback((ev) => {
    if (ev.type === 'team' && ev.team) onTeamChanged(ev.team);
  }, [onTeamChanged]);

  return useMemo(() => (teamId ? {
    scope: `team:${teamId}`,
    path: teamChatUrl(teamId),
    loadChat, clearChat, stopChat, onEvent,
    title: t('teamDetails.buildChat'),
    emptyHint: t('teamDetails.buildChatHint'),
    suggestions: [
      t('teamDetails.chatSuggestManifest'),
      t('teamDetails.chatSuggestMember'),
      t('teamDetails.chatSuggestMode'),
      t('teamDetails.chatSuggestCharter'),
    ],
  } : null), [teamId, loadChat, clearChat, stopChat, onEvent, t]);
}

function MemberCard({ member, agents, onChange, onRemove, isLeader, isEntry }) {
  const { t } = useI18n();
  const [suggesting, setSuggesting] = useState(false);
  const suggest = async () => {
    setSuggesting(true);
    try {
      const { data } = await suggestTeamManifest(member.agent_id);
      onChange({ ...member, manifest: data.manifest, role: member.role || data.role });
    } catch { /* the suggestion is a convenience, not a requirement */ }
    setSuggesting(false);
  };
  const agent = agents.find((a) => a.id === member.agent_id);
  return (
    <div className="border border-gray-200 rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-sm font-semibold text-gray-900">
          {agent?.name || member.agent_id}
        </span>
        {isLeader && (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5">
            <Crown className="w-3 h-3" /> {t('teamDetails.lead2')}
          </span>
        )}
        {isEntry && (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-full px-2 py-0.5">
            <DoorOpen className="w-3 h-3" /> {t('teamDetails.takesTheRequest')}
          </span>
        )}
        <button onClick={onRemove} className="ml-auto p-1 text-gray-400 hover:text-red-600">
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="block text-[11px] font-semibold text-gray-500 mb-0.5">
            {t('teamDetails.nameTeammatesUse')}
          </label>
          <input
            value={member.name}
            onChange={(e) => onChange({ ...member, name: e.target.value })}
            placeholder={agent?.name || member.agent_id}
            className="w-full text-sm border border-gray-300 rounded-lg px-2 py-1.5"
          />
        </div>
        <div>
          <label className="block text-[11px] font-semibold text-gray-500 mb-0.5">{t('teamDetails.role')}</label>
          <input
            value={member.role}
            onChange={(e) => onChange({ ...member, role: e.target.value })}
            placeholder={t('teamDetails.reviewer')}
            className="w-full text-sm border border-gray-300 rounded-lg px-2 py-1.5"
          />
        </div>
      </div>
      <div>
        <div className="flex items-center justify-between mb-0.5">
          <label className="block text-[11px] font-semibold text-gray-500">
            {t('teamDetails.manifestWhatThisMemberWill')}
          </label>
          <button
            onClick={suggest}
            className="inline-flex items-center gap-1 text-[11px] font-semibold text-indigo-600 hover:text-indigo-800"
          >
            {suggesting ? <Loader className="w-3 h-3 animate-spin" /> : <Wand2 className="w-3 h-3" />}
            suggest
          </button>
        </div>
        <textarea
          value={member.manifest} rows={3}
          onChange={(e) => onChange({ ...member, manifest: e.target.value })}
          placeholder={t('teamDetails.reviewsEveryDiffBeforeIt')}
          className="w-full text-sm border border-gray-300 rounded-lg px-2 py-1.5"
        />
        {!member.manifest.trim() && (
          <p className="text-[11px] text-amber-700 mt-1">
            {t('teamDetails.withoutAManifestTheOther')}
          </p>
        )}
      </div>
    </div>
  );
}

function BoardMessage({ msg }) {
  const { t } = useI18n();
  const recipients = (msg.recipients || []).filter((r) => r !== '*');
  return (
    <div className={`rounded-lg p-3 ${KIND_STYLES[msg.kind] || KIND_STYLES.message}`}>
      <div className="flex items-center gap-2 mb-1 flex-wrap">
        <span className="text-sm font-bold text-gray-900">{msg.sender}</span>
        {recipients.length > 0 && (
          <span className="text-xs text-indigo-600">→ {recipients.join(', ')}</span>
        )}
        {recipients.length === 0 && msg.kind === 'message' && (
          <span className="text-xs text-gray-400">{t('teamDetails.everyone')}</span>
        )}
        <span className="text-xs text-gray-400">{t('teamDetails.round', { n: msg.round })}</span>
        {msg.kind !== 'message' && (
          <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-500">
            {msg.kind}
          </span>
        )}
        <span className="ml-auto text-[11px] text-gray-400">
          {msg.cost ? `$${msg.cost.toFixed(4)}` : ''}
        </span>
      </div>
      <p className="text-sm text-gray-800 whitespace-pre-wrap">{msg.content}</p>
      {msg.run_id && (
        <Link
          to={`/messages/${msg.run_id}`}
          className="mt-1 inline-flex items-center gap-1 text-[11px] text-indigo-600 hover:text-indigo-800"
        >
          <ExternalLink className="w-3 h-3" /> {t('teamDetails.fullRun')}
        </Link>
      )}
    </div>
  );
}

export default function TeamDetails() {
  const { t } = useI18n();
  const { teamId } = useParams();
  const { selectedWorkspace } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const tab = params.get('tab') || 'work';
  const setTab = (next) => setParams(next === 'work' ? {} : { tab: next }, { replace: true });
  const chat = useChatColumn(false);

  const [team, setTeam] = useState(null);
  const [draft, setDraft] = useState(null);
  const [agents, setAgents] = useState([]);
  const [runs, setRuns] = useState([]);
  const [run, setRun] = useState(null);
  const [messages, setMessages] = useState([]);
  const [goal, setGoal] = useState('');
  const [estimate, setEstimate] = useState(null);
  const [briefing, setBriefing] = useState(null);
  const [addAgent, setAddAgent] = useState('');
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const boardEndRef = useRef(null);

  // The build chat, drawn either in the column beside the roster or in the
  // floating panel — one descriptor, so it is the same conversation either way.
  const applyTeamFromChat = useCallback((updated) => {
    setTeam(updated);
    setDraft(updated);
  }, []);
  const teamChat = useTeamChatDescriptor(team?.team_id, applyTeamFromChat);
  usePageChat(teamChat);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await getAgents(selectedWorkspace);
        setAgents(data.agents || data || []);
      } catch { /* the picker degrades to ids */ }
    })();
  }, [selectedWorkspace]);

  const loadRun = useCallback(async (teamRunId) => {
    try {
      const { data } = await getTeamRun(teamRunId);
      setRun(data);
      setMessages(data.messages || []);
    } catch {
      setMessage(t('teamDetails.loadRunFailed'));
    }
  }, [t]);

  const loadRuns = useCallback(async () => {
    try {
      const { data } = await getTeamRuns(teamId);
      setRuns(data.runs || []);
      return data.runs || [];
    } catch {
      return [];
    }
  }, [teamId]);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await getTeam(teamId);
        setTeam(data); setDraft(data);
        const history = await loadRuns();
        if (history.length) loadRun(history[0].team_run_id);
      } catch {
        setMessage(t('teamDetails.loadTeamFailed'));
      }
    })();
  }, [teamId, loadRuns, loadRun, t]);

  // The board arrives live; polling is the fallback so a dropped stream slows
  // the page down rather than freezing it.
  useChannel(run?.team_run_id ? `team:${run.team_run_id}` : null, (ev) => {
    const d = ev?.data;
    if (!d) return;
    if (d.type === 'message') {
      setMessages((prev) => (prev.some((m) => m.seq === d.seq) ? prev : [...prev, d]));
    } else if (d.type === 'team_done' || d.type === 'stopping') {
      setRun((prev) => ({ ...prev, ...d }));
      if (d.type === 'team_done') loadRuns();
    }
  });

  useEffect(() => {
    if (!run?.team_run_id || !isLive(run.status)) return undefined;
    const timer = setInterval(async () => {
      try {
        const since = messages.length ? messages[messages.length - 1].seq : 0;
        const { data } = await getTeamMessages(run.team_run_id, since);
        if (data.messages?.length) {
          setMessages((prev) => {
            const seen = new Set(prev.map((m) => m.seq));
            return [...prev, ...data.messages.filter((m) => !seen.has(m.seq))];
          });
        }
        setRun((prev) => ({ ...prev, ...data }));
        if (!isLive(data.status)) loadRuns();
      } catch { /* transient */ }
    }, 3000);
    return () => clearInterval(timer);
  }, [run?.team_run_id, run?.status, messages, loadRuns]);

  useEffect(() => {
    boardEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [messages.length]);

  const live = isLive(run?.status);

  const handleStart = async () => {
    if (!team) return;
    setStarting(true); setMessage('');
    try {
      const { data } = await startTeamRun(team.team_id, { goal, workspace: selectedWorkspace });
      if (data.team_run_id) {
        setMessages([]);
        setRun(data);
        setGoal('');
        setTab('work');
        loadRuns();
      }
    } catch (e) {
      setMessage(e.response?.data?.detail || t('teamDetails.startFailed'));
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    if (!run?.team_run_id) return;
    setStopping(true);
    // Optimistic, because the request returns as soon as the turns in flight
    // have been interrupted and the button should not look inert until then.
    setRun((prev) => ({ ...prev, status: 'stopping' }));
    try {
      await stopTeamRun(run.team_run_id);
    } catch {
      loadRun(run.team_run_id);
    } finally {
      setStopping(false);
    }
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true); setMessage('');
    try {
      const { data } = await updateTeam(draft.team_id, draft);
      setTeam(data); setDraft(data);
      setMessage(t('teamDetails.saved'));
    } catch (e) {
      setMessage(e.response?.data?.detail || t('teamDetails.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const showBriefing = async (agentId) => {
    try {
      const { data } = await getTeamBriefing(team.team_id, agentId);
      setBriefing(data);
    } catch (e) {
      setMessage(e.response?.data?.detail || t('teamDetails.briefingFailed'));
    }
  };

  const set = (patch) => setDraft((d) => ({ ...d, ...patch }));
  const setMember = (idx, member) =>
    set({ members: draft.members.map((m, i) => (i === idx ? member : m)) });

  if (!team || !draft) {
    return (
      <div className="p-6 text-sm text-gray-500 flex items-center gap-2">
        <Loader className="w-4 h-4 animate-spin" /> {t('teamDetails.loadingTheTeam')}
      </div>
    );
  }

  const entryId = draft.entry_agent_id || draft.members[0]?.agent_id;

  return (
    <PageContainer>
      <PageHeader
        icon={UsersRound}
        title={team.name}
        backTo="/teams"
        backLabel={t('teamDetails.allTeams')}
        badges={
          <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border ${MODE_BADGE[team.mode] || MODE_BADGE.parallel}`}>
            {modeLabel(team.mode, t)}
          </span>
        }
        description={<>
          {team.mode === 'centralized' && `led by ${team.leader_display_name} · `}
          {team.mode === 'autonomous' && `${team.entry_display_name || team.members[0]?.display_name} takes the request · `}
          {team.members.length} members · up to {team.max_rounds} rounds
        </>}
        actions={<>
          <ChatToggle open={chat.open} onToggle={chat.toggle}
                      label={t('teamDetails.buildChat')} />
          <button
            onClick={async () => {
                try {
                  const { data } = await estimateTeam(team.team_id);
                  setEstimate(data);
                } catch { /* estimate is advisory */ }
              }}
              className="inline-flex items-center px-3 py-2 text-xs font-semibold text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-lg hover:bg-indigo-100"
            >
              <DollarSign className="w-3.5 h-3.5 mr-1" /> {t('teamDetails.estimate')}
            </button>
            {live ? (
              <button
                onClick={handleStop} disabled={stopping}
                className="inline-flex items-center px-3 py-2 text-xs font-semibold text-white bg-amber-600 rounded-lg hover:bg-amber-700 disabled:opacity-60"
              >
                {stopping ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" />
                          : <Square className="w-3.5 h-3.5 mr-1" />}
                Stop
              </button>
            ) : (
              <button
                onClick={handleStart}
                disabled={starting || !team.members.length}
                className="inline-flex items-center px-3 py-2 text-xs font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              >
                {starting ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" />
                          : <Play className="w-3.5 h-3.5 mr-1" />}
                Run
              </button>
            )}
        </>}
      />

      {message && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {message}
          <button onClick={() => setMessage('')} className="ml-auto p-0.5 text-amber-600">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Transport — the request handed to the team */}
      <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
        <div>
          <label className="block text-xs font-semibold text-gray-600 mb-1">
            {t('teamDetails.theRequestToHandThe')}
          </label>
          <textarea
            value={goal} onChange={(e) => setGoal(e.target.value)} rows={2}
            disabled={live}
            placeholder={t('teamDetails.whatTheTeamShouldDeliver')}
            className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2 disabled:bg-gray-50"
          />
          <p className="text-xs text-gray-500 mt-1">
            {t('teamDetails.requestHint')}
          </p>
        </div>

        {estimate && (
          <div className="mt-3 rounded-lg border border-indigo-100 bg-indigo-50 p-3 text-xs text-indigo-800">
            <span className="font-bold">{t('teamDetails.upToLlmCalls', { count: estimate.llm_calls_upper_bound })}</span>
            {' — '}{t('teamDetails.membersRounds', { members: estimate.members, rounds: estimate.max_rounds })}{' '}
            <span className="text-indigo-600">
              {t(`teamDetails.${estimate.note_key}`, { defaultValue: estimate.note })}
            </span>
          </div>
        )}
      </div>

      {/* Tabs stay above the row: inside the scrolling column they would slide
          out of reach as soon as the content was longer than the screen. */}
      <div className="mt-4 border-b border-gray-200 flex gap-1 shrink-0">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id} onClick={() => setTab(id)}
            className={`inline-flex items-center gap-1.5 px-4 py-2 text-sm font-semibold border-b-2 -mb-px ${
              tab === id
                ? 'border-indigo-600 text-indigo-700'
                : 'border-transparent text-gray-500 hover:text-gray-800'
            }`}
          >
            <Icon className="w-4 h-4" /> {label}
            {id === 'history' && runs.length > 0 && (
              <span className="text-[11px] text-gray-400">({runs.length})</span>
            )}
          </button>
        ))}
      </div>

      {/* The page and the chat that edits the same team. */}
      <div className={chat.gridClass}>
        <div className={chat.mainClass}>
      <div className="mt-4 space-y-4">
        {tab === 'work' && (
          <>
            <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
              <h3 className="text-xs font-bold uppercase tracking-wide text-gray-500 mb-2">
                {t('teamDetails.whoIsOnThisTeam')}
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {team.members.map((m) => (
                  <div key={m.agent_id} className="border border-gray-200 rounded-lg p-3">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-gray-900">{m.display_name}</span>
                      {team.leader_agent_id === m.agent_id && (
                        <Crown className="w-3.5 h-3.5 text-amber-600" />
                      )}
                      {team.mode === 'autonomous' && m.display_name === (team.entry_display_name || team.members[0]?.display_name) && (
                        <DoorOpen className="w-3.5 h-3.5 text-indigo-600" />
                      )}
                      {m.role && <span className="text-xs text-gray-500">{m.role}</span>}
                    </div>
                    <p className="text-xs text-gray-600 mt-1 line-clamp-3">
                      {m.manifest || 'no manifest'}
                    </p>
                  </div>
                ))}
              </div>
            </div>

            {!run ? (
              <div className="bg-white rounded-xl border border-gray-200 p-10 text-center text-sm text-gray-500">
                {t('teamDetails.noRunsYetGiveThe')}
              </div>
            ) : (
              <>
                <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm flex items-center gap-3 flex-wrap">
                  <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${STATUS_STYLES[run.status] || 'bg-gray-100 text-gray-600'}`}>
                    {run.status}
                  </span>
                  {live && <Radio className="w-3.5 h-3.5 text-blue-500 animate-pulse" />}
                  <span className="text-sm text-gray-600">
                    {run.rounds_done} round{run.rounds_done === 1 ? '' : 's'}
                  </span>
                  <span className="text-sm text-gray-600">${(run.total_cost || 0).toFixed(4)}</span>
                  {run.stop_reason && (
                    <span className="text-sm text-gray-500 italic">— {run.stop_reason.replace(/_/g, ' ')}</span>
                  )}
                  {run.task_id && (
                    <Link to={`/tasks/${run.task_id}`} className="text-xs text-indigo-600 hover:text-indigo-800 inline-flex items-center gap-1">
                      <ExternalLink className="w-3 h-3" /> {t('teamDetails.task')}
                    </Link>
                  )}
                </div>

                <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
                  <h3 className="text-xs font-bold uppercase tracking-wide text-gray-500 mb-3 flex items-center gap-1.5">
                    <MessageSquare className="w-3.5 h-3.5" /> {t('teamDetails.theBoard')}
                  </h3>
                  <div className="space-y-2 max-h-[640px] overflow-auto pr-1">
                    {messages.map((m) => <BoardMessage key={m.seq} msg={m} />)}
                    {live && (
                      <div className="flex items-center gap-2 text-sm text-gray-500 px-3 py-2">
                        <Loader className="w-4 h-4 animate-spin" /> {t('teamDetails.theTeamIsWorking')}
                      </div>
                    )}
                    <div ref={boardEndRef} />
                  </div>
                </div>

                {run.result && !live && (
                  <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
                    <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-2">
                      The team's answer
                    </h3>
                    <pre className="text-sm text-gray-800 whitespace-pre-wrap max-h-96 overflow-auto">
                      {run.result}
                    </pre>
                  </div>
                )}
              </>
            )}
          </>
        )}

        {tab === 'history' && (
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
            {runs.length === 0 ? (
              <p className="p-10 text-center text-sm text-gray-500">
                {t('teamDetails.thisTeamHasNotRun')}
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                  <tr>
                    <th className="px-4 py-2">{t('teamDetails.started')}</th>
                    <th className="px-4 py-2">{t('teamDetails.request')}</th>
                    <th className="px-4 py-2">{t('teamDetails.status')}</th>
                    <th className="px-4 py-2 text-right">{t('teamDetails.rounds')}</th>
                    <th className="px-4 py-2 text-right">{t('teamDetails.cost')}</th>
                    <th className="px-4 py-2" />
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {runs.map((r) => (
                    <tr
                      key={r.team_run_id}
                      className={`hover:bg-gray-50 cursor-pointer ${
                        run?.team_run_id === r.team_run_id ? 'bg-indigo-50/60' : ''
                      }`}
                      onClick={() => { loadRun(r.team_run_id); setTab('work'); }}
                    >
                      <td className="px-4 py-2 whitespace-nowrap text-gray-600">
                        {new Date(r.started_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-2 text-gray-800 max-w-md truncate">{r.goal}</td>
                      <td className="px-4 py-2">
                        <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${STATUS_STYLES[r.status] || 'bg-gray-100 text-gray-600'}`}>
                          {r.status}
                        </span>
                        {r.stop_reason && (
                          <span className="ml-1.5 text-xs text-gray-500 italic">
                            {r.stop_reason.replace(/_/g, ' ')}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-right text-gray-600">{r.rounds_done}</td>
                      <td className="px-4 py-2 text-right text-gray-600">
                        ${(r.total_cost || 0).toFixed(4)}
                      </td>
                      <td className="px-4 py-2 text-right">
                        {r.task_id && (
                          <Link
                            to={`/tasks/${r.task_id}`} onClick={(e) => e.stopPropagation()}
                            className="text-xs text-indigo-600 hover:text-indigo-800 inline-flex items-center gap-1"
                          >
                            <ExternalLink className="w-3 h-3" /> {t('teamDetails.task')}
                          </Link>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {tab === 'settings' && (
          <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teamDetails.name')}</label>
                <input
                  value={draft.name} onChange={(e) => set({ name: e.target.value })}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teamDetails.howItIsRun')}</label>
                <select
                  value={draft.mode} onChange={(e) => set({ mode: e.target.value })}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                >
                  {modeOptions(t).map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
                <p className="text-xs text-gray-500 mt-1">{modeHelp(draft.mode, t)}</p>
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teamDetails.description')}</label>
              <textarea
                value={draft.description} onChange={(e) => set({ description: e.target.value })}
                rows={2}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">
                {t('teamDetails.charterTheSystemPromptEvery')}
              </label>
              <textarea
                value={draft.charter} onChange={(e) => set({ charter: e.target.value })}
                rows={4}
                placeholder={t('teamDetails.whatThisTeamIsWhat')}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
              />
              <p className="text-xs text-gray-500 mt-1">
                Added on top of each agent's own instructions, never in place of them.
              </p>
            </div>

            {draft.mode === 'centralized' && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">
                    <Crown className="w-3.5 h-3.5 inline mr-1 -mt-0.5" /> {t('teamDetails.teamLead')}
                  </label>
                  <select
                    value={draft.leader_agent_id || ''}
                    onChange={(e) => set({ leader_agent_id: e.target.value || null })}
                    className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                  >
                    <option value="">{t('teamDetails.selectAnAgent')}</option>
                    {agents.map((a) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">
                    {t('teamDetails.nameTheTeamCallsThe')}
                  </label>
                  <input
                    value={draft.leader_name}
                    onChange={(e) => set({ leader_name: e.target.value })}
                    placeholder={t('teamDetails.lead')}
                    className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                  />
                </div>
              </div>
            )}

            {draft.mode === 'autonomous' && (
              <div className="md:w-1/2">
                <label className="block text-xs font-semibold text-gray-600 mb-1">
                  <DoorOpen className="w-3.5 h-3.5 inline mr-1 -mt-0.5" /> {t('teamDetails.whoTakesTheRequest')}
                </label>
                <select
                  value={draft.entry_agent_id || ''}
                  onChange={(e) => set({ entry_agent_id: e.target.value || null })}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                >
                  <option value="">
                    First on the roster{draft.members[0] ? ` — ${draft.members[0].name || draft.members[0].agent_id}` : ''}
                  </option>
                  {draft.members.map((m) => (
                    <option key={m.agent_id} value={m.agent_id}>{m.name || m.agent_id}</option>
                  ))}
                </select>
                <p className="text-xs text-gray-500 mt-1">
                  There is no coordinator in this mode, so someone has to be the door.
                  This member reads the request and either does it or hands it to the
                  teammate whose manifest covers it.
                </p>
              </div>
            )}

            {/* Roster */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide">{t('teamDetails.roster')}</h3>
                <div className="flex items-center gap-2">
                  <select
                    value={addAgent} onChange={(e) => setAddAgent(e.target.value)}
                    className="text-sm border border-gray-300 rounded-lg px-2 py-1.5"
                  >
                    <option value="">{t('teamDetails.addAnAgent')}</option>
                    {agents
                      .filter((a) => !draft.members.some((m) => m.agent_id === a.id))
                      .map((a) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
                  </select>
                  <button
                    onClick={() => {
                      if (!addAgent) return;
                      const a = agents.find((x) => x.id === addAgent);
                      set({
                        members: [...draft.members, {
                          agent_id: addAgent, name: a?.name || addAgent,
                          role: '', manifest: '', goal: '', memory_horizon: 10,
                        }],
                      });
                      setAddAgent('');
                    }}
                    disabled={!addAgent}
                    className="inline-flex items-center px-2.5 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-40"
                  >
                    <Plus className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
              {draft.mode === 'autonomous' && (
                <p className="text-xs text-gray-500 mb-2">
                  Order matters here: the first member takes the request unless you
                  name another above.
                </p>
              )}
              <div className="space-y-3">
                {draft.members.length === 0 && (
                  <p className="text-sm text-gray-500 italic">
                    {t('teamDetails.noMembersYetATeam')}
                  </p>
                )}
                {draft.members.map((m, i) => (
                  <MemberCard
                    key={`${m.agent_id}-${i}`} member={m} agents={agents}
                    isLeader={draft.leader_agent_id === m.agent_id}
                    isEntry={draft.mode === 'autonomous' && m.agent_id === entryId}
                    onChange={(next) => setMember(i, next)}
                    onRemove={() => set({ members: draft.members.filter((_, j) => j !== i) })}
                  />
                ))}
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teamDetails.maxRounds')}</label>
                <input
                  type="number" min={1} max={30} value={draft.max_rounds}
                  onChange={(e) => set({ max_rounds: Number(e.target.value) })}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teamDetails.parallelTurns')}</label>
                <input
                  type="number" min={1} max={12} value={draft.max_concurrent}
                  onChange={(e) => set({ max_concurrent: Number(e.target.value) })}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teamDetails.turnTimeoutS')}</label>
                <input
                  type="number" min={30} value={draft.turn_timeout}
                  onChange={(e) => set({ turn_timeout: Number(e.target.value) })}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teamDetails.costCeiling')}</label>
                <input
                  type="number" step="0.01" min={0} value={draft.cost_ceiling ?? ''}
                  onChange={(e) => set({ cost_ceiling: e.target.value === '' ? null : Number(e.target.value) })}
                  placeholder={t('teamDetails.none')}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                />
              </div>
            </div>

            <div className="flex items-center gap-6 flex-wrap">
              <label className="inline-flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox" checked={draft.allow_direct_messages}
                  onChange={(e) => set({ allow_direct_messages: e.target.checked })}
                />
                Members may address one teammate privately (<code>{t('teamDetails.name2')}</code>)
              </label>
              <label className="inline-flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox" checked={draft.synthesize}
                  onChange={(e) => set({ synthesize: e.target.checked })}
                />
                {t('teamDetails.closeWithOneWrittenAnswer')}
              </label>
            </div>

            <div className="flex justify-between items-center">
              <button
                onClick={() => showBriefing(draft.members[0]?.agent_id)}
                className="inline-flex items-center px-3 py-2 text-xs font-semibold text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                <Eye className="w-3.5 h-3.5 mr-1.5" /> {t('teamDetails.seeWhatAMemberIs')}
              </button>
              <button
                onClick={handleSave} disabled={saving}
                className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              >
                {saving ? <Loader className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}
                Save
              </button>
            </div>

            {briefing && (
              <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-xs font-bold uppercase tracking-wide text-gray-500">
                    Team block added to {briefing.agent_id}'s system prompt
                  </h4>
                  <button onClick={() => setBriefing(null)} className="p-1 text-gray-400 hover:text-gray-600">
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
                <pre className="text-xs text-gray-700 whitespace-pre-wrap max-h-80 overflow-auto">
                  {briefing.prompt}
                </pre>
              </div>
            )}
          </div>
        )}
      </div>
        </div>

        {chat.open && teamChat && (
          <ChatColumn>
            <EntityChat {...teamChat} {...FILL_COLUMN} />
          </ChatColumn>
        )}
      </div>
    </PageContainer>
  );
}
