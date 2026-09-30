/**
 * Where a run's `task_id` points. Only a task run's id is a task: a chat run
 * keeps its conversation id in the same field, and linking that to /tasks
 * opened a task page for something that is not a task.
 */
export function runSource(run) {
  if (!run?.task_id) return null;
  if (run.session_type === 'task') return { kind: 'task', to: `/tasks/${run.task_id}` };
  if (run.session_type === 'chat') return { kind: 'chat', to: `/chat/${run.task_id}` };
  return null;
}

export function duration(started, finished) {
  if (!started) return '—';
  const end = finished ? new Date(finished) : new Date();
  const secs = Math.max(0, Math.round((end - new Date(started)) / 1000));
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  return `${mins}m ${secs % 60}s`;
}
