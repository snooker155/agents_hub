/**
 * The Skills tab's own state: what this agent has been taught in the active
 * workspace, and adding or removing one.
 *
 * Skills are workspace-scoped, so everything here is keyed on the workspace as
 * well as the agent, and nothing is fetched until the tab is opened.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  createAgentSkill, deleteAgentSkill, getAgentSkills, updateAgentSkillsConfig,
} from '../../api';
import { errorDetail } from '../toast';

export function useAgentSkills({ id, agent, activeTab, selectedWorkspace, t, toast }) {
  // ── Skills state ────────────────────────────────────────────────────────────
  const [skills, setSkills] = useState([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [skillsEnabled, setSkillsEnabled] = useState(false);
  const [skillsConfigSaving, setSkillsConfigSaving] = useState(false);
  const [showAddSkill, setShowAddSkill] = useState(false);
  const [skillForm, setSkillForm] = useState({ name: '', description: '', steps: '', tags: '' });
  const [skillSaving, setSkillSaving] = useState(false);
  const [skillDeleteBusy, setSkillDeleteBusy] = useState({});
  const [skillsMessage, setSkillsMessage] = useState('');


  const fetchSkills = useCallback(async (wsName) => {
    if (!wsName) return;
    setSkillsLoading(true);
    try {
      const resp = await getAgentSkills(id, wsName);
      setSkills(resp.data || []);
    } catch {
      setSkills([]);
    }
    setSkillsLoading(false);
  }, [id]);

  const handleToggleSkillsEnabled = async (enabled) => {
    setSkillsConfigSaving(true);
    try {
      await updateAgentSkillsConfig(id, { skills_enabled: enabled });
      setSkillsEnabled(enabled);
    } catch (e) {
      toast.error(t('agentDetails.errors.skillsConfig'), errorDetail(e));
    }
    setSkillsConfigSaving(false);
  };

  const handleSaveSkill = async () => {
    const wsName = selectedWorkspace;
    if (!wsName || !skillForm.name || !skillForm.description || !skillForm.steps.trim()) return;
    setSkillSaving(true);
    try {
      const steps = skillForm.steps.split('\n').map(s => s.trim()).filter(Boolean);
      const tags = skillForm.tags ? skillForm.tags.split(',').map(t => t.trim()).filter(Boolean) : [];
      await createAgentSkill(id, { workspace: wsName, name: skillForm.name, description: skillForm.description, steps, tags });
      setSkillForm({ name: '', description: '', steps: '', tags: '' });
      setShowAddSkill(false);
      await fetchSkills(wsName);
      setSkillsMessage(t('agentDetails.skillSaved'));
      setTimeout(() => setSkillsMessage(''), 3000);
    } catch (e) {
      toast.error(t('agentDetails.errors.saveSkill'), errorDetail(e));
    }
    setSkillSaving(false);
  };

  const handleDeleteSkill = async (skillId) => {
    const wsName = selectedWorkspace;
    if (!wsName) return;
    setSkillDeleteBusy(b => ({ ...b, [skillId]: true }));
    try {
      await deleteAgentSkill(id, skillId, wsName);
      setSkills(prev => prev.filter(s => s.id !== skillId));
    } catch (e) {
      toast.error(t('agentDetails.errors.deleteSkill'), errorDetail(e));
    }
    setSkillDeleteBusy(b => ({ ...b, [skillId]: false }));
  };


  // Load the catalogue when the tab opens.
  //
  // On a microtask rather than straight from the effect body: `fetchSkills`
  // raises its loading flag as it starts, and a setState made synchronously
  // inside an effect costs an extra render pass for nothing. The delay is one
  // tick, ahead of the network call it precedes either way.
  useEffect(() => {
    if (activeTab !== 'skills') return undefined;
    let cancelled = false;
    queueMicrotask(() => { if (!cancelled) fetchSkills(selectedWorkspace); });
    return () => { cancelled = true; };
  }, [activeTab, fetchSkills, selectedWorkspace]);

  // The switch itself is part of the agent spec, not of the catalogue, so it
  // follows whatever the page last loaded. Deferred for the same reason.
  useEffect(() => {
    if (!agent) return undefined;
    let cancelled = false;
    queueMicrotask(() => { if (!cancelled) setSkillsEnabled(!!agent.skills_enabled); });
    return () => { cancelled = true; };
  }, [agent]);

  return {
    skills, skillsLoading, skillsEnabled, skillsConfigSaving,
    showAddSkill, setShowAddSkill, skillForm, setSkillForm,
    skillSaving, skillDeleteBusy, skillsMessage,
    fetchSkills, handleToggleSkillsEnabled, handleSaveSkill, handleDeleteSkill,
  };
}

export default useAgentSkills;
