/**
 * Skills catalog and installs.
 */
import api from './index';

// Skills catalog — reusable procedures owned by a workspace, publishable to the
// global catalog, installed onto agents as copies.
export const getSkills = (workspace, params = {}) =>
  api.get('/skills', { params: { workspace, ...params } });
export const getSkillTargets = (workspace) => api.get('/skills/agents', { params: { workspace } });
export const createSkill = (data) => api.post('/skills', data);
export const getSkillDetails = (id) => api.get(`/skills/${encodeURIComponent(id)}`);
export const updateSkill = (id, data) => api.patch(`/skills/${encodeURIComponent(id)}`, data);
export const updateSkillSharing = (id, shared) =>
  api.post(`/skills/${encodeURIComponent(id)}/sharing`, { shared });
export const installSkill = (id, data) => api.post(`/skills/${encodeURIComponent(id)}/install`, data);
export const deleteSkill = (id) => api.delete(`/skills/${encodeURIComponent(id)}`);
