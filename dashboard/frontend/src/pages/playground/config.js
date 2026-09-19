/**
 * The settings a simulation run actually ran with.
 *
 * A scenario is edited in place: tune it, run it again, and the live row now
 * describes the *next* run rather than any of the finished ones. So a run keeps
 * a snapshot of the scenario as it was at launch, in `run.config`, and every
 * readout about a past run — its tick cap, its cast, its ceilings — reads from
 * there rather than from the scenario on screen.
 *
 * Runs recorded before the snapshot existed have none, which is what the
 * fallback and `hasSnapshot` are for: showing the current scenario for those is
 * a guess, and the UI says so instead of passing it off as history.
 */

export function runConfig(run, scenario) {
  const snapshot = run?.config;
  if (snapshot && Object.keys(snapshot).length) return snapshot;
  return scenario || {};
}

export function hasSnapshot(run) {
  return Boolean(run?.config && Object.keys(run.config).length);
}

/** Whether the scenario has moved on since this run — the snapshot carries the
    scenario's `updated_at` from launch, so a plain comparison answers it. */
export function scenarioEditedSince(run, scenario) {
  return Boolean(
    hasSnapshot(run) && scenario?.updated_at && run.config.updated_at
    && scenario.updated_at !== run.config.updated_at,
  );
}
