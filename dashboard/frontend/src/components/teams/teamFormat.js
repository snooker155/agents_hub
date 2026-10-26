/** How the team page words a run's ending and a turn's length. */

export const STOP_REASONS = ['goal_met', 'max_rounds', 'stopped', 'wall_clock', 'cost_ceiling', 'error'];

/**
 * Why a run ended, only when it says more than the status does: a run that
 * "completed" because it was stopped reads "stopped" from its badge already.
 */
export function stopReasonText(run, t) {
  const reason = run?.stop_reason;
  if (!reason || reason === run.status) return '';
  return STOP_REASONS.includes(reason) ? t(`teamDetails.stopReasons.${reason}`) : reason.replace(/_/g, ' ');
}

/** A turn's length, short: 850 ms, 12 s, 3 min. */
export function durationText(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) return '';
  if (n < 1000) return `${Math.round(n)} ms`;
  const s = Math.round(n / 1000);
  if (s < 60) return `${s} s`;
  return `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, '0')} s`;
}

/** Runs still going: queued for a process, working or being stopped. */
export const isRunLive = (status) => status === 'running' || status === 'stopping' || status === 'pending';
