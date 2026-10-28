import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import {
  DollarSign, FileText, FlaskConical, MessageSquare, PanelRightClose, PanelRightOpen, Play, Settings2, Square, UsersRound,
} from 'lucide-react';
import {
  getTeam, updateTeam, startTeamRun, getTeamRuns, getTeamRun, getTeamTurns,
  getTeamMessages, stopTeamRun, resumeTeamRun, deleteTeamRun, getTeamBriefing, getAgents,
  estimateTeam, getTeamChat, clearTeamChat, stopTeamChat, teamChatUrl,
} from '../api';
import SaveAsEvalCaseDialog from '../components/evals/SaveAsEvalCaseDialog';
import InPanelNote from '../components/pageChat/InPanelNote';
import { usePageChat, usePageChatPanel } from '../components/pageChat/pageChat';
import { useWorkspace } from '../components/workspace';
import { useChannel, useLiveRefetch, useStream } from '../components/stream';
import { errorDetail, useToast } from '../components/toast';
import { AppBar } from '../components/PageLayout';
import { PaneTab } from '../components/PageTabs';
import PageLoader from '../components/PageLoader';
import FlowArtifacts from '../components/flow/FlowArtifacts';
import { conversationArtifacts } from '../components/chat/artifactOps';
import { conversationViews } from '../components/chat/turnViews';
import { useMessagesWithTrails } from '../components/chat/runTrail';
import { FileOpenContext } from '../components/chat/fileOpenContext';
import { useChatViewMode } from '../components/chat/viewMode';
import { attachmentsPayload, referencesPayload } from '../components/chat/composerPayload';
import { modeLabel } from '../components/teamModes';
import TeamRunHistory from '../components/teams/TeamRunHistory';
import TeamRunChat from '../components/teams/TeamRunChat';
import TeamRunSide from '../components/teams/TeamRunSide';
import TeamSettingsForm from '../components/teams/TeamSettingsForm';
import TeamCreatorChat from '../components/teams/TeamCreatorChat';
import { boardChatMessages, isTurnLive } from '../components/teams/teamBoard';
import { useLiveTrails } from '../components/teams/liveTrails';
import { useLiveTurns } from '../components/runs/liveTurns';
import { isRunLive } from '../components/teams/teamFormat';
import { useI18n } from '../i18n';

/**
 * One team (/teams/:teamId), laid out as the flow and loop pages are.
 *
 * Left, New run over the team's runs. Centre, the run on screen as a chat
 * (the request, every member's reply under its name, the closing answer) with
 * the request box under it, or the files and views its turns produced
 * (Artifacts). Right, the participants with their part in the run and the run
 * log, every member turn with its steps. Settings switches the whole page to
 * the team's settings (the roster, the manifests, the limits) and back; the
 * build chat opens beside either.
 *
 * `?run=<team_run_id>` opens one run in particular and is written back when a
 * run is picked here, so the address stays shareable; `?tab=settings` is the
 * settings page.
 */
const SIDE_COLUMN = 'hidden w-[220px] shrink-0 lg:flex xl:w-[260px] 2xl:w-[300px] min-[1800px]:w-[340px]';
const NO_TURNS = [];

