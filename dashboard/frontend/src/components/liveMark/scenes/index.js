import { mountSearch } from './search';
import { mountWork } from './work';

/*
 * The live mark's scene states: searches (search.js) and making or running
 * things (work.js), drawn after the "Orbit Loader" artifact. A scene starts
 * as the mark, works for as long as its state lasts and folds back into the
 * mark when the state changes; the mark is where LiveMark hands over to the
 * next state. One search hands over to the next without the mark: the lens
 * stays and only the field under it changes.
 */

const SEARCH = {
  'search-web': 'web',
  'search-memory': 'memory',
  'search-kb': 'kb',
  'search-files': 'files',
  'search-doc': 'doc',
  'search-code': 'code',
  'search-db': 'db',
  'search-mail': 'mail',
  'search-tools': 'tools',
  'search-history': 'history',
  'search-errors': 'errors',
};

const WORK = {
  'make-view': 'view',
  'make-3d': 'gen3d',
  'make-html': 'html',
  'write-code': 'code',
  'write-file': 'file',
  'make-flow': 'flowmake',
  'run-flow': 'flowrun',
  'make-team': 'teammake',
  'run-team': 'teamrun',
  'make-agent': 'agentmake',
  delegate: 'delegate',
};

export const SEARCH_STATES = Object.keys(SEARCH);
export const WORK_STATES = Object.keys(WORK);
export const SCENE_STATES = [...SEARCH_STATES, ...WORK_STATES];

export function isScene(state) {
  return Object.prototype.hasOwnProperty.call(SEARCH, state)
    || Object.prototype.hasOwnProperty.call(WORK, state);
}

/**
 * Mount the scene for `state` into the SVG group `g`. The controller's
 * update(dt, next) takes the state now wanted and returns 'done' once the
 * scene has folded back into the mark; accepts(next) tells whether this
 * scene carries on into `next` (another search does) rather than folding.
 */
export function mountScene(state, g) {
  const ctl = SEARCH[state] ? mountSearch(g, SEARCH[state])
    : WORK[state] ? mountWork(g, WORK[state]) : null;
  if (!ctl) return null;
  // names go through this scene's own table: search-code and write-code
  // are both 'code' to their scenes
  const table = SEARCH[state] ? SEARCH : WORK;
  const kind = (name) => (Object.prototype.hasOwnProperty.call(table, name) ? table[name] : null);
  return {
    accepts: (next) => !!kind(next) && ctl.accepts(kind(next)),
    update: (dt, next) => ctl.update(dt, kind(next)),
    get phase() { return ctl.phase; },
    destroy: () => ctl.destroy(),
  };
}
