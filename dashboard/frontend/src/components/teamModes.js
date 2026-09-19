/**
 * How a team's three modes are named and explained in the UI.
 *
 * Shared by the teams list and the team page so the mode a card shows and the
 * mode the editor explains are never two different sentences. The wording
 * itself lives in the `teams.modes.*` i18n namespace — these helpers take the
 * caller's `t` so both screens resolve it the same way.
 */
export const MODES = ['centralized', 'autonomous', 'parallel'];

export const modeLabel = (mode, t) =>
  (MODES.includes(mode) ? t(`teams.modes.${mode}.label`) : mode);

export const modeSummary = (mode, t) =>
  (MODES.includes(mode) ? t(`teams.modes.${mode}.summary`) : '');

export const modeHelp = (mode, t) =>
  (MODES.includes(mode) ? t(`teams.modes.${mode}.help`) : '');

export const modeOptions = (t) =>
  MODES.map((value) => ({ value, label: t(`teams.modes.${value}.option`) }));

export const MODE_BADGE = {
  centralized: 'bg-amber-50 text-amber-700 border-amber-200',
  autonomous: 'bg-indigo-50 text-indigo-700 border-indigo-200',
  parallel: 'bg-sky-50 text-sky-700 border-sky-200',
};

export const STATUS_STYLES = {
  running: 'bg-blue-100 text-blue-700',
  stopping: 'bg-amber-100 text-amber-700',
  completed: 'bg-green-100 text-green-700',
  stopped: 'bg-amber-100 text-amber-700',
  failed: 'bg-red-100 text-red-700',
};

export const isLive = (status) => status === 'running' || status === 'stopping';
