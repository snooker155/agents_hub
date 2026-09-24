// The signed-in person's own palette preference (dashboard/backend/routes/
// account.py, common/identity.get_preferences/set_preferences). 404 outside
// AUTH_MODE=multi, the same "these endpoints do not exist" rule every other
// route in routes/account.py follows — see src/components/theme.js, which
// falls back to this browser's own localStorage copy whenever this 404s or
// there is no session to ask with.
import api from './index';

export const getMyPreferences = () => api.get('/auth/preferences');
export const putMyPreferences = (preferences) => api.put('/auth/preferences', preferences);
