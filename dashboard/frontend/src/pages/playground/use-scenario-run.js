import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getSimRun, getSimRuns, getSimTicks, startSimulation, stopSimulation, triggerSimAgent,
} from '../../api';
import { useChannel, useLiveRefetch, useStream } from '../../components/stream';
import { isLiveStatus } from './status';

/**
 * A run of the scenario, watched live: starting and stopping it, the ticks as
 * they arrive on the stream, the agents still thinking, and the run picked by
 * the URL.
 *
 * Split out of the page body as the run-watching half of the state —
 * ``useScenarioDocument`` is the other half, the stored scenario and its run
 * history. This hook reads and writes that history (``runs`` / ``setRuns``)
 * rather than owning a second copy of it: the document hook fetches it once
 * alongside the scenario, and every start, stop or finished run here only
 * ever refreshes that same list.
 */
export function useScenarioRun({
  scenarioId, runParam, navigate, scenario, selectedWorkspace,
  runs, setRuns, setMode, setMessage, setEventsSeen, t,
}) {
  const [run, setRun] = useState(null);
  const [ticks, setTicks] = useState([]);
  const [cursor, setCursor] = useState(0);          // index into ticks
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

  // The run the page has actually loaded — see the URL-follows effect below.
  const shownRunRef = useRef(null);

  // A fresh scenario has no run on screen yet, and switching scenarios must
  // not leave the previous one's tick list or in-flight bubbles on screen
  // while the new one loads. Mirrors the reset ``useScenarioDocument`` does
  // for the scenario itself, on the same dependency.
  useEffect(() => {
    setRun(null);
    setTicks([]);
    setInFlight([]);
    setActivity([]);
    setWaitingForTrigger(false);
    shownRunRef.current = null;
  }, [scenarioId]);

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
  }, [setEventsSeen, setMessage, t]);

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
  }, [scenarioId, setRuns]);

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
        if (prev.some((tk) => tk.tick === data.tick)) return prev;
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

  // Read by the catch-up below, which must not be torn down and rebuilt every
  // time a tick lands: on a world that ticks fast, a callback that depends on
  // `ticks` would be replaced faster than it is ever called.
  const ticksRef = useRef([]);
  useEffect(() => { ticksRef.current = ticks; }, [ticks]);

  // Tick catch-up, replacing what used to be a 2.5-second poll.
  //
  // Ticks themselves arrive on the `sim:<run_id>` channel subscribed above.
  // On the app channel, `sim_runs.changed` (playground/store.py `_notify`)
  // fires when a run starts, is stopped or ends — and deliberately NOT per
  // tick: that file's own comment calls invalidating the catalogue once a
  // second a refetch storm for a badge. So there is no per-tick notification
  // to subscribe to, and `fallbackMs` carries that gap at a 30-second floor:
  // a dropped channel costs a delay, not a frozen stage.
  const catchUpTicks = useCallback(async () => {
    const runId = run?.sim_run_id;
    if (!runId) return;
    try {
      const seen = ticksRef.current;
      const since = seen.length ? seen[seen.length - 1].tick : -1;
      const { data } = await getSimTicks(runId, since);
      if (data.ticks?.length) {
        setTicks((prev) => [...prev, ...data.ticks].sort((a, b) => a.tick - b.tick));
        // A tick that arrived this way rather than on the stream still ends
        // every turn it contains: otherwise a dropped connection leaves
        // bubbles spinning for agents that finished long ago.
        const newest = data.ticks[data.ticks.length - 1].tick;
        setInFlight((prev) => prev.filter((d) => d.tick > newest));
        setActivity((prev) => prev.filter((a) => a.tick > newest));
      }
      // Without the tick list: `data.ticks` is only what arrived since the
      // last read, and folding it into the run would leave a `ticks` field on
      // it that disagrees with the page's own.
      const { ticks: _fetched, ...meta } = data;
      setRun((prev) => ({ ...prev, ...meta }));
    } catch { /* transient */ }
  }, [run?.sim_run_id]);

  useLiveRefetch(catchUpTicks, {
    type: 'sim_runs.changed',
    enabled: Boolean(run?.sim_run_id) && isLiveStatus(run?.status),
    fallbackMs: 30000,
  });

  // A reconnect that could not resume, or events dropped because this tab fell
  // behind, leaves the stage and the run list holding whatever they had.
  const { onRefetch } = useStream();
  useEffect(() => onRefetch(() => { catchUpTicks(); refreshRuns(); }),
    [onRefetch, catchUpTicks, refreshRuns]);

  // Following pins the scrubber to the newest tick until the user scrubs back.
  useEffect(() => {
    if (following && ticks.length) setCursor(ticks.length - 1);
  }, [ticks.length, following]);

  const handleStart = useCallback(async () => {
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
  }, [scenario, selectedWorkspace, setMessage, setMode, navigate, refreshRuns, t]);

  const handleStop = useCallback(async () => {
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
  }, [run?.sim_run_id]);

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
  const handleTrigger = useCallback(async (agent, text) => {
    if (!run?.sim_run_id) return false;
    try {
      await triggerSimAgent(run.sim_run_id, agent, text);
      setMessage('');
      return true;
    } catch (e) {
      setMessage(e.response?.data?.detail || t('playground.triggerFailed'));
      return false;
    }
  }, [run?.sim_run_id, setMessage, t]);

  return {
    run, ticks, cursor, setCursor, following, setFollowing,
    inFlight, activity, waitingForTrigger, starting, refreshing,
    openRun, refreshRuns, handleStart, handleStop, handleRefresh, handleTrigger,
  };
}

export default useScenarioRun;
