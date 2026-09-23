import { useCallback, useEffect, useState } from 'react';
import {
  getScenario, getSimRuns, updateScenario, deleteScenario, estimateScenario,
} from '../../api';

/**
 * The scenario itself: loading it (with its run history, to decide which mode
 * the page opens on), saving it, deleting it, pricing it.
 *
 * Split out of the page body because this is the half of the state that is
 * about the *document* — the stored scenario and what it costs to run — as
 * opposed to ``useScenarioRun``, which is about a run of it in progress. The
 * two still meet at one place: this hook owns the run history list, because
 * it is fetched alongside the scenario in the same request pair and used to
 * pick the opening mode; ``useScenarioRun`` reads and refreshes it from here.
 */
export function useScenarioDocument({ scenarioId, navigate, setMode, setMessage, t }) {
  const [scenario, setScenario] = useState(null);
  const [loading, setLoading] = useState(true);
  const [missing, setMissing] = useState(false);
  const [runs, setRuns] = useState([]);
  const [estimate, setEstimate] = useState(null);

  // Reloading on scenarioId keeps the page honest when navigated to directly or
  // when the id changes underneath us.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setMissing(false);
    setMessage('');
    setEstimate(null);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scenarioId]);

  const handleEstimate = useCallback(async () => {
    try {
      const { data } = await estimateScenario(scenario.scenario_id);
      setEstimate(data);
    } catch (e) {
      setMessage(e.response?.data?.detail || t('playground.estimateFailed'));
    }
  }, [scenario, setMessage, t]);

  // Renaming is a write of the *stored* scenario, not of the setup form's
  // draft: the header is on screen in every mode, including the two that have
  // no draft to save. The empty name the backend refuses is refused here too,
  // so a cleared field reverts instead of erroring.
  const handleRename = useCallback(async (name) => {
    if (!name.trim() || name === scenario.name) return;
    try {
      const { data } = await updateScenario(scenario.scenario_id, { ...scenario, name });
      setScenario(data);
    } catch (e) {
      setMessage(e.response?.data?.detail || t('playground.saveFailed'));
    }
  }, [scenario, setMessage, t]);

  const handleDelete = useCallback(async () => {
    try {
      await deleteScenario(scenario.scenario_id);
      navigate('/playground');
    } catch (e) {
      setMessage(e.response?.data?.detail || t('playground.deleteFailed'));
    }
  }, [scenario, navigate, setMessage, t]);

  return {
    scenario, setScenario, loading, missing, runs, setRuns,
    estimate, setEstimate, handleEstimate, handleRename, handleDelete,
  };
}

export default useScenarioDocument;
