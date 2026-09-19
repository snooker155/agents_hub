/**
 * What a simulation run's status means to the pages that watch one.
 *
 * A run is live from the moment its row is written — "starting" — not from its
 * first tick: the first tick is as slow as the slowest agent in it, which is a
 * minute of model calls, and a page that only counts "running" as live spends
 * that minute looking like nothing was launched.
 */

/** Statuses a run can hold while it is still going. */
export const LIVE_STATUSES = ['starting', 'running', 'stopping'];

/** Is this status one the run is still moving under? */
export const isLiveStatus = (status) => LIVE_STATUSES.includes(status);

/** The same question asked of a run row. */
export const isLiveRun = (run) => isLiveStatus(run?.status);
