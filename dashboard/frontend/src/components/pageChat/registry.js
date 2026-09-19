/**
 * Route → what the page chat is about.
 *
 * The panel has to work on a page that knows nothing about it, so the default
 * subject is derived from the URL alone: the entity kinds in `chat/references.py`
 * all have a page of their own, and their paths are the inverse of the
 * `path_fn` each kind declares there. A task page hands the agent that task, a
 * flow page that flow, a list page hands it nothing but its own name.
 *
 * A page that knows more than its URL does (which rows are selected, which
 * filter is on) says so with `usePageChat` instead; this table is the floor,
 * not the ceiling.
 *
 * Pure data at module scope: the panel re-derives the subject on every
 * navigation, and rebuilding thirty regexes each time would be a waste.
 */

const seg = (value) => {
  try {
    return decodeURIComponent(value || '');
  } catch {
    return value || '';
  }
};

// One entry per route that carries an entity. `scope` is the conversation key:
// the same page with the same entity reopens the same thread, a different
// entity starts its own.
const ROUTE_SUBJECTS = [
  {
    match: /^\/tasks\/(.+)$/,
    subject: (m) => ({ scope: `task:${seg(m[1])}`, refs: [{ kind: 'task', id: seg(m[1]) }] }),
  },
  {
    match: /^\/views\/(.+)$/,
    subject: (m) => ({ scope: `view:${seg(m[1])}`, refs: [{ kind: 'view', id: seg(m[1]) }] }),
  },
  {
    match: /^\/projects\/(.+)$/,
    subject: (m) => ({ scope: `project:${seg(m[1])}`, refs: [{ kind: 'project', id: seg(m[1]) }] }),
  },
  {
    match: /^\/flows\/(.+)$/,
    subject: (m) => ({ scope: `flow:${seg(m[1])}`, refs: [{ kind: 'flow', id: seg(m[1]) }] }),
  },
  // Both of these pages register a chat of their own, which the panel prefers.
  // The rules stay because this table is the floor: a page that has not
  // registered yet — or stops registering — still points at what it is showing.
  {
    match: /^\/agents\/(.+)$/,
    subject: (m) => ({ scope: `agent:${seg(m[1])}`, refs: [{ kind: 'agent', id: seg(m[1]) }] }),
  },
  {
    match: /^\/teams\/(.+)$/,
    subject: (m) => ({ scope: `team:${seg(m[1])}`, refs: [{ kind: 'team', id: seg(m[1]) }] }),
  },
  // Before the scenario rule below: a world and the run history are not
  // scenario ids, the same ordering the shell's title table needs.
  { match: /^\/playground\/runs$/, subject: () => ({ scope: 'playground-runs' }) },
  { match: /^\/playground\/worlds$/, subject: () => ({ scope: 'playground-worlds' }) },
  {
    match: /^\/playground\/([^/]+)$/,
    subject: (m) => ({ scope: `scenario:${seg(m[1])}`, refs: [{ kind: 'scenario', id: seg(m[1]) }] }),
  },
  // Two pages keep the entity in the query string rather than the path.
  {
    match: /^\/loops$/,
    subject: (m, params) => {
      const id = params.get('loop');
      return id ? { scope: `loop:${id}`, refs: [{ kind: 'loop', id }] } : { scope: 'loops' };
    },
  },
  {
    match: /^\/plan$/,
    subject: (m, params) => {
      const id = params.get('job');
      return id ? { scope: `job:${id}`, refs: [{ kind: 'job', id }] } : { scope: 'plan' };
    },
  },
];

/**
 * What the chat is about on this URL.
 *
 * @param {string} pathname
 * @param {string} [search]  the query string, where two pages keep their entity.
 * @returns {{scope: string, refs: Array<{kind: string, id: string}>}}
 */
export function describeRoute(pathname, search = '') {
  const path = pathname || '/';
  const params = new URLSearchParams(search || '');
  for (const entry of ROUTE_SUBJECTS) {
    const m = path.match(entry.match);
    if (m) {
      const subject = entry.subject(m, params) || {};
      return { scope: subject.scope || path, refs: subject.refs || [] };
    }
  }
  // Every other page is its own scope, keyed by its path: one thread per page,
  // which is what a list page or a settings page wants.
  return { scope: path === '/' ? 'chat' : path.replace(/^\//, ''), refs: [] };
}

export default describeRoute;
