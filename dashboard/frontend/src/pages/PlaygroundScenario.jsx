import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Link, useParams, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Gamepad2, Play, Square, Trash2, Loader, Settings2, Users, X, Save,
  AlertTriangle, DollarSign, ChevronLeft, ChevronRight, Radio, Zap,
  Send, Gauge, Lock, ListTree, MessagesSquare, History, RefreshCw, BookOpen,
  BookText, Map,
} from 'lucide-react';
import {
  getSimEnvironments, getScenario, updateScenario, deleteScenario, estimateScenario,
  startSimulation, getSimRuns, getSimRun, getSimTicks, stopSimulation, getAgents,
  getModelsCatalog, triggerSimAgent,
  getScenarioChat, clearScenarioChat, stopScenarioChat, scenarioChatUrl,
} from '../api';
import EntityChat from '../components/EntityChat';
import InPanelNote from '../components/pageChat/InPanelNote';
import { usePageChat, usePageChatPanel } from '../components/pageChat/pageChat';
import { useWorkspace } from '../components/workspace';
import { useToast } from '../components/toast';
import { useChannel } from '../components/stream';
import WorldView from './playground/renderers';
import { Combo } from './playground/combo';
import { ScenarioTranscript, EventFeed } from './playground/transcript';
import StoryPane from './playground/story';
import { eventLines } from './playground/events';
import { RunHistory, RunParams } from './playground/history';
import NarrativePanel from './playground/narrative';
import StageCanvas from './playground/stage';
import { runConfig } from './playground/config';
import { isLiveStatus } from './playground/status';
import { genericGrants } from './playground/world-spec';

import { PageContainer, PageHeader } from '../components/PageLayout';
import InlineEdit from '../components/InlineEdit';
import { useI18n, statusLabel } from '../i18n';
/**
 * One scenario — setting it up and watching it run.
 *
 * Setup is a separate mode from watching. Watching is three surfaces, which is
 * exactly why this cannot be a Studio tab:
 *   1. the run itself, in the wide column, behind one switch: the transcript
 *      (what each agent was woken for, reasoned and submitted, with a link to
 *      that turn's own run log), the world's event log, the whole run compiled
 *      into one text, the tick drawn as the place it happened in, and the
 *      prose that place was written from. Five readings of one scenario, so
 *      they share the column rather than competing for the eye side by side;
 *      the transcript is what you came to read, so it is the one that opens.
 *   2. the world view and its parameters, on the right with the meters: state
 *      you glance at rather than read
 *   3. transport (tick scrubber) + a live cost meter
 *
 * The main/right split follows what changes: the pane that moves every tick
 * gets the width, the readouts get the sidebar.
 *
 * The scenario list lives on its own page (Playground.jsx): a run is a
 * full-attention surface, and its URL has to be linkable.
 */

/** One of the two readings of a run. A switch rather than two panes, so the
    column belongs to whichever one you asked for — and set at the weight of
    the panel headings it replaced, since that is what it is: the title of what
    you are looking at, which happens to be clickable. */
