import { useMemo, useState } from 'react';

/*
 * Incremental loading for the long server-side lists (runs, sessions, run
 * groups, instances): the first `pageSize` rows, then "load more" adds the
 * next `pageSize`, plus a sort field and direction. The server sorts and cuts
 * the window; this keeps the choice and turns it into `limit`/`offset`/
 * `sort`/`order` params.
 *
 * The window is always fetched from the top (`offset` 0, `limit` = rows
 * loaded so far), so a live refetch or a delete reloads exactly what is on
 * screen and never leaves a gap between batches.
 *
 * Page size and sort are remembered per list in this browser; how many
 * batches are loaded is not, and goes back to one whenever `resetOn` changes
 * (the filters), in the same render, so a filter change fetches once.
 */

export const PAGE_SIZES = [10, 20, 50, 100];
export const DEFAULT_PAGE_SIZE = 20;
// The most rows one list holds; the list endpoints accept a limit up to this.
export const MAX_LOADED = 2000;

function readStored(key) {
  try {
    const raw = window.localStorage.getItem(`listPaging:${key}`);
    return raw ? JSON.parse(raw) || {} : {};
  } catch {
    return {};
  }
}

function writeStored(key, value) {
  try {
    window.localStorage.setItem(`listPaging:${key}`, JSON.stringify(value));
  } catch { /* storage unavailable: the choice lasts until reload */ }
}

export function useListPaging(storageKey, {
  sorts, defaultSort, defaultOrder = 'desc', resetOn = [],
}) {
  const [prefs, setPrefs] = useState(() => {
    const stored = readStored(storageKey);
    return {
      pageSize: PAGE_SIZES.includes(stored.pageSize) ? stored.pageSize : DEFAULT_PAGE_SIZE,
      sort: sorts.includes(stored.sort) ? stored.sort : defaultSort,
      order: stored.order === 'asc' || stored.order === 'desc' ? stored.order : defaultOrder,
    };
  });
  const resetKey = JSON.stringify([resetOn, prefs]);
  const [batchState, setBatchState] = useState({ batches: 1, key: resetKey });
  const batches = batchState.key === resetKey ? batchState.batches : 1;

  const update = (patch) => {
    const next = { ...prefs, ...patch };
    writeStored(storageKey, next);
    setPrefs(next);
  };

  // A sort the list no longer offers (the workspace column hides in a
  // non-default workspace) falls back to the default.
  const sort = sorts.includes(prefs.sort) ? prefs.sort : defaultSort;
  const limit = Math.min(MAX_LOADED, batches * prefs.pageSize);

  return {
    pageSize: prefs.pageSize,
    setPageSize: (n) => update({ pageSize: Number(n) }),
    sort,
    setSort: (s) => update({ sort: s }),
    order: prefs.order,
    toggleOrder: () => update({ order: prefs.order === 'asc' ? 'desc' : 'asc' }),
    limit,
    loadMore: () => setBatchState({ batches: batches + 1, key: resetKey }),
    params: useMemo(() => ({ limit, offset: 0, sort, order: prefs.order }), [limit, sort, prefs.order]),
  };
}
