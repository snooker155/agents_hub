/**
 * What the loop list and the loop page share: how a status and a score are
 * coloured, the stop reasons and the shape of a new loop.
 */

export const STATUS_STYLES = {
  pending: 'bg-gray-100 text-gray-600',
  running: 'bg-blue-100 text-blue-700',
  stopping: 'bg-amber-100 text-amber-700',
  completed: 'bg-green-100 text-green-700',
  stopped: 'bg-amber-100 text-amber-700',
  failed: 'bg-red-100 text-red-700',
};

// Reason and evaluator wording lives in the i18n namespace; these lists keep
// the order the selects render in.
export const STOP_REASONS = [
  'criterion_met', 'target_score', 'max_iterations', 'no_improvement',
  'cost_ceiling', 'wall_clock', 'stopped', 'flow_failed', 'error',
];

export const EVALUATOR_KINDS = ['final_agent', 'agent', 'model'];

export const stopReasonLabel = (reason, t) =>
  (STOP_REASONS.includes(reason) ? t(`loops.stopReasons.${reason}`) : reason);

export const isLive = (run) => run?.status === 'running' || run?.status === 'stopping' || run?.status === 'pending';

export const hasScore = (score) => score !== null && score !== undefined;

export const scoreColor = (score) => {
  if (!hasScore(score)) return 'bg-gray-300';
  if (score >= 80) return 'bg-green-500';
  if (score >= 55) return 'bg-amber-500';
  return 'bg-red-500';
};

export const scoreText = (score) => {
  if (!hasScore(score)) return 'text-gray-500';
  if (score >= 80) return 'text-green-700';
  if (score >= 55) return 'text-amber-700';
  return 'text-red-700';
};

export const emptyLoop = (workspace) => ({
  name: '', description: '', workspace: workspace || null, flow_id: '',
  exit_criterion: '', max_iterations: 5, min_iterations: 1, target_score: 80,
  patience: 2, cost_ceiling: null, max_wall_seconds: 3600,
  evaluator_mode: 'final_agent', evaluator_agent_id: null,
  rubric: '', grader: null,
});

// A loop's rubric grader as the one-line "provider/model" the form edits.
export const graderText = (g) => {
  if (!g) return '';
  if (typeof g === 'string') return g;
  return g.model ? `${g.provider ? `${g.provider}/` : ''}${g.model}` : '';
};

// An iteration graded against a rubric keeps the whole grading as JSON in
// evaluator_raw (loops/evaluator.evaluate_with_rubric); null for any other judge.
export const rubricGrading = (raw) => {
  if (!raw || raw[0] !== '{') return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && parsed.kind === 'rubric' ? parsed : null;
  } catch {
    return null;
  }
};

// A loop is a process of its own; when it dies the watchdog relaunches it,
// and a person can too. The stored position is what makes picking it up
// cheaper than starting over, so a run that never finished an iteration has
// nothing to resume from. A completed run has nothing left to do: only a
// failed or a deliberately stopped run is offered.
export const isResumable = (r) => (r.status === 'failed' || r.status === 'stopped') && !!(r.position?.iterations_done);
