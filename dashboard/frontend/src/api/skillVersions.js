/**
 * Skills beyond the catalog CRUD in api/index.js (fourth-cycle stage 3):
 * version history, restore, pin, update from the original, sync from the
 * workspace's .claude/skills folders, SKILL.md import and export.
 */
import api from './index';

const enc = encodeURIComponent;

// { current, pinned, versions: [{ skill_id, version, op, content_hash,
//   actor_kind, actor_id, note, at }, ...] }, newest first.
export const listSkillVersions = (skillId) => api.get(`/skills/${enc(skillId)}/versions`);

// One version with its `snapshot`: { name, description, steps, body, tags,
// resources, allowed_tools }.
export const getSkillVersion = (skillId, version) =>
  api.get(`/skills/${enc(skillId)}/versions/${version}`);

export const restoreSkillVersion = (skillId, version) =>
  api.post(`/skills/${enc(skillId)}/versions/${version}/restore`);

// `version: null` unpins: the agent follows the latest version again.
export const pinSkillVersion = (skillId, version) =>
  api.put(`/skills/${enc(skillId)}/pin`, { version });

export const updateSkillFromOrigin = (skillId) =>
  api.post(`/skills/${enc(skillId)}/update-from-origin`);

// { added, updated, unchanged, missing, followed, errors }, each a list.
export const syncSkills = (workspace, projectId) =>
  api.post('/skills/sync', { workspace, project_id: projectId || null });

export const importSkillMarkdown = (workspace, content, agentId = '') =>
  api.post('/skills/import-md', { workspace, content, agent_id: agentId });

export const exportSkillMarkdown = (skillId) =>
  api.get(`/skills/${enc(skillId)}/export`, { responseType: 'text' });