export default function TeamDetails() {
  const { t } = useI18n();
  const toast = useToast();
  const { teamId } = useParams();
  const { selectedWorkspace } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const settingsOpen = params.get('tab') === 'settings';
  const [viewMode, setViewMode] = useChatViewMode();

  const [team, setTeam] = useState(null);
  const [draft, setDraft] = useState(null);
  const [agents, setAgents] = useState([]);
  const [runs, setRuns] = useState([]);
  const [run, setRun] = useState(null);
  const [messages, setMessages] = useState([]);
  const [turns, setTurns] = useState(NO_TURNS);
  const [goal, setGoal] = useState('');
  const [briefing, setBriefing] = useState(null);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [saving, setSaving] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  // How many model calls a run may take at most (routes/teams estimate).
  const [estimate, setEstimate] = useState(null);
  // "To eval case": a finished run seeds an eval dataset (components/evals/).
  const [caseDialogOpen, setCaseDialogOpen] = useState(false);
  const [centerTab, setCenterTab] = useState('chat'); // chat | artifacts
  const [artifactPick, setArtifactPick] = useState(null);

  const runId = run?.team_run_id || null;
  const live = isRunLive(run?.status);

  // The address: the run on screen and whether the settings page is open.
  const writeAddress = useCallback((next) => {
    setParams((prev) => {
      const out = {};
      const tab = next.tab !== undefined ? next.tab : prev.get('tab');
      const rid = next.run !== undefined ? next.run : prev.get('run');
      if (tab) out.tab = tab;
      if (rid) out.run = rid;
      return out;
    }, { replace: true });
  }, [setParams]);
  const toggleSettings = () => writeAddress({ tab: settingsOpen ? null : 'settings' });

  useEffect(() => {
    getAgents(selectedWorkspace)
      .then(({ data }) => setAgents(data.agents || data || []))
      .catch(() => { /* the pickers degrade to ids */ });
  }, [selectedWorkspace]);

  const loadTurns = useCallback((id) => {
    if (!id) return Promise.resolve();
    return getTeamTurns(id)
      .then(({ data }) => setTurns(data.turns || NO_TURNS))
      .catch(() => {});
  }, []);

  // Loading a run and writing it into the address are kept apart: the
  // setter from useSearchParams changes identity with every address change,
  // and a loader that closed over it would restart the page's first load.
  const loadRun = useCallback(async (id) => {
    try {
      const { data } = await getTeamRun(id);
      setRun(data);
      setMessages(data.messages || []);
      setTurns(NO_TURNS);
      loadTurns(id);
    } catch {
      toast.error(t('teamDetails.loadRunFailed'));
    }
  }, [loadTurns, toast, t]);

  // A promise chain, not an async function: the React Compiler lint treats an
  // async function called from an effect as a synchronous setState.
  const loadRuns = useCallback(() => getTeamRuns(teamId)
    .then(({ data }) => {
      setRuns(data.runs || []);
      return data.runs || [];
    })
    .catch(() => []), [teamId]);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await getTeam(teamId);
        setTeam(data); setDraft(data);
        const history = await loadRuns();
        const wanted = params.get('run');
        const first = wanted && history.some((r) => r.team_run_id === wanted)
          ? wanted : history[0]?.team_run_id;
        if (first) loadRun(first);
      } catch {
        toast.error(t('teamDetails.loadTeamFailed'));
      }
    })();
    // Once per team: the loaders are stable, and the run in the address is
    // read here only to choose where to start.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [teamId]);

  const openRun = (id) => {
    writeAddress({ run: id, tab: null });
    setCenterTab('chat');
    loadRun(id);
  };

  // A fresh entry for the history: the chat empties, the box waits for the
  // request and the address drops the run it was showing.
  const startNewRun = () => {
    setRun(null);
    setMessages([]);
    setTurns(NO_TURNS);
    setGoal('');
    setCenterTab('chat');
    writeAddress({ run: null, tab: null });
  };

  // Deleting a History entry: the run with its board. The run on screen,
  // when it is that one, gives way to a new empty run.
  const handleDeleteRun = async (r) => {
    if (!window.confirm(t('teamDetails.confirmDeleteRun'))) return;
    try {
      await deleteTeamRun(r.team_run_id);
    } catch (e) {
      toast.error(t('teamDetails.deleteRunFailed'), errorDetail(e));
      return;
    }
    setRuns((prev) => prev.filter((x) => x.team_run_id !== r.team_run_id));
    if (r.team_run_id === runId) startNewRun();
  };

  // The board and the turns arrive live; the catch-up below is the fallback.
  useChannel(runId ? `team:${runId}` : null, (ev) => {
    // Channel events arrive flat (common/session_broker.py); `data` is the
    // older wrapped shape.
    const d = ev?.type ? ev : ev?.data;
    if (!d) return;
    if (d.type === 'message') {
      setMessages((prev) => (prev.some((m) => m.seq === d.seq) ? prev : [...prev, d]));
      loadTurns(runId);
    } else if (d.type === 'turn_start' || d.type === 'turn_done') {
      loadTurns(runId);
    } else if (d.type === 'round_start') {
      setRun((prev) => (prev ? { ...prev, status: prev.status === 'pending' ? 'running' : prev.status } : prev));
    } else if (d.type === 'team_done' || d.type === 'stopping') {
      setRun((prev) => ({ ...prev, ...d }));
      if (d.type === 'team_done') { loadRuns(); loadTurns(runId); }
    }
  });

  const messagesRef = useRef([]);
  useEffect(() => { messagesRef.current = messages; }, [messages]);

  // Board catch-up on `team_runs.changed` (teams/store.py `_notify`), and
  // every few seconds while the team works: a run carried by its own process
  // publishes on a channel that may not reach this page.
  const catchUpBoard = useCallback(async () => {
    if (!runId) return;
    try {
      const seen = messagesRef.current;
      const since = seen.length ? seen[seen.length - 1].seq : 0;
      const { data } = await getTeamMessages(runId, since);
      if (data.messages?.length) {
        setMessages((prev) => {
          const known = new Set(prev.map((m) => m.seq));
          return [...prev, ...data.messages.filter((m) => !known.has(m.seq))];
        });
      }
      setRun((prev) => (prev?.team_run_id === runId ? { ...prev, ...data, messages: undefined } : prev));
      loadTurns(runId);
      if (!isRunLive(data.status)) loadRuns();
    } catch { /* transient */ }
  }, [runId, loadRuns, loadTurns]);

  useLiveRefetch(catchUpBoard, {
    type: 'team_runs.changed',
    enabled: Boolean(runId) && live,
    fallbackMs: 30000,
  });
  useEffect(() => {
    if (!live || !runId) return undefined;
    const timer = setInterval(catchUpBoard, 4000);
    return () => clearInterval(timer);
  }, [live, runId, catchUpBoard]);

  // The history lists each run with its status: when the run on screen
  // changes status (it ends, above all), the list is read again.
  const runStatus = run?.status;
  useEffect(() => {
    if (runStatus) loadRuns();
  }, [runStatus, loadRuns]);

  const { onRefetch } = useStream();
  useEffect(() => onRefetch(() => { loadRuns(); catchUpBoard(); }),
    [onRefetch, loadRuns, catchUpBoard]);

  // The board as a chat, with the members still working as replies being
  // written; the steps, files and views of every reply come from its run's
  // trail, for the Build view and the Artifacts tab alike. A turn in flight
  // has its steps read as it goes (liveTrails.js), the same ones for its
  // reply being written here and for its entry in the run log.
  const posted = useMemo(() => new Set(messages.map((m) => m.run_id).filter(Boolean)), [messages]);
  const liveTurnIds = useMemo(
    () => (live ? turns.filter(isTurnLive).map((tn) => String(tn.run_id)) : []),
    [live, turns],
  );
  // What each member at work does streams on the team's channel (teams/
  // runner.py): its reply as it is written and its steps. The trail read
  // every few seconds stands in until the stream has said anything.
  const streamed = useLiveTurns(live && runId ? [`team:${runId}`] : [], 'team');
  const polledTrails = useLiveTrails(liveTurnIds);
  const liveTrails = useMemo(() => {
    const out = { ...polledTrails };
    for (const [rid, { turn }] of Object.entries(streamed)) {
      out[rid] = { ...out[rid], timeline: turn.timeline, files: turn.files || out[rid]?.files || [], turn };
    }
    return out;
  }, [polledTrails, streamed]);
  const working = useMemo(
    () => (live ? turns.filter((tn) => isTurnLive(tn) && !posted.has(tn.run_id)) : NO_TURNS),
    [live, turns, posted],
  );
  const chatMessages = useMemo(
    () => (run ? boardChatMessages({ run, messages, working, trails: liveTrails, t }) : []),
    [run, messages, working, liveTrails, t],
  );
  const withTrails = useMessagesWithTrails(chatMessages, true);
  const shownArtifacts = useMemo(() => ({
    artifacts: conversationArtifacts(withTrails),
    views: conversationViews(withTrails).filter((v) => v.view_kind !== 'code'),
  }), [withTrails]);
  const artifactCount = Object.keys(shownArtifacts.artifacts).length + shownArtifacts.views.length;
  const showFileInArtifacts = useCallback((path) => {
    setArtifactPick(`file:${path}`);
    setCenterTab('artifacts');
  }, []);

  // A run is only a run: it makes no task. What the box carried (files, hub
  // records) goes with it; an attached task is the one the team works on.
  // Says whether the run started, so the box can let go of its attachments.
  const handleStart = async ({ attachments = [], references = [] } = {}) => {
    if (!team) return false;
    setStarting(true);
    try {
      const { data } = await startTeamRun(team.team_id, {
        goal,
        workspace: selectedWorkspace,
        attachments: attachmentsPayload(attachments, selectedWorkspace),
        references: referencesPayload(references),
      });
      if (!data.team_run_id) return false;
      setMessages([]);
      setTurns(NO_TURNS);
      setRun(data);
      setGoal('');
      setCenterTab('chat');
      writeAddress({ run: data.team_run_id, tab: null });
      loadRuns();
      return true;
    } catch (e) {
      toast.error(t('teamDetails.startFailed'), errorDetail(e));
      return false;
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    if (!runId) return;
    setStopping(true);
    // Optimistic, because the request returns as soon as the turns in flight
    // have been interrupted and the button should not look inert until then.
    setRun((prev) => ({ ...prev, status: 'stopping' }));
    try {
      await stopTeamRun(runId);
    } catch {
      loadRun(runId);
    } finally {
      setStopping(false);
    }
  };

  const handleResume = async () => {
    if (!runId) return;
    setResuming(true);
    try {
      const { data } = await resumeTeamRun(runId);
      setRun((prev) => ({ ...prev, ...data }));
      loadRuns();
    } catch (e) {
      toast.error(t('teamDetails.startFailed'), errorDetail(e));
    } finally {
      setResuming(false);
    }
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const { data } = await updateTeam(draft.team_id, draft);
      setTeam(data); setDraft(data);
      toast.success(t('teamDetails.saved'));
    } catch (e) {
      toast.error(t('teamDetails.saveFailed'), errorDetail(e));
    } finally {
      setSaving(false);
    }
  };

  const showBriefing = async (agentId) => {
    try {
      const { data } = await getTeamBriefing(team.team_id, agentId);
      setBriefing(data);
    } catch (e) {
      toast.error(t('teamDetails.briefingFailed'), errorDetail(e));
    }
  };

  const applyTeamFromChat = useCallback((updated) => {
    setTeam(updated);
    setDraft(updated);
  }, []);

  const handleEstimate = async () => {
    try {
      const { data } = await estimateTeam(teamId);
      setEstimate(data);
    } catch (e) {
      toast.error(t('teamDetails.estimateFailed', { defaultValue: 'Could not estimate the run' }), errorDetail(e));
    }
  };

  // The Team Creator's conversation, drawn either in the column here or in the
  // floating page-chat panel: one descriptor, so it is the same conversation
  // either way, and the column says so while the panel holds it.
  const teamChat = useTeamChatDescriptor(team?.team_id, applyTeamFromChat);
  usePageChat(teamChat);
  const { inlineSuppressed: panelHoldsChat } = usePageChatPanel();

  if (!team || !draft) {
    return <div className="h-full bg-white"><PageLoader label={t('teamDetails.loadingTheTeam')} /></div>;
  }

  const entryName = team.entry_display_name || team.members[0]?.display_name;
  const subtitle = [
    modeLabel(team.mode, t),
    team.mode === 'centralized' && team.leader_display_name ? t('teamDetails.ledBy', { name: team.leader_display_name }) : '',
    team.mode === 'autonomous' && entryName ? t('teamDetails.takesRequest', { name: entryName }) : '',
    t('teamDetails.membersCount', { count: team.members.length }),
    t('teamDetails.upToRounds', { count: team.max_rounds }),
  ].filter(Boolean).join(' · ');

  const creator = chatOpen && (panelHoldsChat ? (
    <div className="absolute inset-y-0 right-0 z-30 flex w-full flex-col overflow-hidden border-l border-slate-200 bg-white p-4 shadow-2xl sm:w-[26rem] 2xl:static 2xl:z-auto 2xl:w-[34.5rem] 2xl:shrink-0 2xl:shadow-none">
      <InPanelNote />
    </div>
  ) : (
    <TeamCreatorChat
      teamId={team.team_id}
      onTeamChanged={applyTeamFromChat}
      onClose={() => setChatOpen(false)}
      className="absolute inset-y-0 right-0 z-30 w-full border-l border-slate-200 shadow-2xl sm:w-[26rem] 2xl:static 2xl:z-auto 2xl:w-[34.5rem] 2xl:shrink-0 2xl:shadow-none"
    />
  ));

  return (
    <FileOpenContext.Provider value={showFileInArtifacts}>
    <div className="h-full flex flex-col overflow-hidden">
      <AppBar
        icon={UsersRound}
        title={team.name}
        titleWrap
        subtitle={subtitle}
        backTo="/teams"
        backLabel={t('teamDetails.allTeams')}
        center={settingsOpen ? null : (
          <div className="inline-flex items-center gap-1 rounded-lg bg-gray-100 p-0.5">
            <PaneTab active={centerTab === 'chat'} onClick={() => setCenterTab('chat')} icon={MessageSquare} label={t('teamDetails.tabRun')} />
            <PaneTab
              active={centerTab === 'artifacts'} onClick={() => setCenterTab('artifacts')}
              icon={FileText} label={t('chat.artifacts')} badge={artifactCount}
            />
          </div>
        )}
        actions={<>
          {/* Run is in the chat's box; Stop stays up here too, so a run can
              be stopped from the settings. */}
          {!settingsOpen && !live && (
            <button
              onClick={handleEstimate}
              title={t('teamDetails.estimate')}
              className="flex items-center rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-sm font-semibold text-indigo-700 shadow-sm transition hover:bg-indigo-100"
              data-testid="team-estimate"
            >
              <DollarSign className="mr-1.5 h-4 w-4" /> <span className="hidden sm:inline">{t('teamDetails.estimate')}</span>
            </button>
          )}
          {run?.team_run_id && !live && (
            <button
              onClick={() => setCaseDialogOpen(true)}
              title={t('messageDetails.toEvalCaseHint')}
              className="flex items-center rounded-lg border border-indigo-200 bg-indigo-50 px-3 py-1.5 text-sm font-semibold text-indigo-700 shadow-sm transition hover:bg-indigo-100"
              data-testid="team-to-eval-case"
            >
              <FlaskConical className="mr-1.5 h-4 w-4" /> <span className="hidden sm:inline">{t('messageDetails.toEvalCase')}</span>
            </button>
          )}
          {live && (
            <button
              onClick={handleStop}
              disabled={stopping || run?.status === 'stopping'}
              className="flex items-center rounded-lg border border-rose-200 bg-rose-50 px-3 py-1.5 text-sm font-semibold text-rose-600 shadow-sm transition hover:bg-rose-100 disabled:opacity-50"
            >
              <Square className="mr-1.5 h-4 w-4" /> {t('common.stop')}
            </button>
          )}
          <button
            onClick={toggleSettings} aria-pressed={settingsOpen}
            className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-semibold shadow-sm transition ${
              settingsOpen ? 'border-indigo-200 bg-indigo-50 text-indigo-700' : 'border-gray-200 bg-gray-50 text-gray-700 hover:bg-gray-100'}`}
            data-testid="team-settings-toggle"
          >
            {settingsOpen ? <Play className="h-4 w-4 text-indigo-500" /> : <Settings2 className="h-4 w-4 text-cyan-500" />}
            <span className="hidden sm:inline">{settingsOpen ? t('teamDetails.backToRuns') : t('teamDetails.tabSettings')}</span>
          </button>
          <button
            onClick={() => setChatOpen((v) => !v)} aria-pressed={chatOpen}
            title={chatOpen ? t('teamDetails.hideBuildChat') : t('teamDetails.showBuildChat')}
            className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm font-semibold shadow-sm transition ${
              chatOpen ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-gray-200 bg-gray-50 text-emerald-600 hover:bg-emerald-50'}`}
            data-testid="team-creator-toggle"
          >
            {chatOpen ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
            <span className="hidden sm:inline">{t('teamDetails.buildChat')}</span>
          </button>
        </>}
      />

      <section className="relative flex min-h-0 flex-1 overflow-hidden bg-white">
        {settingsOpen ? (
          <div className="min-h-0 min-w-0 flex-1 overflow-y-auto bg-slate-50 p-4 sm:p-6">
            <TeamSettingsForm
              draft={draft} agents={agents}
              onChange={(patch) => setDraft((d) => ({ ...d, ...patch }))}
              onSave={handleSave} saving={saving}
              briefing={briefing} onShowBriefing={showBriefing} onCloseBriefing={() => setBriefing(null)}
            />
          </div>
        ) : (
          <>
            {/* Left column: New run over the team's runs. */}
            <TeamRunHistory
              runs={runs}
              selectedId={runId}
              onSelect={openRun}
              onNew={startNewRun}
              onDelete={handleDeleteRun}
              newDisabled={live}
              className={`${SIDE_COLUMN} border-r border-slate-200`}
            />

            {/* Centre column: the run as a chat, or its artifacts. */}
            <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
              {estimate && centerTab === 'chat' && (
                <div className="mx-4 mt-3 flex items-start gap-2 rounded-lg border border-indigo-100 bg-indigo-50 p-3 text-xs text-indigo-800" data-testid="team-estimate-note">
                  <span className="min-w-0 flex-1">
                    <span className="font-bold">{t('teamDetails.upToLlmCalls', { count: estimate.llm_calls_upper_bound })}</span>
                    {' — '}{t('teamDetails.membersRounds', { members: estimate.members, rounds: estimate.max_rounds })}{' '}
                    <span className="text-indigo-600">
                      {t(`teamDetails.${estimate.note_key}`, { defaultValue: estimate.note })}
                    </span>
                  </span>
                  <button type="button" onClick={() => setEstimate(null)} className="text-indigo-400 hover:text-indigo-700" aria-label={t('common.close')}>×</button>
                </div>
              )}
              {/* Always mounted, so what the box holds survives a tab switch. */}
              <div className={`min-h-0 flex-1 overflow-hidden ${centerTab === 'chat' ? '' : 'hidden'}`}>
                <TeamRunChat
                  team={team} run={run} messages={withTrails} live={live}
                  workspace={selectedWorkspace} active={centerTab === 'chat'}
                  viewMode={viewMode} onViewModeChange={setViewMode}
                  goal={goal} onGoalChange={setGoal}
                  onRun={handleStart} starting={starting}
                  onStop={handleStop} stopping={stopping}
                  onResume={handleResume} resuming={resuming}
                />
              </div>
              {centerTab === 'artifacts' && (
                <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
                  <FlowArtifacts
                    artifacts={shownArtifacts.artifacts}
                    views={shownArtifacts.views}
                    workspace={run?.workspace || selectedWorkspace}
                    picked={artifactPick}
                    onPick={setArtifactPick}
                  />
                </div>
              )}
            </div>

            {/* Right column: the participants and the run log. */}
            <TeamRunSide
              team={team} run={run} turns={turns} messages={messages} liveTrails={liveTrails}
              className={SIDE_COLUMN}
            />
          </>
        )}
        {creator}
      </section>
      {caseDialogOpen && run?.team_run_id && (
        <SaveAsEvalCaseDialog
          runId={run.team_run_id}
          workspace={run.workspace || selectedWorkspace}
          onClose={() => setCaseDialogOpen(false)}
        />
      )}
    </div>
    </FileOpenContext.Provider>
  );
}

// The team's build chat as the page-chat panel's descriptor (components/pageChat):
// the same callbacks TeamCreatorChat hands EntityChat, so both draw one conversation.
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
