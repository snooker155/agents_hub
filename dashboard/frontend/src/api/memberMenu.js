/**
 * The sidebar rows an administrator hides from members (routes/settings.py).
 * `hidden: null` on the PUT goes back to the default.
 */
import api from './index';

export const getMemberMenu = () => api.get('/settings/member-menu');
export const setMemberMenu = (hidden) => api.put('/settings/member-menu', { hidden });
