/**
 * Batch eval runs (evals/batch.py): cancel a run's open provider batches, or
 * check them now instead of at the next scheduler tick. The run itself is
 * read with getEvalRun, which carries `batch` for a batch run.
 */
import api from './index';

export const cancelEvalRun = (runId) => api.post(`/eval-runs/${runId}/cancel`);

export const pollEvalRun = (runId) => api.post(`/eval-runs/${runId}/poll`, null, { timeout: 0 });
