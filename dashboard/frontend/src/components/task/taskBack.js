/*
 * Where a task page's back link leads. A task opened from a project's task
 * board carries `{ backTo: { path } }` in the router state, so its
 * back link returns to that board instead of the Tasks page; the task page
 * hands the same state on to the tasks it links to (subtasks, parent,
 * dependencies), so the way back survives walking the tree.
 */
export const projectTasksBack = (projectId) => (projectId
  ? { backTo: { path: `/projects/${projectId}?tab=Tasks` } }
  : undefined);

/** The router state to pass on from a page that was itself opened with one. */
export const forwardBack = (location) => (location?.state?.backTo ? { backTo: location.state.backTo } : undefined);
