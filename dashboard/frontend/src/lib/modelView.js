// Whether the model structure page opens in 2D (reactflow) or 3D (three).
// Kept in this browser's localStorage always, and in the signed-in person's
// preferences (`model_view` on /api/auth/preferences) when that endpoint
// exists: it 404s outside AUTH_MODE=multi, so the local copy is the fallback,
// the same rule src/components/theme.js follows for the palette.
import { getMyPreferences, putMyPreferences } from '../api/palette';

export const MODEL_VIEW_KEY = 'agents_hub_model_view';
export const MODEL_VIEWS = ['2d', '3d'];
export const DEFAULT_MODEL_VIEW = '2d';

const valid = (v) => (MODEL_VIEWS.includes(v) ? v : null);

export function readLocalModelView() {
  try {
    return valid(window.localStorage.getItem(MODEL_VIEW_KEY)) || DEFAULT_MODEL_VIEW;
  } catch {
    return DEFAULT_MODEL_VIEW;
  }
}

/** The account's preference when there is one, else this browser's. */
export async function loadModelView() {
  try {
    const { data } = await getMyPreferences();
    const fromAccount = valid(data?.model_view);
    if (fromAccount) return fromAccount;
  } catch {
    // 404 outside multi mode, 401 signed out, or offline: use the local copy.
  }
  return readLocalModelView();
}

/** Write locally at once; tell the account in the background, ignoring a 404. */
export function saveModelView(view) {
  const v = valid(view);
  if (!v) return;
  try { window.localStorage.setItem(MODEL_VIEW_KEY, v); } catch { /* storage blocked */ }
  try {
    Promise.resolve(putMyPreferences({ model_view: v })).catch(() => {});
  } catch { /* no api in this context */ }
}
