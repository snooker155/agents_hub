// The signed-in person's own preferences (dashboard/backend/routes/
// account.py, common/identity.get_preferences/set_preferences). The endpoint
// exists only under AUTH_MODE=multi, so it is not called at all without a
// session in that mode: the call is refused here with the same 404 shape the
// backend would give, and callers (src/components/theme.js,
// src/components/chat/steering.js, src/lib/modelView.js) fall back to this
// browser's localStorage copy exactly as before, without a failed request in
// the console on every page load.
import api, { getAuthMode, getSessionToken } from './index';

// The mode never changes without a backend restart (see AuthContext.jsx), so
// it is asked once per page; a failed ask is not remembered.
let multiMode = null;

const accountAvailable = async () => {
  if (!getSessionToken()) return false;
  if (!multiMode) {
    multiMode = getAuthMode().then(
      ({ data }) => data?.mode === 'multi',
      () => { multiMode = null; return false; },
    );
  }
  return multiMode;
};

const noAccount = () => Promise.reject({ response: { status: 404, data: {} } });

export const getMyPreferences = async () => (
  (await accountAvailable()) ? api.get('/auth/preferences') : noAccount()
);

export const putMyPreferences = async (preferences) => (
  (await accountAvailable()) ? api.put('/auth/preferences', preferences) : noAccount()
);