function PaneTab({ active, onClick, icon: Icon, label, badge = 0 }) {
  return (
    <button
      type="button" onClick={onClick}
      className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-bold uppercase tracking-wide transition-colors ${
        active ? 'bg-white text-gray-800 shadow-sm' : 'text-gray-500 hover:text-gray-700'
      }`}
    >
      <Icon className={`w-3.5 h-3.5 ${active ? 'text-indigo-500' : 'text-gray-400'}`} />
      {label}
      {badge > 0 && (
        <span className="px-1.5 rounded-full bg-indigo-50 text-indigo-600 text-[10px] font-bold">
          {badge > 99 ? '99+' : badge}
        </span>
      )}
    </button>
  );
}


/** The sidebar card's header: the switch *is* the title, in two halves across
    the card's full width. A pill above the card plus a heading inside it named
    the same panel twice and left the card looking untitled — this is one
    header that happens to be clickable, which is what a tab is. */
function ColumnTab({ active, onClick, icon: Icon, label }) {
  return (
    <button
      type="button" onClick={onClick}
      className={`flex items-center justify-center gap-1.5 px-3 py-3 text-xs font-bold uppercase tracking-wide border-b-2 transition-colors ${
        active
          ? 'bg-white border-indigo-500 text-gray-800'
          : 'bg-gray-50 border-transparent text-gray-400 hover:text-gray-600 hover:bg-gray-100'
      }`}
    >
      <Icon className={`w-4 h-4 shrink-0 ${active ? 'text-indigo-500' : 'text-gray-400'}`} />
      <span className="truncate">{label}</span>
    </button>
  );
}


// The run's feed owns whatever height is left under the transport bar once the
// layout is a column (xl and up); narrower than that the page is an ordinary
// scroll, so the feed falls back to a bounded box.
const FEED_HEIGHT = 'max-h-[calc(100vh-24rem)] min-h-[18rem] xl:flex-1 xl:min-h-0 xl:max-h-none';

// The map and the narrative editor are not feeds: they do not scroll a list,
// they fill what they are given. A taller floor than a feed's, because a map
// scaled into eighteen rems is a diagram of nothing.
const PANE_HEIGHT = 'min-h-[30rem] xl:flex-1 xl:min-h-0';

// Setting up and watching stand the same sidebar beside their main column, so
// the template lives here rather than being written out at both call sites —
// where it had already drifted by 2rem, which read as the page resizing itself
// when you switched tabs.
const TWO_COLUMNS = 'grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_26rem] gap-6';

const STATUS_STYLES = {
  starting: 'bg-blue-100 text-blue-700',
  running: 'bg-blue-100 text-blue-700',
  stopping: 'bg-amber-100 text-amber-700',
  completed: 'bg-green-100 text-green-700',
  stopped: 'bg-amber-100 text-amber-700',
  failed: 'bg-red-100 text-red-700',
};

export default function PlaygroundScenario() {
  const { t } = useI18n();
  const { scenarioId } = useParams();
  const navigate = useNavigate();
  // Which run is on screen is the URL's to say: ?run= pins one, and with no
  // ?run= it is the newest. Every way of switching runs — the picker, the
  // history tab, a link from the history page — goes through the URL, so a run
  // is linkable and a reload comes back to the one you were reading.
  const [searchParams] = useSearchParams();
  const runParam = searchParams.get('run');
  const { selectedWorkspace } = useWorkspace();
  const [environments, setEnvironments] = useState([]);
  const [agents, setAgents] = useState([]);
  const [models, setModels] = useState([]);
  const [scenario, setScenario] = useState(null);
  const [loading, setLoading] = useState(true);
  const [missing, setMissing] = useState(false);
  const [mode, setMode] = useState('watch');       // 'setup' | 'watch' | 'history'
  // Which of the two readings of the same run is on screen. They share one
  // column because they are two views of the run, not two things to watch at
  // once — the world's log next to the dialogue only pulled the eye off it.
  // Five readings of one scenario, one column: the dialogue, the world's log,
  // the run compiled into one text, the same tick drawn as a place, and the
  // prose the place was written from.
  // 'transcript' | 'events' | 'story' | 'stage' | 'narrative'
  const [pane, setPane] = useState('transcript');
  // The sidebar carries two things now: what the scenario *is* (the readouts)
  // and a chat that can change it. Tabs rather than stacking, because both want
  // the full height of the column and only one of them is ever being read.
  const [sideTab, setSideTab] = useState('params');  // 'params' | 'chat'
  const [eventsSeen, setEventsSeen] = useState(0);
  const [runs, setRuns] = useState([]);
  const [run, setRun] = useState(null);
  const [ticks, setTicks] = useState([]);
  const [cursor, setCursor] = useState(0);         // index into ticks
  const [following, setFollowing] = useState(true);
  // Decisions that arrived on the stream before their tick was written. A tick
  // is as slow as its slowest agent, and without these the page looks frozen
  // while agents are visibly finishing one by one.
  const [inFlight, setInFlight] = useState([]);
  // Agents that have been woken and are still thinking, with whatever the
  // model has written so far. A tick is as slow as its slowest agent, so this
  // is most of what there is to watch while one is running.
  const [activity, setActivity] = useState([]);
  const [waitingForTrigger, setWaitingForTrigger] = useState(false);
  const [starting, setStarting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [estimate, setEstimate] = useState(null);
  const [message, setMessage] = useState('');
  const pollRef = useRef(null);
  // The build chat's send(), handed over by whichever EntityChat is mounted —
  // the sidebar tab or the floating panel. It is what lets a button on the page
  // ask the builder for something instead of making the user type it.
  const chatSendRef = useRef(null);
  // A request made while no chat was on screen. Held until one mounts and
  // registers, then sent: opening the chat and sending are one click.
  const pendingAskRef = useRef(null);
  // Stable across renders on purpose: EntityChat registers in an effect keyed
  // on it, and a fresh function each render would re-register continuously.
  const registerChatSend = useCallback((send) => {
    chatSendRef.current = send;
    if (send && pendingAskRef.current) {
      const text = pendingAskRef.current;
      pendingAskRef.current = null;
      send(text);
    }
  }, []);
  // The run the page has actually loaded — see the URL-follows effect below.
  const shownRunRef = useRef(null);

  useEffect(() => {
    (async () => {
      try {
        // The environment catalogue is workspace-aware: a scenario cast in a
        // world this workspace authored has to find that world here, or its
        // own parameters and action list come up blank.
        const [e, a] = await Promise.all([
          getSimEnvironments(selectedWorkspace), getAgents(selectedWorkspace),
        ]);
        setEnvironments(e.data.environments || []);
        setAgents(a.data.agents || a.data || []);
      } catch { /* catalogs are optional */ }
      try {
        // Only models the Models page has enabled are offerable — the picker in
        // the header is built from the same list, and pricing keys on it.
        const { data } = await getModelsCatalog();
        const providers = data.providers || {};
        setModels(Object.entries(providers).flatMap(([provider, entry]) => (
          (entry?.models || [])
            .filter((m) => m.enabled)
            .map((m) => ({ provider, model: m.id }))
        )));
      } catch { /* an empty catalogue just means nothing to offer */ }
    })();
  }, [selectedWorkspace]);

  const loadRun = useCallback(async (simRunId) => {
    try {
      const { data } = await getSimTicks(simRunId, -1);
      const { data: meta } = await getSimRun(simRunId);
      setRun({ ...meta, ...data });
      setTicks(data.ticks || []);
      setInFlight([]);
      setActivity([]);
      // A different run is a different world log; nothing in it has been read.
      setEventsSeen(0);
      setCursor(Math.max(0, (data.ticks || []).length - 1));
      setFollowing(isLiveStatus(data.status));
    } catch {
      setMessage(t('playground.loadRunFailed'));
    }
  }, [t]);

  /** Switch the page to a run. The URL is the switch — the effect below reads
      it — so the same click works from the picker, the history tab and a link. */
  const openRun = useCallback((simRunId) => {
    if (!simRunId) return;
    navigate(`/playground/${scenarioId}?run=${simRunId}`);
  }, [navigate, scenarioId]);

  /** The run history, refetched — after starting a run, and whenever one ends. */
  const refreshRuns = useCallback(async () => {
    try {
      const { data } = await getSimRuns(scenarioId);
      setRuns(data.runs || []);
    } catch { /* the list we have is still the list we had */ }
  }, [scenarioId]);

  // Reloading on scenarioId keeps the page honest when navigated to directly or
  // when the id changes underneath us.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setMissing(false);
    setMessage('');
    setEstimate(null);
    setRun(null);
    setTicks([]);
    setInFlight([]);
    setActivity([]);
    setWaitingForTrigger(false);
    shownRunRef.current = null;
    (async () => {
      try {
        const [{ data: sc }, { data: hist }] = await Promise.all([
          getScenario(scenarioId), getSimRuns(scenarioId),
        ]);
        if (cancelled) return;
        setScenario(sc);
        setRuns(hist.runs || []);
        // Watching is only worth opening on when there is something to
        // watch: a scenario with no runs behind it has nothing on the watch
        // tab but an empty transcript, and the next thing you want is the
        // form — so a fresh scenario opens on its parameters.
        setMode(sc.roles?.length && (hist.runs || []).length ? 'watch' : 'setup');
      } catch {
        if (!cancelled) setMissing(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [scenarioId]);

  // The run on screen follows the URL. Held in a ref as well as in state so
  // that loading one cannot retrigger this effect: the fetch is what sets the
  // state it would otherwise be compared against.
  useEffect(() => {
    const target = runParam || runs[0]?.sim_run_id;
    if (!target || target === shownRunRef.current) return;
    shownRunRef.current = target;
    loadRun(target);
  }, [runParam, runs, loadRun]);

  // Live ticks arrive on the sim channel; polling is the fallback so a dropped
  // stream degrades to a slower update rather than a frozen page.
  useChannel(run?.sim_run_id ? `sim:${run.sim_run_id}` : null, (ev) => {
    const data = ev?.data;
    if (data?.type === 'tick') {
      setTicks((prev) => {
        if (prev.some((t) => t.tick === data.tick)) return prev;
        return [...prev, data].sort((a, b) => a.tick - b.tick);
      });
      // The meter in the transport bar counts ticks and money, and both are
      // the run's numbers rather than the tick's — they ride along with the
      // tick so the bar keeps up with the transcript instead of waiting for
      // the next poll.
      setRun((prev) => (prev ? {
        ...prev,
        ticks_done: Math.max(prev.ticks_done || 0, data.ticks_done ?? data.tick ?? 0),
        total_cost: data.total_cost ?? prev.total_cost,
      } : prev));
      // The tick record supersedes anything we showed early for it.
      setInFlight((prev) => prev.filter((d) => d.tick > data.tick));
      setActivity((prev) => prev.filter((a) => a.tick > data.tick));
      setWaitingForTrigger(false);
    } else if (data?.type === 'agent_start') {
      // A woken agent takes its place in the transcript before it has thought
      // a word — carrying the mail it was woken to read.
      setActivity((prev) => [
        ...prev.filter((a) => !(a.tick === data.tick && a.agent === data.agent)),
        {
          tick: data.tick, agent: data.agent,
          triggers: data.triggers || [], messages: data.messages || [],
          reasoning: '', action: '',
        },
      ]);
      setWaitingForTrigger(false);
    } else if (data?.type === 'agent_stream') {
      // The same bubble, filling in. Only ever an update: a stray delta after
      // the decision landed must not resurrect a finished turn.
      setActivity((prev) => prev.map((a) => (
        a.tick === data.tick && a.agent === data.agent
          ? { ...a, reasoning: data.reasoning || '', action: data.action || '' }
          : a
      )));
    } else if (data?.type === 'decision') {
      setInFlight((prev) => [
        ...prev.filter((d) => !(d.tick === data.tick && d.agent === data.agent)),
        data,
      ]);
      // The finished turn replaces the bubble that was standing in for it.
      setActivity((prev) => prev.filter(
        (a) => !(a.tick === data.tick && a.agent === data.agent),
      ));
    } else if (data?.type === 'idle') {
      setWaitingForTrigger(true);
    } else if (data?.type === 'status') {
      // The first tick promotes a starting run to running. Ignored once the
      // run is stopping: a stop that landed mid-tick is the newer truth.
      setRun((prev) => (prev?.status === 'stopping'
        ? prev : { ...prev, status: data.status }));
    } else if (data?.type === 'stopping') {
      setRun((prev) => ({ ...prev, status: 'stopping' }));
    } else if (data?.type === 'done') {
      setRun((prev) => ({ ...prev, ...data }));
      setInFlight([]);
      setActivity([]);
      setWaitingForTrigger(false);
      setFollowing(false);
      // How it ended is what the history row says, so the list is refetched
      // rather than left showing a run that is still "running".
      refreshRuns();
    }
  });

  // Read by the poll below, which must not be torn down and restarted every
  // time a tick lands: on a world that ticks faster than the interval, an
  // effect that depends on `ticks` never gets to fire at all.
  const ticksRef = useRef([]);
  useEffect(() => { ticksRef.current = ticks; }, [ticks]);

  useEffect(() => {
    if (!run?.sim_run_id) return undefined;
    if (!isLiveStatus(run.status)) return undefined;
    pollRef.current = setInterval(async () => {
      try {
        const seen = ticksRef.current;
        const since = seen.length ? seen[seen.length - 1].tick : -1;
        const { data } = await getSimTicks(run.sim_run_id, since);
        if (data.ticks?.length) {
          setTicks((prev) => [...prev, ...data.ticks].sort((a, b) => a.tick - b.tick));
          // A tick that arrived by poll rather than on the stream still ends
          // every turn it contains: otherwise a dropped connection leaves
          // bubbles spinning for agents that finished long ago.
          const newest = data.ticks[data.ticks.length - 1].tick;
          setInFlight((prev) => prev.filter((d) => d.tick > newest));
          setActivity((prev) => prev.filter((a) => a.tick > newest));
        }
        // Without the tick list: `data.ticks` is only what arrived since the
        // last poll, and folding it into the run would leave a `ticks` field
        // on it that disagrees with the page's own.
        const { ticks: _polled, ...meta } = data;
        setRun((prev) => ({ ...prev, ...meta }));
      } catch { /* transient */ }
    }, 2500);
    return () => clearInterval(pollRef.current);
  }, [run?.sim_run_id, run?.status]);

  // Following pins the scrubber to the newest tick until the user scrubs back.
  useEffect(() => {
    if (following && ticks.length) setCursor(ticks.length - 1);
  }, [ticks.length, following]);

  const handleStart = async () => {
    if (!scenario) return;
    setStarting(true);
    setMessage('');
    try {
      const { data } = await startSimulation(scenario.scenario_id, selectedWorkspace);
      if (data.sim_run_id) {
        setTicks([]);
        setInFlight([]);
        setRun(data);
        setFollowing(true);
        setMode('watch');
        // The page already has the run the call just returned; pinning it in
        // the URL keeps the link honest without asking for it a second time.
        shownRunRef.current = data.sim_run_id;
        navigate(`/playground/${scenario.scenario_id}?run=${data.sim_run_id}`,
                 { replace: true });
        refreshRuns();
      }
    } catch (e) {
      setMessage(e.response?.data?.detail || t('playground.startFailed'));
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    if (!run?.sim_run_id) return;
    // The backend interrupts the calls in flight, so the tick in progress is
    // abandoned rather than finished — the button means now, not next tick.
    try {
      await stopSimulation(run.sim_run_id);
      setRun((prev) => ({ ...prev, status: 'stopping' }));
      setInFlight([]);
      setActivity([]);
      setWaitingForTrigger(false);
    } catch { /* already finished */ }
  };

  /** Ask the server for the run again — status, ticks and the history row.
      The page is already fed by the stream and a poll, but both are
      best-effort: a dropped connection, a reload mid-run or a run driven from
      another tab all leave the page a little behind, and "is it still like
      that?" should not require a reload that loses the tick you scrubbed to. */
  const handleRefresh = useCallback(async () => {
    if (!run?.sim_run_id) {
      refreshRuns();
      return;
    }
    setRefreshing(true);
    try {
      await Promise.all([loadRun(run.sim_run_id), refreshRuns()]);
    } finally {
      setRefreshing(false);
    }
  }, [run?.sim_run_id, loadRun, refreshRuns]);

  /** Poke one agent from outside the world while the run is live. */
  const handleTrigger = async (agent, text) => {
    if (!run?.sim_run_id) return false;
    try {
      await triggerSimAgent(run.sim_run_id, agent, text);
      setMessage('');
      return true;
    } catch (e) {
      setMessage(e.response?.data?.detail || t('playground.triggerFailed'));
      return false;
    }
  };

  const handleEstimate = async () => {
    try {
      const { data } = await estimateScenario(scenario.scenario_id);
      setEstimate(data);
    } catch (e) {
      setMessage(e.response?.data?.detail || t('playground.estimateFailed'));
    }
  };

  // Renaming is a write of the *stored* scenario, not of the setup form's
  // draft: the header is on screen in every mode, including the two that have
  // no draft to save. The empty name the backend refuses is refused here too,
  // so a cleared field reverts instead of erroring.
  const handleRename = async (name) => {
    if (!name.trim() || name === scenario.name) return;
    try {
      const { data } = await updateScenario(scenario.scenario_id, { ...scenario, name });
      setScenario(data);
    } catch (e) {
      setMessage(e.response?.data?.detail || t('playground.saveFailed'));
    }
  };

  const handleDelete = async () => {
    try {
      await deleteScenario(scenario.scenario_id);
      navigate('/playground');
    } catch (e) {
      setMessage(e.response?.data?.detail || t('playground.deleteFailed'));
    }
  };

  const currentTick = ticks[cursor];
  const live = isLiveStatus(run?.status);
  // The run's own activation, not the scenario's: editing the scenario must not
  // relabel — or re-group — a transcript that was recorded under the old mode.
  const activation = run?.activation || scenario?.activation || 'synchronous';
  const envSpec = environments.find((e) => e.env_id === scenario?.environment);
  // The build chat, offered to the floating panel as well as to the sidebar tab.
  const scenarioChat = useScenarioChatDescriptor(
    scenario?.scenario_id, envSpec, setScenario, registerChatSend,
  );

  /** Ask the builder for something, opening the chat if it is not on screen. */
  const askAgent = useCallback((text) => {
    if (!text) return;
    if (chatSendRef.current) {
      chatSendRef.current(text);
      return;
    }
    pendingAskRef.current = text;
    setSideTab('chat');
  }, []);
  usePageChat(scenarioChat);
  const roleNames = (scenario?.roles || []).map((r) => r.display_name || r.name || r.agent_id);
  // Counted from the same reading of the ticks the feed renders, so the badge
  // and the log can never disagree about how much the world has written.
  const eventCount = useMemo(() => eventLines(ticks, cursor).length, [ticks, cursor]);

  // Looking at the log is what marks it read — while it is on screen it stays
  // read, so a tick landing under your eyes does not badge itself.
  useEffect(() => {
    if (pane === 'events') setEventsSeen(eventCount);
  }, [pane, eventCount]);

  if (loading) {
    return (
      <PageContainer>
        <div className="flex items-center gap-2 text-sm text-gray-500 py-10">
          <Loader className="w-4 h-4 animate-spin" /> {t('common.loading')}
        </div>
      </PageContainer>
    );
  }

  if (missing || !scenario) {
    return (
      <PageContainer>
        <PageHeader
          icon={Gamepad2}
          title={t('playground.scenarioNotFound')}
          backTo="/playground"
          backLabel={t('playground.scenarios')}
        />
        <div className="bg-white rounded-xl border border-gray-200 p-10 text-center text-sm text-gray-500">
          {t('playground.scenarioNotFoundHint')}
        </div>
      </PageContainer>
    );
  }

  return (
    /* Watching and setting up both fill the screen: the working surface should
       run to the bottom edge rather than stopping at some guessed height, and
       — the reason setup joined it — a sidebar that stretched to match a
       twelve-card form put the chat's composer a screen and a half below the
       fold. Each column scrolls itself instead. History is an ordinary list,
       so it keeps the normal flow. */
    <PageContainer fill={mode !== 'history'}>
      <PageHeader
        icon={Gamepad2}
        title={
          <InlineEdit
            value={scenario.name}
            onSave={handleRename}
            className="text-2xl font-bold"
          />
        }
        backTo="/playground"
        backLabel={t('playground.scenarios')}
        badges={
          /* Centred on the title it read as floating in the middle of it once
             the ⓘ next to it was gone. Dropped onto the title's baseline —
             where the ⓘ sat — so the row has one line to read along. */
          <span className="text-xs text-gray-500 self-end pb-[3px]">
            {t('playground.scenarioMeta', {
              env: envSpec?.env_name || scenario.environment,
              seed: scenario.seed,
              ticks: t('playground.tickCount', { count: scenario.max_ticks }),
            })}
            {' · '}
            <span className="font-semibold text-indigo-600">
              {t(`playground.activation.${scenario.activation || 'synchronous'}`)}
            </span>
          </span>
        }
        actions={
          <>
            <div className="inline-flex rounded-lg border border-gray-300 overflow-hidden">
              {['setup', 'watch', 'history'].map((m) => (
                <button
                  key={m} onClick={() => setMode(m)}
                  className={`px-3 py-1.5 text-xs font-semibold capitalize ${
                    mode === m ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600'
                  }`}
                >
                  {t(`playground.mode.${m}`)}
                  {/* How many runs there are to go back to is the reason to
                      open the tab at all, so the tab carries the number. */}
                  {m === 'history' && runs.length > 0 && (
                    <span className={`ml-1.5 text-[10px] font-bold ${
                      mode === m ? 'text-indigo-100' : 'text-gray-400'
                    }`}>
                      {runs.length}
                    </span>
                  )}
                </button>
              ))}
            </div>
            <button
              onClick={handleEstimate}
              className="inline-flex items-center px-3 py-2 text-xs font-semibold text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-lg hover:bg-indigo-100"
            >
              <DollarSign className="w-3.5 h-3.5 mr-1" /> {t('playground.estimate')}
            </button>
            <button
              onClick={handleRefresh}
              disabled={refreshing}
              title={t('playground.refreshRun')}
              className="inline-flex items-center px-3 py-2 text-xs font-semibold text-gray-600 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 mr-1 ${refreshing ? 'animate-spin' : ''}`} />
              {t('common.refresh')}
            </button>
            {live ? (
              <button
                onClick={handleStop}
                className="inline-flex items-center px-3 py-2 text-xs font-semibold text-white bg-amber-600 rounded-lg hover:bg-amber-700"
              >
                <Square className="w-3.5 h-3.5 mr-1" /> {t('playground.stop')}
              </button>
            ) : (
              <button
                onClick={handleStart}
                disabled={starting || !scenario.roles.length}
                className="inline-flex items-center px-3 py-2 text-xs font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              >
                {starting ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Play className="w-3.5 h-3.5 mr-1" />}
                {t('playground.run')}
              </button>
            )}
            <button
              onClick={handleDelete}
              title={t('playground.deleteScenario')}
              className="p-2 text-gray-400 hover:text-red-600 border border-gray-200 rounded-lg"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </>
        }
      />

      {message && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span className="flex-1">{message}</span>
          <button
            onClick={() => setMessage('')}
            aria-label={t('common.dismiss')}
            title={t('common.dismiss')}
            className="shrink-0 text-amber-500 hover:text-amber-800"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      <div className="flex flex-col gap-6 flex-1 min-h-0">
        {/* Transport + cost meter */}
        {/* The scrubber is the stage's playback as much as the transcript's:
            both readings are of the tick it is parked on. */}
        {(estimate || run) && mode !== 'history' && (
          <div className="shrink-0 bg-white rounded-xl border border-gray-200 p-4 shadow-sm">
            {estimate && (
              <div className="rounded-lg border border-indigo-100 bg-indigo-50 p-3 text-xs text-indigo-800 flex items-start gap-2">
                <div className="flex-1">
                  <span className="font-bold">
                    {t('playground.projectedCost', { cost: estimate.estimated_total_cost.toFixed(4) })}
                  </span>
                  {' — '}
                  {t('playground.estimateBreakdown', {
                    agents: estimate.agents,
                    ticks: estimate.max_ticks,
                    calls: estimate.llm_calls,
                  })}{' '}
                  <span className="text-indigo-600">
                    {t(`playground.${estimate.note_key}`, { defaultValue: estimate.note })}
                  </span>
                </div>
                <button
                  onClick={() => setEstimate(null)}
                  aria-label={t('common.dismiss')}
                  title={t('common.dismiss')}
                  className="shrink-0 text-indigo-400 hover:text-indigo-700"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            )}

            {run && (
              <div className={`flex items-center gap-3 flex-wrap ${estimate ? 'mt-4 pt-3 border-t border-gray-100' : ''}`}>
                <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${STATUS_STYLES[run.status] || 'bg-gray-100 text-gray-600'}`}>
                  {statusLabel(run.status, t)}
                </span>
                {live && (
                  <span className="inline-flex items-center gap-1 text-xs text-blue-600">
                    <Radio className="w-3 h-3 animate-pulse" /> {t('playground.live')}
                  </span>
                )}
                <span className="text-xs text-gray-500">
                  {t('playground.tickProgress', {
                    tick: currentTick?.tick ?? 0,
                    total: run.ticks_done || 0,
                  })}
                </span>
                <span className="text-xs font-semibold text-gray-700">
                  ${(run.total_cost || 0).toFixed(4)}
                </span>
                {run.error && <span className="text-xs text-amber-700">{run.error}</span>}

                {/* The right-hand group is one block pinned to the edge, not
                    three items that happen to be last: the scrubber only
                    exists once a tick has landed, and hanging `ml-auto` on it
                    meant the run picker and the history button sat on the left
                    for the first seconds of every run and then jumped right
                    when the first tick arrived. */}
                <div className="flex items-center gap-3 flex-wrap ml-auto">
                  {/* Tick scrubber — ticks are a cleaner axis than wall time. */}
                  {ticks.length > 0 && (
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => { setFollowing(false); setCursor((c) => Math.max(0, c - 1)); }}
                        className="p-1 text-gray-400 hover:text-gray-700"
                      >
                        <ChevronLeft className="w-4 h-4" />
                      </button>
                      <input
                        type="range" min={0} max={ticks.length - 1} value={cursor}
                        onChange={(e) => { setFollowing(false); setCursor(parseInt(e.target.value, 10)); }}
                        className="w-48"
                      />
                      <button
                        onClick={() => {
                          const next = Math.min(ticks.length - 1, cursor + 1);
                          setCursor(next);
                          if (next === ticks.length - 1) setFollowing(true);
                        }}
                        className="p-1 text-gray-400 hover:text-gray-700"
                      >
                        <ChevronRight className="w-4 h-4" />
                      </button>
                      <button
                        onClick={() => { setFollowing(true); setCursor(ticks.length - 1); }}
                        className={`text-xs font-semibold px-2 py-1 rounded ${
                          following ? 'bg-indigo-100 text-indigo-700' : 'text-gray-500 hover:text-gray-800'
                        }`}
                      >
                        {t('playground.follow')}
                      </button>
                    </div>
                  )}

                  {/* The picker is the quick hop between neighbouring runs; when
                      the question is which run, the history tab is the list that
                      can answer it, so it sits right next to it. */}
                  {runs.length > 1 && (
                    <select
                      value={run.sim_run_id}
                      onChange={(e) => openRun(e.target.value)}
                      className="text-xs border border-gray-300 rounded-md px-2 py-1"
                    >
                      {runs.map((r) => (
                        <option key={r.sim_run_id} value={r.sim_run_id}>
                          {new Date(r.started_at).toLocaleString()} ({r.status})
                        </option>
                      ))}
                    </select>
                  )}
                  <button
                    onClick={() => setMode('history')}
                    title={t('playground.runHistory')}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-gray-500 hover:text-indigo-700 border border-gray-200 rounded-md px-2 py-1"
                  >
                    <History className="w-3.5 h-3.5" />
                    {runs.length > 0 && runs.length}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {mode === 'history' ? (
          /* Every run this scenario has had, as a list: how far each got, why
             it ended, what it scored and what it cost — the four things you
             actually pick a run by, and none of which fit in the picker up in
             the transport bar. Choosing one goes through the URL, so the run
             you land on is the run you can link someone to. */
          <div className="flex flex-col gap-3">
            <RunHistory
              runs={runs}
              selectedId={run?.sim_run_id}
              /* Only the fallback: a row with a snapshot shows its own cap. */
              maxTicks={scenario.max_ticks}
              onSelect={(r) => { openRun(r.sim_run_id); setMode('watch'); }}
              empty={t('playground.noRunsYetForScenario')}
            />
            <Link
              to="/playground/runs"
              className="self-start inline-flex items-center gap-1.5 text-xs font-semibold text-indigo-600 hover:text-indigo-800"
            >
              <History className="w-3.5 h-3.5" /> {t('playground.allScenariosHistory')}
            </Link>
          </div>
        ) : mode === 'setup' ? (
          /* Setting up is the same two columns watching is: the form takes the
             width, and the sidebar keeps its readouts — and the chat that can
             fill the form in for you, which is most of the point of having it
             here rather than only on the watch tab. */
          <div className={`${TWO_COLUMNS} xl:flex-1 xl:min-h-0`}>
            <SetupPanel
              scenario={scenario}
              environments={environments}
              agents={agents}
              models={models}
              /* The prose lives on the watch card's switch, which a scenario
                 with no runs behind it never opens on. Without a way through
                 from the field it belongs next to, it would be a tab nobody
                 finds until after their first run. */
              onOpenNarrative={() => { setMode('watch'); setPane('narrative'); }}
              /* The form is the long half of the row, so it owns the scroll:
                 the sidebar beside it stays pinned to the viewport. */
              className="xl:min-h-0 xl:overflow-y-auto xl:pr-1"
              /* Saving is not a request to stop editing — the panel stays put
                 and says so itself, rather than throwing the user into the
                 watch tab mid-setup. */
              onSaved={(s) => setScenario(s)}
            />
            <ScenarioSidebar
              tab={sideTab} onTab={setSideTab}
              scenario={scenario} envSpec={envSpec} currentTick={currentTick}
              run={run} ticks={ticks} live={live}
              chat={scenarioChat}
            />
          </div>
        ) : (
          /* Two columns. The run takes the width — as the transcript or as the
             world's event log, one at a time behind a switch: both are the same
             ticks read two ways, and standing them side by side only meant the
             quieter one kept tugging at the eye. The world state and the run's
             meters are readouts you glance at, so they get the sidebar. */
          <div className={`${TWO_COLUMNS} xl:flex-1 xl:min-h-0`}>
            {/* The run as a conversation: every turn since tick 0, each with
                the agent's thought and its call on the environment — the same
                three things a chat with an agent shows. Behind the second tab,
                the same ticks as the world wrote them. */}
            {/* Less padding above the switcher than around the rest: it is the
                card's own title row, and a full gutter over it pushed the
                transcript down for nothing. No padding under the feed either:
                the scroll runs to the card's edge, so a long transcript reads
                as continuing past the border instead of stopping short of it.
                The composer carries that gutter itself when it is there. */}
            <div className={`bg-white rounded-xl border border-gray-200 px-5 pt-3 shadow-sm min-w-0 flex flex-col xl:min-h-0 ${
              /* A feed runs to the card's bottom edge on purpose, and so does
                 the map — it takes the whole card under the switch, sides and
                 floor included, because a map inset in a gutter is a map with
                 less map in it. A page being written keeps the gutter. */
              pane === 'narrative' ? 'pb-5' : 'pb-0'
            }`}>
              <div className="shrink-0 flex items-center justify-between gap-2 mb-3 flex-wrap">
                <div className="inline-flex items-center gap-1 p-0.5 rounded-lg bg-gray-100">
                  {/* The world first and the place it happens in second: both
                      are what the run is *of*, and they are read before and
                      around it rather than after it. The three readings of the
                      run itself follow, transcript first — it is what you came
                      for, and it is what the card opens on. */}
                  <PaneTab
                    active={pane === 'narrative'}
                    onClick={() => setPane('narrative')}
                    icon={BookText}
                    label={t('playgroundNarrative.tab')}
                  />
                  <PaneTab
                    active={pane === 'stage'}
                    onClick={() => setPane('stage')}
                    icon={Map}
                    label={t('playground.stageTitle')}
                  />
                  <PaneTab
                    active={pane === 'transcript'}
                    onClick={() => setPane('transcript')}
                    icon={MessagesSquare}
                    label={t('playground.transcriptTitle')}
                  />
                  <PaneTab
                    active={pane === 'events'}
                    onClick={() => setPane('events')}
                    icon={ListTree}
                    label={t('playground.events')}
                    /* Unread rather than total: the point of the badge is that
                       something happened while you were reading the dialogue,
                       and a running total says that on every tick. */
                    badge={pane === 'events' ? 0 : Math.max(0, eventCount - eventsSeen)}
                  />
                  {/* The same run with the seams taken out: every turn and
                      every event woven into one text, which is the reading
                      neither list can give. Last of the three because it is
                      what you turn to once the run has something to say. */}
                  <PaneTab
                    active={pane === 'story'}
                    onClick={() => setPane('story')}
                    icon={BookOpen}
                    label={t('playground.storyTitle')}
                  />
                </div>
                <span className="text-[11px] text-gray-400">
                  {t(`playground.activation.${activation}`)}
                  {currentTick ? ` · ${t('playground.tickLabelShort', { tick: currentTick.tick })}` : ''}
                </span>
              </div>

              {pane === 'stage' ? (
                <StageCanvas
                  ticks={ticks}
                  cursor={cursor}
                  roles={scenario.roles || []}
                  activity={activity}
                  following={following}
                  /* Out past the card's own padding, to its border on three
                     sides: the negative margins are what make the map the
                     card's floor rather than a panel floating inside it. */
                  className={`${PANE_HEIGHT} -mx-5`}
                />
              ) : pane === 'narrative' ? (
                <NarrativePanel
                  scenario={scenario}
                  onSaved={(saved) => setScenario(saved)}
                  /* Writing a world is exactly the job the build chat is for,
                     and it already has the tool that stores this field — so the
                     pane offers it rather than leaving the user to find the
                     chat and phrase the request. */
                  onAskAgent={askAgent}
                  className={PANE_HEIGHT}
                />
              ) : pane === 'story' ? (
                <StoryPane
                  runId={run?.sim_run_id}
                  status={run?.status}
                  /* Recomposed as the run grows, and only while this pane is
                     the one on screen — it is mounted by the switch. */
                  ticksDone={ticks.length}
                  heightClass={FEED_HEIGHT}
                />
              ) : pane === 'transcript' ? (
                <>
                  <ScenarioTranscript
                    ticks={ticks}
                    cursor={cursor}
                    activation={activation}
                    inFlight={inFlight}
                    activity={activity}
                    following={following}
                    waitingForTrigger={waitingForTrigger}
                    /* Between pressing Run and the first tick there is nothing
                       to render but the fact that it is running — which is the
                       one thing worth saying at that moment. */
                    status={run?.status}
                    heightClass={FEED_HEIGHT}
                  />

                  {live && (
                    /* Pinned to the bottom of the card, and its rule drawn
                       across the whole of it: the composer is the card's
                       floor, the way the chat's is, not a block that floats
                       wherever the transcript happens to end. The negative
                       margin is what takes the border out to the card's
                       edges past its padding. */
                    <TriggerComposer
                      agents={roleNames}
                      onSend={handleTrigger}
                      className="shrink-0 mt-auto -mx-5 px-5 pt-3 pb-5 border-t border-gray-200"
                    />
                  )}
                </>
              ) : (
                <EventFeed ticks={ticks} cursor={cursor} heightClass={FEED_HEIGHT} />
              )}
            </div>

            <ScenarioSidebar
              tab={sideTab} onTab={setSideTab}
              scenario={scenario} envSpec={envSpec} currentTick={currentTick}
              run={run} ticks={ticks} live={live}
              chat={scenarioChat}
            />
          </div>
        )}
      </div>
    </PageContainer>
  );
}


/**
 * The scenario page's right-hand column.
 *
 * Two tabs over one column, not two columns: what the scenario *is* and a chat
 * that can change it are both read at the full height of the sidebar, and only
 * ever one at a time. The readouts stay the default — you open this page to
 * look at something, and the chat is what you turn to when looking is not
 * enough.
 *
 * The chat's callbacks are memoised on the scenario id on purpose: EntityChat
 * loads its transcript in an effect keyed on them, so fresh closures every
 * render would refetch the conversation on every keystroke.
 */
/**
 * The scenario's build chat, as one descriptor.
 *
 * A descriptor rather than wiring inside the sidebar, because the same
 * conversation has two homes — the sidebar's Chat tab, and the floating page
 * chat — and two copies of it would be two threads about one scenario.
 *
 * The callbacks are memoised on the scenario id on purpose: EntityChat loads
 * its transcript in an effect keyed on them, so fresh closures every render
 * would refetch the conversation on every keystroke.
 */
function useScenarioChatDescriptor(scenarioId, envSpec, onScenarioChanged, registerSend) {
  const { t } = useI18n();

  const loadChat = useCallback(() => getScenarioChat(scenarioId), [scenarioId]);
  const clearChat = useCallback(() => clearScenarioChat(scenarioId), [scenarioId]);
  const stopChat = useCallback(() => stopScenarioChat(scenarioId), [scenarioId]);

  // The turn ends with the scenario as it now stands, so the form beside the
  // chat updates without a refetch.
  const onEvent = useCallback((ev) => {
    if (ev.type === 'scenario' && ev.scenario) onScenarioChanged(ev.scenario);
  }, [onScenarioChanged]);

  // What the world is made of is a per-environment question: a market has no
  // locations to walk between and no items to hand over, so those two openers
  // are offered only where the environment actually has a knob for them.
  const paramNames = useMemo(
    () => new Set((envSpec?.params || []).map((p) => String(p.name || ''))),
    [envSpec],
  );

  return useMemo(() => (scenarioId ? {
    scope: `scenario:${scenarioId}`,
    path: scenarioChatUrl(scenarioId),
    loadChat, clearChat, stopChat, onEvent, registerSend,
    title: t('playground.scenarioChat'),
    emptyHint: t('playground.scenarioChatHint'),
    suggestions: [
      t('playground.chatSuggestAddCharacter'),
      ...(paramNames.has('locations') ? [t('playground.chatSuggestAddLocation')] : []),
      ...(paramNames.has('starting_items') ? [t('playground.chatSuggestAddItem')] : []),
      t('playground.chatSuggestRetune'),
      t('playground.chatSuggestExplain'),
      // The chat can start the simulation too — behind an approval step, so the
      // opener asks for the estimate rather than for a run.
      t('playground.chatSuggestRun'),
    ],
  } : null), [scenarioId, loadChat, clearChat, stopChat, onEvent, registerSend, paramNames, t]);
}

function ScenarioSidebar({
  tab, onTab, scenario, envSpec, currentTick, run, ticks, live, chat,
}) {
  const { t } = useI18n();
  const { inlineSuppressed: panelHoldsChat } = usePageChatPanel();

  /* The header of whichever panel is open — one bar, two halves, the card's
     full width, and the only place either panel is named. */
  const tabs = (
    <div className="shrink-0 grid grid-cols-2 border-b border-gray-200">
      <ColumnTab
        active={tab === 'params'} onClick={() => onTab('params')}
        icon={Gauge} label={t('playground.parameters')}
      />
      <ColumnTab
        active={tab === 'chat'} onClick={() => onTab('chat')}
        icon={MessagesSquare} label={t('playground.scenarioChat')}
      />
    </div>
  );

  return (
    <div className="flex flex-col xl:min-h-0">
      {tab === 'chat' ? (
        /* The chat owns the whole column: a build conversation that has to be
           scrolled in a 12-line box is not one you will use. The tab bar is
           the card's header, so the card carries no padding of its own — the
           body below it does. */
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden flex flex-col xl:flex-1 xl:min-h-0">
          {tabs}
          {/* The chat fills the body, so its composer lands on the card's
              floor rather than under the last message, and the negative
              margins take its rule out to the card's edges past the padding —
              the same footing the transcript's trigger composer stands on. */}
          <div className="p-5 flex flex-col flex-1 min-h-0">
            {/* While the floating panel is holding this same conversation,
                the tab says where it went instead of running a second copy. */}
            {panelHoldsChat ? <InPanelNote /> : (
              <EntityChat
                {...chat}
                header={false}
                /* Below xl the page is an ordinary scroll, so the feed is a
                   bounded box. In the column it takes exactly what is left —
                   including nothing, on a short window with the transport bar
                   up: it scrolls itself, so shrinking is right and growing past
                   the column's floor is not. */
                heightClass="max-h-[26rem] min-h-[14rem] xl:max-h-none xl:min-h-0"
                className="flex-1 min-h-0"
                composerClassName="mt-3 xl:mt-auto -mx-5 px-5"
              />
            )}
          </div>
        </div>
      ) : (
        /* Readouts: the world as it stands, and how the run is doing. The
           column scrolls on its own so a long sidebar cannot stretch the row
           past the bottom of the screen. */
        <div className="space-y-6 xl:min-h-0 xl:overflow-y-auto xl:pr-1">
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
            {tabs}
            <div className="p-5">
              {/* Which world these readouts are of — a caption under the
                  header, not a second heading competing with it. */}
              <div className="mb-4 text-[11px] text-gray-400 uppercase tracking-wide truncate">
                {t('playground.world')}{envSpec ? ` — ${envSpec.env_name}` : ''}
              </div>
              <WorldView frame={currentTick?.frame} />
              {(envSpec?.params || []).length > 0 && (
                <dl className="mt-4 pt-3 border-t border-gray-100 space-y-1">
                  {envSpec.params.map((p) => (
                    <div key={p.name} className="flex items-baseline justify-between gap-2 text-[11px]">
                      <dt className="text-gray-500 truncate" title={p.description}>{p.name}</dt>
                      <dd className="font-mono text-gray-800 text-right truncate">
                        {String(scenario.env_params?.[p.name]
                          ?? (Array.isArray(p.default) ? p.default.join(', ') : p.default))}
                      </dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          </div>

          {run && <RunMeters run={run} ticks={ticks} scenario={scenario} />}

          {run?.scores && Object.keys(run.scores).length > 0 && !live && (
            <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
              <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-3">
                {t('playground.objectives')}
              </h3>
              <div className="space-y-2">
                {Object.entries(run.scores).map(([name, vals]) => (
                  <div key={name} className="text-xs">
                    <span className="font-semibold text-gray-900">{name}</span>
                    <div className="text-gray-600">
                      {Object.entries(vals).map(([k, v]) => `${k}: ${v}`).join(' · ')}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Last in the column: what the run was configured with is
              provenance, looked up when a result needs explaining, so it sits
              below the readouts that are watched while it runs. Its own launch
              snapshot, so it still reads true after the scenario has been
              retuned. */}
          {run && <RunParams run={run} scenario={scenario} />}
        </div>
      )}
    </div>
  );
}


/**
 * Send a message into a running world from outside it.
 *
 * In triggered mode this is the external event that wakes an agent; in a
 * synchronous world it is an ordinary message, delivered on the next tick. It
 * is deliberately the same door either way — an agent must not be able to tell
 * an operator's poke from a colleague's.
 */
function TriggerComposer({ agents, onSend, className = '' }) {
  const { t } = useI18n();
  const [agent, setAgent] = useState(agents[0] || '');
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);

  const send = async () => {
    if (!agent || !text.trim()) return;
    setSending(true);
    const ok = await onSend(agent, text.trim());
    setSending(false);
    if (ok) setText('');
  };

  return (
    /* Built like the chat's composer, because it is one: one bordered field
       that lights up as a whole on focus, the text at reading size, and a
       round send button at the end. The role picker is the only extra — it is
       the "to:" of this message, so it is wide enough to read a name in
       rather than a dropdown to guess at. */
    <div className={className}>
      <div className="text-[11px] font-bold text-gray-500 uppercase mb-1.5">
        {t('playground.externalTrigger')}
      </div>
      {/* Filled rather than outlined: the field sits on a white card, and an
          outline alone left the recipient picker floating in the card's own
          background instead of reading as one input.
          The fill stays put on focus, and the picker drops its native
          appearance so the tint runs under it too — a select left to the
          platform paints its own background on macOS, which cut the fill in
          two and undid the one-field reading this is built for. */}
      <div
        className="flex items-center gap-3 bg-gray-100 border border-gray-300 rounded-2xl px-3 py-2
          focus-within:border-indigo-400 focus-within:ring-2 focus-within:ring-indigo-100
          shadow-sm transition-all"
      >
        <select
          value={agent} onChange={(e) => setAgent(e.target.value)}
          title={t('playground.triggerRecipient')}
          className="shrink-0 w-40 sm:w-48 appearance-none text-sm text-gray-700 bg-transparent
            border-0 border-r border-gray-300 rounded-none pr-2 py-1 focus:outline-none truncate"
        >
          {agents.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') send(); }}
          placeholder={t('playground.triggerPlaceholder')}
          className="flex-1 min-w-0 text-base text-gray-800 placeholder-gray-400 bg-transparent
            border-0 focus:outline-none leading-relaxed"
        />
        <button
          onClick={send} disabled={sending || !text.trim()}
          title={t('playground.externalTrigger')}
          className="flex-shrink-0 w-8 h-8 flex items-center justify-center rounded-full
            bg-indigo-600 text-white hover:bg-indigo-700
            disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {sending ? <Loader className="w-4 h-4 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
        </button>
      </div>
      <p className="text-[11px] text-gray-400 mt-1.5">{t('playground.triggerHint')}</p>
    </div>
  );
}


/** How the run itself is doing — a readout, which is why it is in the sidebar. */
function RunMeters({ run, ticks, scenario }) {
  const { t } = useI18n();
  const tokens = ticks.reduce(
    (sum, tk) => sum + (tk.decisions || []).reduce(
      (n, d) => n + (d.inbound_tokens || 0) + (d.outbound_tokens || 0), 0,
    ),
    0,
  );
  const calls = ticks.reduce((n, tk) => n + (tk.decisions || []).length, 0);
  // Everything the run is measured *against* comes from its own snapshot: the
  // scenario is edited in place, so reading a finished run against the live row
  // would show it capped at a number it never ran with and cast with roles it
  // never had.
  const config = runConfig(run, scenario);
  const rows = [
    [t('playground.meters.activation'), t(`playground.activation.${run.activation || config.activation || 'synchronous'}`)],
    [t('playground.meters.ticks'), `${run.ticks_done || 0} / ${config.max_ticks}`],
    [t('playground.meters.calls'), calls],
    [t('playground.meters.tokens'), tokens.toLocaleString()],
    [t('playground.meters.cost'), `$${(run.total_cost || 0).toFixed(4)}`],
    [t('playground.meters.agents'), (config.roles || []).length],
  ];
  if (run.stop_reason) {
    // A world that wrote its own ending says which one it reached; "terminal
    // state" is all the shipped environments can say, and all a user would
    // otherwise see of a condition they authored themselves.
    const ending = run.final_state?.ending;
    rows.push([t('playground.meters.endedBecause'),
      (run.stop_reason === 'terminal' && ending)
        ? ending
        : t(`playground.stopReason.${run.stop_reason}`, { defaultValue: run.stop_reason })]);
  }
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
      <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-3 flex items-center gap-1.5">
        <Gauge className="w-4 h-4 text-indigo-500" /> {t('playground.runMeters')}
      </h3>
      <dl className="space-y-1">
        {rows.map(([label, value]) => (
          <div key={label} className="flex items-baseline justify-between gap-2 text-[11px]">
            <dt className="text-gray-500">{label}</dt>
            <dd className="font-semibold text-gray-800 text-right">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}


/** What a role falls back to when it picks no model of its own. */
function roleInherits(agent, t) {
  return agent?.model
    ? t('playground.inheritFromAgent', { model: agent.model })
    : t('playground.inheritFromScenario');
}


/**
 * Provider + model as a single choice, from the enabled catalogue only.
 *
 * Typing a model name by hand was the old behaviour and it had three failure
 * modes: a typo only showed up as a failed tick, a model could be handed to a
 * provider that does not serve it, and an unpriced pair made both the estimate
 * and the cost ceiling read zero. A pair that came from the catalogue has none
 * of them.
 */
function ModelSelect({ provider, model, options, placeholder, onChange, className }) {
  const value = model ? `${provider || ''}::${model}` : '';
  const byProvider = options.reduce((acc, o) => {
    (acc[o.provider] = acc[o.provider] || []).push(o.model);
    return acc;
  }, {});
  // A stored pair the catalogue no longer offers stays selectable — otherwise
  // editing an unrelated field would silently drop the scenario's model.
  const known = options.some((o) => `${o.provider}::${o.model}` === value);

  return (
    <select
      value={value}
      onChange={(e) => {
        const v = e.target.value;
        const i = v.indexOf('::');
        onChange(i === -1
          ? { provider: null, model: null }
          : { provider: v.slice(0, i) || null, model: v.slice(i + 2) || null });
      }}
      className={className}
    >
      <option value="">{placeholder}</option>
      {value && !known && (
        <option value={value}>{model}{provider ? ` (${provider})` : ''}</option>
      )}
      {Object.entries(byProvider).map(([p, ids]) => (
        <optgroup key={p} label={p}>
          {ids.map((id) => <option key={`${p}::${id}`} value={`${p}::${id}`}>{id}</option>)}
        </optgroup>
      ))}
    </select>
  );
}


/** Setup mode: environment parameters (declared by the env) and role overlays. */
function SetupPanel({
  scenario, environments, agents, models, onSaved, onOpenNarrative, className = '',
}) {
  const { t } = useI18n();
  const toast = useToast();
  const [draft, setDraft] = useState(scenario);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  // The role being edited: {index, role}. index -1 is a role being added, so
  // the dialog does not have to know the difference until it saves.
  const [editing, setEditing] = useState(null);

  // The chat in the sidebar edits the same scenario this form does, so the
  // stored scenario can change while you are typing in it. Re-sync when it
  // does — but never over unsaved edits: silently replacing what someone just
  // typed is worse than leaving a stale field they can still save or discard.
  const syncedRef = useRef(scenario);
  const [outOfSync, setOutOfSync] = useState(false);
  useEffect(() => {
    if (scenario === syncedRef.current) return;
    const previous = syncedRef.current;
    syncedRef.current = scenario;
    setDraft((current) => {
      if (JSON.stringify(current) === JSON.stringify(previous)) {
        setOutOfSync(false);
        return scenario;
      }
      // There are edits to protect. The name is not one of them — the header
      // owns it, this form has no field for it — so adopt it and weigh only
      // what the form itself could have written. Otherwise renaming a
      // scenario from the header would announce itself as a conflict.
      const settled = (v) => JSON.stringify({ ...v, name: '', updated_at: '' });
      setOutOfSync(settled(scenario) !== settled(previous));
      return { ...current, name: scenario.name };
    });
  }, [scenario]);

  const envSpec = environments.find((e) => e.env_id === draft.environment);
  // Everything on this panel edits a draft, including the role dialog — and
  // the dialog's own Save button makes it easy to believe a role is stored
  // when only the draft holds it. So say so, wherever you are on the page.
  const dirty = JSON.stringify(draft) !== JSON.stringify(scenario);

  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const { data } = await updateScenario(draft.scenario_id, draft);
      // Mark this version as the one the form is synced to *before* handing it
      // up: the parent's state change re-runs the sync effect, and without this
      // the edits we just saved would read as an unsynced conflict.
      syncedRef.current = data;
      setDraft(data);
      setOutOfSync(false);
      onSaved(data);
      toast.success(t('playground.scenarioSaved'));
    } catch (e) {
      setError(e.response?.data?.detail || t('playground.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  /** Commit the dialog's copy back into the draft — new role, or edited one. */
  const commitRole = (index, role) => {
    const roles = index < 0
      ? [...draft.roles, role]
      : draft.roles.map((r, j) => (j === index ? role : r));
    setDraft({ ...draft, roles });
    setEditing(null);
  };

  return (
    <div className={`space-y-6 ${className}`}>
      {error && <div className="text-xs text-red-600">{error}</div>}

      {outOfSync && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-sky-200 bg-sky-50 px-3 py-2">
          <span className="text-xs text-sky-800">{t('playground.changedElsewhere')}</span>
          <button
            onClick={() => { setDraft(scenario); setOutOfSync(false); }}
            className="text-xs font-semibold text-sky-700 hover:text-sky-900 underline"
          >
            {t('playground.discardAndReload')}
          </button>
        </div>
      )}

      {dirty && (
        <div className="sticky top-2 z-10 flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 shadow-sm">
          <span className="text-xs text-amber-800">{t('playground.unsavedChanges')}</span>
          <div className="flex items-center gap-3">
            {/* The way back, as on the world form: a banner that only offers
                Save leaves undoing a change the author regrets to Escape and
                a reload. Going back to the stored scenario is the same
                one-liner the out-of-sync banner above already does. */}
            <button onClick={() => setDraft(scenario)} disabled={saving}
                    className="text-xs font-semibold text-amber-800 underline disabled:opacity-50">
              {t('playground.discard')}
            </button>
            <button
              onClick={save} disabled={saving}
              className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}
              {t('playground.saveScenario')}
            </button>
          </div>
        </div>
      )}

      {/* The description, and only it: the name is edited in the page header,
          where it is on screen in every mode. No heading over a single field —
          the label is the heading. */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
        <label className="block text-xs font-semibold text-gray-600 mb-0.5">
          {t('playground.description')}
        </label>
        <textarea
          rows={3}
          value={draft.description || ''}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          className="w-full text-sm border border-gray-300 rounded-md px-2 py-1.5"
        />
        <p className="text-[11px] text-gray-400 mt-0.5">{t('playground.descriptionHint')}</p>
        <button
          type="button" onClick={onOpenNarrative}
          className="mt-2 inline-flex items-center gap-1 text-[11px] font-semibold text-indigo-600 hover:text-indigo-800"
        >
          <BookText className="w-3.5 h-3.5" /> {t('playgroundNarrative.openFromSetup')}
        </button>
      </div>

      {/* Environment parameters — rendered generically from the declared schema,
          so a new environment needs no frontend change. */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
        <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-1 flex items-center gap-1.5">
          <Settings2 className="w-4 h-4 text-indigo-500" /> {t('playground.environment')}
        </h3>
        <p className="text-xs text-gray-500 mb-4">{envSpec?.description}</p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {(envSpec?.params || []).map((p) => (
            <div key={p.name}>
              <label className="block text-xs font-semibold text-gray-600 mb-0.5">{p.name}</label>
              <input
                value={draft.env_params[p.name] ?? (Array.isArray(p.default) ? p.default.join(', ') : p.default)}
                onChange={(e) => setDraft({
                  ...draft,
                  env_params: { ...draft.env_params, [p.name]: e.target.value },
                })}
                className="w-full text-sm border border-gray-300 rounded-md px-2 py-1.5"
              />
              <p className="text-[11px] text-gray-400 mt-0.5">{p.description}</p>
            </div>
          ))}
        </div>

        {envSpec?.actions?.length > 0 && (
          <div className="mt-4 pt-3 border-t border-gray-100">
            <div className="text-[11px] font-bold text-gray-500 uppercase mb-1.5">
              {t('playground.actionApiTheOnlyThings')}
            </div>
            <div className="space-y-1">
              {envSpec.actions.map((a) => (
                <div key={a.name} className="text-xs">
                  <span className="font-mono font-semibold text-indigo-700">{a.name}</span>
                  <span className="text-gray-500"> — {a.description}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Activation — who gets a turn, and why. The single knob that decides
          whether this is a simulated clock or a reactive sandbox. */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
        <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-3 flex items-center gap-1.5">
          <Zap className="w-4 h-4 text-indigo-500" /> {t('playground.activationTitle')}
        </h3>
        {/* The grace period belongs beside the mode it qualifies, not under
            both of them: it is the second half of choosing "triggered", and a
            row of its own read as a third setting that applied either way.
            It stays in place when the mode is synchronous, greyed rather than
            gone — a control that vanishes takes the row's height with it, and
            the block jumps every time the mode is toggled. */}
        <div className="flex flex-col md:flex-row md:items-stretch gap-3">
          {['synchronous', 'triggered'].map((mode) => (
            <button
              key={mode}
              onClick={() => setDraft({ ...draft, activation: mode })}
              className={`flex-1 min-w-0 text-left rounded-lg border p-3 ${
                (draft.activation || 'synchronous') === mode
                  ? 'border-indigo-300 bg-indigo-50'
                  : 'border-gray-200 hover:bg-gray-50'
              }`}
            >
              <div className="text-xs font-bold text-gray-900">
                {t(`playground.activation.${mode}`)}
              </div>
              <p className="text-[11px] text-gray-500 mt-0.5 leading-snug">
                {t(`playground.activationHint.${mode}`)}
              </p>
            </button>
          ))}
          {(() => {
            const idle = (draft.activation || 'synchronous') === 'triggered';
            return (
              <div className={`md:w-52 md:shrink-0 md:pl-3 md:border-l md:border-gray-100 ${
                idle ? '' : 'opacity-50'
              }`}>
                <label className="block text-[11px] font-semibold text-gray-600 mb-0.5">
                  {t('playground.limits.idleGrace')}
                </label>
                <input
                  type="number" min={0} value={draft.idle_grace_seconds ?? 0}
                  disabled={!idle}
                  onChange={(e) => setDraft({
                    ...draft, idle_grace_seconds: parseFloat(e.target.value) || 0,
                  })}
                  className={`w-full text-sm border border-gray-300 rounded-md px-2 py-1.5 ${
                    idle ? '' : 'bg-gray-50 cursor-not-allowed'
                  }`}
                />
                <p className="text-[11px] text-gray-400 mt-0.5 leading-snug">
                  {idle ? t('playground.idleGraceHint') : t('playground.triggeredOnly')}
                </p>
              </div>
            );
          })()}
        </div>
      </div>

      {/* The cast — one card per character, edited in a dialog.
          A character carries eight fields including two paragraphs of prose,
          and laid out inline that is a wall of inputs that hides the only
          thing you scan the list for: who is in this world and what they
          want. So the block shows cards and the editing happens in a dialog. */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
        <div className="flex items-center justify-between mb-1">
          <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide flex items-center gap-1.5">
            <Users className="w-4 h-4 text-indigo-500" /> {t('playground.characters')}
          </h3>
          <button
            onClick={() => setEditing({ index: -1, role: emptyCharacter(agents) })}
            className="text-xs font-semibold text-indigo-600 hover:text-indigo-800"
          >
            {t('playground.addCharacter')}
          </button>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          {t('playground.charactersDescription')}
        </p>

        {draft.roles.length === 0 ? (
          <p className="text-xs text-gray-400 italic">{t('playground.noCharactersYet')}</p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {draft.roles.map((r, i) => (
              <CharacterCard
                key={i}
                role={r}
                agent={agents.find((a) => a.id === r.agent_id)}
                world={envSpec}
                triggered={(draft.activation || 'synchronous') === 'triggered'}
                onOpen={() => setEditing({ index: i, role: r })}
                onRemove={() => setDraft({
                  ...draft, roles: draft.roles.filter((_, j) => j !== i),
                })}
              />
            ))}
          </div>
        )}
      </div>

      {/* Run-level parameters */}
      <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
        <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-3">{t('playground.runLimits')}</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            ['max_ticks', t('playground.limits.maxTicks'), 'number', ''],
            ['seed', t('playground.limits.seed'), 'number', ''],
            ['max_concurrent', t('playground.limits.maxConcurrent'), 'number', ''],
            // A timeout on silence, not on the answer: a model still streaming
            // is still working, and cutting it off throws away a reply that
            // was on its way.
            ['stall_timeout', t('playground.limits.stallTimeout'), 'number',
              t('playground.limits.stallTimeoutHint')],
            ['max_turn_seconds', t('playground.limits.maxTurnSeconds'), 'number',
              t('playground.limits.maxTurnSecondsHint')],
            ['cost_ceiling', t('playground.limits.costCeiling'), 'number', ''],
            // Empty means no wall clock at all, which is worth saying on the
            // field: a blank number input otherwise reads as "unset" and the
            // user has no way to know whether that is allowed.
            ['max_wall_seconds', t('playground.limits.wallClock'), 'number',
              t('playground.limits.wallClockHint')],
          ].map(([key, label, type, hint]) => (
            <div key={key}>
              <label className="block text-[11px] font-semibold text-gray-600 mb-0.5" title={hint}>{label}</label>
              <input
                type={type} value={draft[key] ?? ''}
                onChange={(e) => setDraft({
                  ...draft,
                  [key]: type === 'number'
                    ? (e.target.value === '' ? null : parseFloat(e.target.value))
                    : e.target.value,
                })}
                className="w-full text-sm border border-gray-300 rounded-md px-2 py-1.5"
              />
              {hint && <p className="text-[11px] text-gray-400 mt-0.5">{hint}</p>}
            </div>
          ))}
          {/* Provider and model are picked as one pair: chosen separately they
              can name a model the provider's client does not serve, which only
              surfaces as a failed first tick.

              One cell wide, not two: seven numeric fields plus this one fill
              the grid exactly, so the model sits on the second row instead of
              being pushed onto a third of its own with a hole beside it. */}
          <div>
            <label className="block text-[11px] font-semibold text-gray-600 mb-0.5">
              {t('playground.limits.defaultModel')}
            </label>
            <ModelSelect
              provider={draft.default_provider} model={draft.default_model} options={models}
              placeholder={t('playground.inheritFromWorkspace')}
              onChange={({ provider, model }) => setDraft({
                ...draft, default_provider: provider, default_model: model,
              })}
              className="w-full text-sm border border-gray-300 rounded-md px-2 py-1.5"
            />
          </div>
        </div>
        <div className="flex justify-end mt-4">
          <button
            onClick={save} disabled={saving}
            className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
          >
            {saving ? <Loader className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}
            {t('playground.saveScenario')}
          </button>
        </div>
      </div>

      {editing && (
        <CharacterDialog
          role={editing.role}
          isNew={editing.index < 0}
          agents={agents}
          models={models}
          objectives={envSpec?.objectives || []}
          world={envSpec}
          triggered={(draft.activation || 'synchronous') === 'triggered'}
          onSave={(role) => commitRole(editing.index, role)}
          onRemove={editing.index < 0 ? null : () => {
            setDraft({ ...draft, roles: draft.roles.filter((_, j) => j !== editing.index) });
            setEditing(null);
          }}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}


/**
 * The world role a character is cast in, or null when nothing binds.
 *
 * Case-insensitive, mirroring ``WorldSpec.role`` on the server: a form that
 * matched case-sensitively would warn about a binding the run then honours.
 */
function worldRoleFor(world, name) {
  const wanted = (name || '').trim().toLowerCase();
  if (!wanted) return null;
  return (world?.role_specs || [])
    .find((r) => (r.name || '').trim().toLowerCase() === wanted) || null;
}


/**
 * Which of the world's actions a role may take.
 *
 * The same two filters ``CustomEnvironment.allowed_actions`` applies: the
 * role's own list narrows the whole action set (empty = all of it), and an
 * action's own ``roles`` list narrows who may take it. Built-ins declare no
 * ``roles``, so only the first filter reaches them.
 */
function roleActions(world, spec) {
  const permitted = spec?.actions?.length ? new Set(spec.actions) : null;
  return (world?.actions || [])
    .filter((a) => (!permitted || permitted.has(a.name))
      && !((a.roles || []).length && !(a.roles || []).includes(spec?.name)))
    .map((a) => a.name);
}


/**
 * What a character actually starts with, in a few words.
 *
 * Worth saying on the card and in the dialog, because it is otherwise
 * invisible until the run: the role is picked here and its consequences —
 * where you wake up, what you hold, what you may do — live on the world page.
 *
 * ``spec`` null means nothing bound, and then this describes the generic role
 * the world falls back to. That answer comes from the server
 * (``describe_generic_role``) rather than being worked out again here: a
 * promise about what an uncast character can do is worth nothing if the run
 * disagrees with it.
 */
function roleGrants(world, spec, t) {
  const generic = world?.generic_role || null;
  if (!spec) return generic ? genericGrants(generic, t) : [];

  // A role is the generic role with its own overrides on top — the order
  // ``register_cast`` applies them in — so it is described by the same
  // function rather than by a second one that formats it slightly differently.
  const out = genericGrants({
    start_location: spec.start_location || generic?.start_location || '',
    stats: { ...(generic?.stats || {}), ...(spec.stats || {}) },
    actions: roleActions(world, spec),
  }, t);
  if ((spec.start_items || []).length) {
    out.splice(1, 0, t('playground.worldRole.carries', {
      items: spec.start_items.join(', '),
    }));
  }
  return out;
}


/** A blank character, seeded with the first agent so the common case is one click. */
function emptyCharacter(agents) {
  return {
    agent_id: agents[0]?.id || '', name: '', role: '', goal: '',
    private_knowledge: '', objective: null, provider: null, model: null,
    memory_horizon: 16, wake_every: 0, starts: false, npc: false,
  };
}


/** What a character is, at a glance: who it is, what it wants, how it is wired. */
function CharacterCard({ role, agent, world, triggered, onOpen, onRemove }) {
  const { t } = useI18n();
  // Cast in one of the world's roles, or in the generic role the world falls
  // back to. Only the second is worth an amber line, and only when this world
  // declares roles at all: then somebody meant to pick one and did not, and
  // the character will quietly play something else.
  const cast = worldRoleFor(world, role.role);
  const unbound = (world?.roles || []).length > 0 && !cast;
  const grants = roleGrants(world, cast, t);
  const chips = [];
  if (role.model) chips.push(role.model);
  if (role.objective) chips.push(role.objective);
  chips.push(t('playground.memoryChip', { n: role.memory_horizon ?? 0 }));
  if (triggered && role.wake_every > 0) {
    chips.push(t('playground.wakeChip', { n: role.wake_every }));
  }
  if (triggered && role.starts) chips.push(t('playground.startsChip'));
  if (triggered && role.npc) chips.push(t('playground.npcChip'));

  return (
    <div className="rounded-lg border border-gray-200 p-3 flex flex-col gap-2 hover:border-indigo-200">
      <div className="flex items-start gap-2">
        {/* The name is the handle: it is what the others and the log call
            this character, so it is the heading and the way into the full
            record. */}
        <button onClick={onOpen} className="flex-1 min-w-0 text-left group">
          <div className="text-sm font-bold text-gray-900 truncate group-hover:text-indigo-700">
            {role.name || agent?.name || agent?.id || t('playground.unnamedCharacter')}
          </div>
          <div className="text-[11px] text-gray-500 truncate">
            {agent?.name || agent?.id || t('playground.noAgentSelected')}
            {role.role ? ` · ${role.role}` : ''}
          </div>
        </button>
        <button
          onClick={onRemove}
          title={t('playground.characterDialog.remove')}
          className="p-1 text-gray-300 hover:text-red-600 shrink-0"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      <p className={`text-xs line-clamp-3 ${role.goal ? 'text-gray-600' : 'text-gray-400 italic'}`}>
        {role.goal || t('playground.noGoalSet')}
      </p>

      {grants.length > 0 && (
        <p className={`text-[10px] leading-snug ${
          unbound ? 'text-amber-700' : 'text-gray-500'
        }`}>
          {[unbound ? t('playground.worldRole.unboundChip') : null, ...grants]
            .filter(Boolean).join(' · ')}
        </p>
      )}

      <div className="flex flex-wrap gap-1 mt-auto">
        {role.private_knowledge?.trim() && (
          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 text-[10px] font-medium">
            <Lock className="w-2.5 h-2.5" />{t('playground.privateKnowledgeChip')}
          </span>
        )}
        {chips.map((c) => (
          <span key={c} className="px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 text-[10px]">
            {c}
          </span>
        ))}
      </div>
    </div>
  );
}


/**
 * The whole character in one dialog.
 *
 * It edits a copy and hands it back on save, so closing a half-typed goal
 * leaves the scenario as it was — the same contract as every other dialog
 * here, and the reason the card can stay a read-only summary.
 */
function CharacterDialog({ role, isNew, agents, models, objectives, world,
                           triggered, onSave, onRemove, onClose }) {
  const { t } = useI18n();
  const [copy, setCopy] = useState(role);
  const set = (patch) => setCopy((prev) => ({ ...prev, ...patch }));

  const worldRoles = world?.roles || [];
  const cast = worldRoleFor(world, copy.role);
  // Four things this field can be: bound, nothing typed, typed and matching
  // nothing, or no roles to bind to at all. Each gets a sentence saying which
  // — and then, whichever it was, the same line the world page prints, because
  // what this character will actually start with is the question the field is
  // really being asked.
  const roleLead = cast
    ? cast.description
    : (!worldRoles.length
      ? t('playground.worldRole.noneDeclared')
      : (copy.role.trim()
        ? t('playground.worldRole.unknown')
        : t('playground.worldRole.unset')));
  // One line, not two. The sentence and what it costs are the same thought,
  // and the field they sit under is one row high — a second line pushes the
  // grid apart for a reader who was going to read both anyway.
  const roleHint = [
    roleLead,
    cast ? null : t('playground.worldRole.unboundChip'),
    ...roleGrants(world, cast, t),
  ].filter(Boolean).join(' · ');

  // Escape closes: a dialog over a form the user was already filling in must
  // not be a trap.
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const field = 'w-full text-sm border border-gray-300 rounded-md px-2 py-1.5';
  const label = 'block text-[11px] font-bold text-gray-500 uppercase mb-1';

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[88vh] flex flex-col">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <h3 className="text-sm font-bold text-gray-900">
            {isNew ? t('playground.characterDialog.new') : t('playground.characterDialog.edit')}
          </h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-700">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4 overflow-auto">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className={label}>{t('playground.characterDialog.agent')}</label>
              <select
                value={copy.agent_id} onChange={(e) => set({ agent_id: e.target.value })}
                className={field}
              >
                <option value="">{t('playground.selectAgent')}</option>
                {agents.map((a) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
              </select>
            </div>
            <div>
              <label className={label}>{t('playground.characterDialog.name')}</label>
              <input
                value={copy.name} onChange={(e) => set({ name: e.target.value })}
                placeholder={t('playground.inWorldName')} className={field}
              />
            </div>
            <div>
              {/* Where the world declares roles this is an ordinary select, and
                  looks like every other one in this dialog, because the answer
                  really is one of a known list — plus "no role", which is a
                  choice with consequences of its own rather than an empty
                  field. A value stored before the world had that role stays
                  selectable: opening a dialog must not quietly rewrite a
                  scenario, and the line underneath says what it now means.
                  Worlds that declare no roles keep a free text field — there
                  the string binds to nothing and is prose for the prompt. */}
              <label className={label}>{t('playground.characterDialog.role')}</label>
              {worldRoles.length ? (
                <Combo
                  value={copy.role}
                  options={worldRoles}
                  emptyLabel={t('playground.worldRole.none')}
                  placeholder={t('playground.worldRole.pick')}
                  onChange={(role) => set({ role })}
                  className={field}
                />
              ) : (
                <input
                  value={copy.role} onChange={(e) => set({ role: e.target.value })}
                  placeholder={t('playground.worldRole.freeText')}
                  className={field}
                />
              )}
              {roleHint && (
                <p className={`text-[11px] mt-0.5 leading-snug ${
                  worldRoles.length && !cast ? 'text-amber-700' : 'text-gray-400'
                }`}>
                  {roleHint}
                </p>
              )}
            </div>
            <div>
              {/* A combobox, not a select: the environment's own objectives are
                  offered, but a scenario may target one the env does not
                  declare yet, and that must not need a code change. */}
              <label className={label}>{t('playground.characterDialog.objective')}</label>
              <input
                list="role-dialog-objectives"
                value={copy.objective || ''}
                onChange={(e) => set({ objective: e.target.value.trim() || null })}
                placeholder={t('playground.noScoredObjective')}
                title={t('playground.scoredObjectiveOverFinalState')}
                className={field}
              />
              <datalist id="role-dialog-objectives">
                {objectives.map((o) => <option key={o} value={o} />)}
              </datalist>
            </div>
            <div>
              <label className={label}>{t('playground.characterDialog.model')}</label>
              <ModelSelect
                provider={copy.provider} model={copy.model} options={models}
                placeholder={roleInherits(agents.find((a) => a.id === copy.agent_id), t)}
                onChange={(patch) => set(patch)}
                className={field}
              />
            </div>
            <div>
              <label className={label}>{t('playground.characterDialog.memory')}</label>
              <input
                type="number" min={0} max={50} value={copy.memory_horizon ?? 0}
                onChange={(e) => set({ memory_horizon: parseInt(e.target.value, 10) || 0 })}
                className={field}
              />
              <p className="text-[11px] text-gray-400 mt-0.5">
                {t('playground.howManyPastTicksThis')}
              </p>
            </div>
          </div>

          {/* Only a triggered world has anything to do with these:
              synchronously, everyone acts every tick regardless. */}
          {triggered && (
            <div className="rounded-lg border border-gray-200 p-3">
              <div className="text-[11px] font-bold text-gray-500 uppercase mb-2">
                {t('playground.characterDialog.triggers')}
              </div>
              <div className="flex flex-wrap items-center gap-4">
                <label className="flex items-center gap-2 text-xs text-gray-600">
                  {t('playground.wakeEvery')}
                  <input
                    type="number" min={0} max={100} value={copy.wake_every ?? 0}
                    onChange={(e) => set({ wake_every: parseInt(e.target.value, 10) || 0 })}
                    title={t('playground.wakeEveryHint')}
                    className="text-sm border border-gray-300 rounded-md px-2 py-1.5 w-20"
                  />
                </label>
                <label className="flex items-center gap-2 text-xs text-gray-600">
                  <input
                    type="checkbox" checked={!!copy.starts}
                    onChange={(e) => set({ starts: e.target.checked })}
                  />
                  {t('playground.starts')}
                </label>
                {/* The opposite end of the same dial: a role that waits to be
                    reached rather than one that goes looking for a turn. */}
                <label className="flex items-center gap-2 text-xs text-gray-600">
                  <input
                    type="checkbox" checked={!!copy.npc}
                    onChange={(e) => set({ npc: e.target.checked, starts: e.target.checked ? false : copy.starts })}
                  />
                  {t('playground.npc')}
                </label>
              </div>
              <p className="text-[11px] text-gray-400 mt-2">{t('playground.startsHint')}</p>
              <p className="text-[11px] text-gray-400 mt-1">{t('playground.npcHint')}</p>
            </div>
          )}

          <div>
            <label className={label}>{t('playground.characterDialog.goal')}</label>
            <textarea
              value={copy.goal} onChange={(e) => set({ goal: e.target.value })}
              placeholder={t('playground.statedGoalWhatThisAgent')} rows={4}
              className={`${field} resize-y`}
            />
          </div>
          <div>
            <label className={label}>{t('playground.characterDialog.privateKnowledge')}</label>
            <textarea
              value={copy.private_knowledge}
              onChange={(e) => set({ private_knowledge: e.target.value })}
              placeholder={t('playground.privateKnowledgeWhatOnlyThis')} rows={4}
              className={`${field} resize-y`}
            />
          </div>
        </div>

        <div className="flex items-center justify-between gap-2 px-5 py-3 border-t border-gray-200">
          {onRemove ? (
            <button
              onClick={onRemove}
              className="inline-flex items-center text-xs font-semibold text-red-600 hover:text-red-700"
            >
              <Trash2 className="w-3.5 h-3.5 mr-1" /> {t('playground.characterDialog.remove')}
            </button>
          ) : <span />}
          <div className="flex items-center gap-2">
            <button onClick={onClose} className="px-3 py-2 text-sm font-semibold text-gray-600 hover:text-gray-900">
              {t('playground.cancel')}
            </button>
            <button
              onClick={() => onSave(copy)}
              className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
            >
              <Save className="w-4 h-4 mr-1.5" /> {t('playground.characterDialog.save')}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
