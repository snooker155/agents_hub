// A view's owner (views/owner.py): the run that made it, and where that run's
// own page lives. Shared by the view's page and the Artifacts panel.

// Where each owner kind's own page lives (App.jsx routes). "run" is an agent
// run (a row in `runs`, MessageDetails' route); the rest are entity runs
// (common/entity_runs.py), whose own page takes the entity's id, not the
// run's, so it needs `owner.entity_id` (dashboard/backend/routes/views.py
// resolves it). Every entity page takes the run in its `?run=` parameter
// (the loops page also names the loop with `?loop=`).
export const OWNER_ROUTE = {
  run: (o) => `/messages/${o.id}`,
  team: (o) => (o.entity_id ? `/teams/${o.entity_id}?run=${o.id}` : null),
  flow: (o) => (o.entity_id ? `/flows/${o.entity_id}?run=${o.id}` : null),
  scenario: (o) => (o.entity_id ? `/playground/${o.entity_id}?run=${o.id}` : null),
  loop: (o) => (o.entity_id ? `/loops?loop=${o.entity_id}&run=${o.id}` : '/loops'),
};

// A view's owner as the backend returns it, falling back to the older
// `run_id`-only shape for a view fetched before this field existed.
export function resolveOwner(view) {
  if (view?.owner?.kind && view?.owner?.id) return view.owner;
  if (view?.run_id) return { kind: 'run', id: view.run_id };
  return null;
}

/** `{to, label}` for the owner's page, `to` null when it has none; null without an owner. */
export function ownerLink(view) {
  const owner = resolveOwner(view);
  if (!owner) return null;
  return { to: OWNER_ROUTE[owner.kind]?.(owner) || null, label: `${owner.kind} · ${owner.id}` };
}
