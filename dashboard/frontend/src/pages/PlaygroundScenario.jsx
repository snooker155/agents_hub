import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Link, useParams, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Gamepad2, Play, Square, Trash2, Loader, X,
  AlertTriangle, DollarSign, RefreshCw, History,
} from 'lucide-react';
import { getSimEnvironments, getAgents, getModelsCatalog } from '../api';
import { useWorkspace } from '../components/workspace';
import { usePageChat } from '../components/pageChat/pageChat';
import { RunHistory } from './playground/history';
import { eventLines } from './playground/events';
import { isLiveStatus } from './playground/status';
import { useScenarioChatDescriptor } from './playground/use-scenario-chat';
import { useScenarioDocument } from './playground/use-scenario-document';
import { useScenarioRun } from './playground/use-scenario-run';
import { SetupPanel } from './playground/setup-panel';
import { ScenarioSidebar } from './playground/scenario-sidebar';
import { RunTransport } from './playground/run-transport';
import { RunView, TWO_COLUMNS } from './playground/run-view';

import { PageContainer, PageHeader } from '../components/PageLayout';
import InlineEdit from '../components/InlineEdit';
import { useI18n } from '../i18n';
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
 *
 * This file is the shell: it wires the URL, the two halves of the state
 * (``useScenarioDocument`` for the stored scenario, ``useScenarioRun`` for a
 * run of it in progress), the build chat, and the page's own layout. Every
 * card and dialog on it is a component from ``./playground``.
 */
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
  const [mode, setMode] = useState('watch');       // 'setup' | 'watch' | 'history'
  // Which of the two readings of the same run is on screen. They share one
  // column because they are two views of the run, not two things to watch at
  // once — the world's log next to the dialogue only pulled the eye off it.
  // 'transcript' | 'events' | 'story' | 'stage' | 'narrative'
  const [pane, setPane] = useState('transcript');
  // The sidebar carries two things now: what the scenario *is* (the readouts)
  // and a chat that can change it. Tabs rather than stacking, because both want
  // the full height of the column and only one of them is ever being read.
  const [sideTab, setSideTab] = useState('params');  // 'params' | 'chat'
  const [eventsSeen, setEventsSeen] = useState(0);
  const [message, setMessage] = useState('');
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

  const {
    scenario, setScenario, loading, missing, runs, setRuns, estimate, setEstimate,
    handleEstimate, handleRename, handleDelete,
  } = useScenarioDocument({ scenarioId, navigate, setMode, setMessage, t });

  const {
    run, ticks, cursor, setCursor, following, setFollowing, inFlight, activity,
    waitingForTrigger, starting, refreshing, openRun,
    handleStart, handleStop, handleRefresh, handleTrigger,
  } = useScenarioRun({
    scenarioId, runParam, navigate, scenario, selectedWorkspace,
    runs, setRuns, setMode, setMessage, setEventsSeen, t,
  });

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
  // read, so a tick landing under your eyes does not badge itself. Carried
  // over unchanged from before this file was split: the badge itself is
  // forced to zero while this pane is open (see RunView), so what matters is
  // only that eventsSeen catches up to eventCount by the time the pane is
  // left, not the timing of the effect in between.
  useEffect(() => {
    if (pane === 'events') setEventsSeen(eventCount); // eslint-disable-line react-hooks/set-state-in-effect
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
          <RunTransport
            estimate={estimate}
            onDismissEstimate={() => setEstimate(null)}
            run={run}
            live={live}
            ticks={ticks}
            cursor={cursor}
            setCursor={setCursor}
            following={following}
            setFollowing={setFollowing}
            currentTick={currentTick}
            runs={runs}
            onSelectRun={openRun}
            onOpenHistory={() => setMode('history')}
          />
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
          <RunView
            pane={pane} setPane={setPane}
            ticks={ticks} cursor={cursor}
            scenario={scenario} activity={activity} inFlight={inFlight}
            following={following} run={run} roleNames={roleNames}
            onTrigger={handleTrigger} live={live}
            waitingForTrigger={waitingForTrigger}
            eventCount={eventCount} eventsSeen={eventsSeen}
            onAskAgent={askAgent} onScenarioSaved={(saved) => setScenario(saved)}
            activation={activation} currentTick={currentTick}
            sideTab={sideTab} onSideTab={setSideTab}
            envSpec={envSpec} chat={scenarioChat}
          />
        )}
      </div>
    </PageContainer>
  );
}
