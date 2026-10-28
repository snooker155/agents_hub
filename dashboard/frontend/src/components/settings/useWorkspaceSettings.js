// The state behind WorkspaceSettingsSections.jsx: one workspace's settings
// overrides, the global values they fall back to, its tool policy, and the
// provider connection tests. Used by the Settings page and by the Settings tab
// of a workspace.
import { useState, useEffect, useCallback, useRef } from 'react';
import {
  getSettings, updateSettings, testProvider as postTestProvider, testLocalModel,
  getWorkspaceSettingsOverrides, updateWorkspaceSettingsOverrides,
  getWorkspacePolicy, updateWorkspacePolicy, importWorkspaceHooksFile,
} from '../../api';
import { useI18n } from '../../i18n';
import { patchWorkspaceSummary } from '../../api/workspaceSummary';

// The override fields the workspace Save writes. Anything else in the bag
// (palette, web domains) has its own editor and is left as it is.
const OVERRIDE_FIELDS = [
  'openai_base_url', 'openai_api_key', 'anthropic_api_key', 'google_api_key',
  'ollama_base_url', 'lmstudio_base_url',
  'langfuse_secret_key', 'langfuse_public_key', 'langfuse_base_url',
  'orch_log_level',
  'rag_vector_db', 'rag_vector_db_url', 'rag_vector_db_api_key', 'rag_vector_db_collection',
  'rag_embedding_provider', 'rag_embedding_model', 'rag_embedding_api_key', 'rag_embedding_base_url',
  'agent_mode', 'agent_docker_image', 'agent_docker_network', 'agent_docker_extra_args',
  'task_assignment_mode',
];

export function useWorkspaceSettings(workspace) {
  const { t } = useI18n();

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  // What saving a first provider key switched on (the Models page changes it).
  const [defaultModels, setDefaultModels] = useState([]);
  const [error, setError] = useState('');

  const [globalSettings, setGlobalSettings] = useState({});
  const [envDefinedFields, setEnvDefinedFields] = useState([]);
  const [masked, setMasked] = useState({});
  const [wsOverrides, setWsOverrides] = useState({});
  // The overrides as last loaded, so the Save button shows only once a field
  // differs from what is stored.
  const [savedOverrides, setSavedOverrides] = useState({});
  // Every other change on the page waits for the same Save button too: the
  // machine-wide .env settings (guard, code runner, streaming) and the
  // workspace policy (approval, auto mode, timeouts, hooks) as patches over
  // what is stored, and the blocks with a form of their own as registered
  // savers (settingsDraft.js). Nothing is written until Save.
  const [globalPatch, setGlobalPatch] = useState({});
  const [policyPatch, setPolicyPatch] = useState({});
  const [drafts, setDrafts] = useState({});
  const draftsRef = useRef({});
  // Which kind of change the last Save wrote, for the message above the page.
  const [savedScope, setSavedScope] = useState('workspace');

  // The workspace's tool policy lives outside the override bag: the approval
  // gate and the hooks are read live by the agent process, so they get their
  // own endpoint and their own save rather than riding on the Save button.
  const [policy, setPolicy] = useState({ require_tool_approval: false, hooks: {} });
  const [hooksText, setHooksText] = useState('{}');
  const [hooksError, setHooksError] = useState('');
  const [policySaving, setPolicySaving] = useState(false);
  const [policySaved, setPolicySaved] = useState(false);

  const [fetchErrors, setFetchErrors] = useState({ ollama: '', lmstudio: '' });
  const [providerStatus, setProviderStatus] = useState({});
  const [providerTesting, setProviderTesting] = useState({});
  // Token streaming is a global (.env) switch, not a workspace override: it
  // changes how every agent process on this machine builds its LLM.

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [globalResp, overridesResp, policyResp] = await Promise.all([
        getSettings(),
        getWorkspaceSettingsOverrides(workspace),
        // A workspace that predates the policy endpoint simply has none yet.
        getWorkspacePolicy(workspace).catch(() => null),
      ]);

      const data = globalResp.data;
      setGlobalSettings(data);
      setEnvDefinedFields(data.env_defined_fields || []);
      setMasked({
        openai_api_key_masked: data.openai_api_key_masked,
        anthropic_api_key_masked: data.anthropic_api_key_masked,
        google_api_key_masked: data.google_api_key_masked,
        langfuse_secret_key_masked: data.langfuse_secret_key_masked,
        langfuse_public_key_masked: data.langfuse_public_key_masked,
        rag_vector_db_api_key_masked: data.rag_vector_db_api_key_masked,
        rag_embedding_api_key_masked: data.rag_embedding_api_key_masked,
      });

      const overrides = overridesResp?.data?.overrides || {};
      setWsOverrides(overrides);
      setSavedOverrides(overrides);

      const loadedPolicy = policyResp?.data || { require_tool_approval: false, hooks: {} };
      setPolicy(loadedPolicy);
      setHooksText(JSON.stringify(loadedPolicy.hooks || {}, null, 2));
      setHooksError('');
    } catch (e) {
      setError(`${t('settings.errors.load')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }, [t, workspace]);

  useEffect(() => { load(); }, [load]);

  const hasOverrideField = (field) => Object.prototype.hasOwnProperty.call(wsOverrides || {}, field);
  const getFieldValue = (field, fallback = '') => (
    hasOverrideField(field) ? (wsOverrides[field] ?? '') : (globalSettings[field] ?? fallback)
  );
  const setG = (field, value) => setWsOverrides(prev => ({ ...prev, [field]: value }));
  // Compared the way Save writes them: an empty field is no override.
  const overridesDirty = OVERRIDE_FIELDS.some((k) => (wsOverrides[k] || '') !== (savedOverrides[k] || ''));
  const hooksDirty = hooksText.trim() !== JSON.stringify(policy.hooks || {}, null, 2).trim();
  const hasOtherChanges = hooksDirty || Object.keys(globalPatch).length > 0
    || Object.keys(policyPatch).length > 0 || Object.keys(drafts).length > 0;
  const dirty = overridesDirty || hasOtherChanges;

  // A staged value equal to the stored one is no change, so switching a
  // toggle back hides the Save button again.
  const stageGlobal = (patch) => setGlobalPatch((prev) => {
    const next = { ...prev };
    Object.entries(patch).forEach(([k, v]) => {
      if (v === globalSettings[k]) delete next[k]; else next[k] = v;
    });
    return next;
  });
  const stagePolicy = (patch) => setPolicyPatch((prev) => {
    const next = { ...prev };
    Object.entries(patch).forEach(([k, v]) => {
      if (v === policy[k]) delete next[k]; else next[k] = v;
    });
    return next;
  });
  const setDraft = useCallback((key, save) => {
    if (save) draftsRef.current[key] = save; else delete draftsRef.current[key];
    setDrafts((prev) => {
      if (Boolean(prev[key]) === Boolean(save)) return prev;
      const next = { ...prev };
      if (save) next[key] = true; else delete next[key];
      return next;
    });
  }, []);

  const save = async () => {
    let hooks;
    if (hooksDirty) {
      try {
        hooks = hooksText.trim() ? JSON.parse(hooksText) : {};
      } catch {
        setHooksError(t('settings.hooksInvalidJson'));
        return;
      }
      if (typeof hooks !== 'object' || Array.isArray(hooks) || hooks === null) {
        setHooksError(t('settings.hooksMustBeObject'));
        return;
      }
    }
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      if (overridesDirty) {
        const payload = {};
        OVERRIDE_FIELDS.forEach((k) => { if (wsOverrides[k]) payload[k] = wsOverrides[k]; });
        const { data: savedData } = await updateWorkspaceSettingsOverrides(workspace, payload);
        setDefaultModels(savedData?.default_models || []);
      }
      if (Object.keys(globalPatch).length) {
        await updateSettings(globalPatch);
      }
      const policyBody = hooksDirty ? { ...policyPatch, hooks } : policyPatch;
      if (Object.keys(policyBody).length) {
        await updateWorkspacePolicy(workspace, policyBody);
        if ('allow_auto_approve' in policyBody) {
          patchWorkspaceSummary(workspace, { auto_approve_allowed: policyBody.allow_auto_approve });
        }
      }
      let allStored = true;
      for (const saveDraft of Object.values({ ...draftsRef.current })) {
        // Each block shows its own error and keeps its edits when it fails.
        if (!(await saveDraft())) allStored = false;
      }
      setSavedScope(overridesDirty && !Object.keys(globalPatch).length && !Object.keys(policyBody).length
        && !Object.keys(drafts).length ? 'workspace' : 'page');
      setGlobalPatch({});
      setPolicyPatch({});
      await load();
      if (allStored) {
        setSaved(true);
        setTimeout(() => setSaved(false), 3000);
      }
    } catch (e) {
      setError(`${t('settings.errors.save')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setSaving(false);
    }
  };

  // Where a chat turn runs is a machine-wide .env setting like streaming:
  // saved on change, not with the workspace Save.
  const [chatExecutionSaving, setChatExecutionSaving] = useState(false);
  const setChatExecution = async (next) => {
    setChatExecutionSaving(true);
    setError('');
    try {
      await updateSettings({ chat_execution: next });
      setGlobalSettings(s => ({ ...s, chat_execution: next }));
    } catch (e) {
      setError(`${t('settings.errors.save')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setChatExecutionSaving(false);
    }
  };

  // The egress proxy (environments/egress.py): a machine-wide .env setting
  // saved on change; the backend starts or stops its listener at once.
  const [egressSaving, setEgressSaving] = useState(false);
  const setEgressProxy = async (next) => {
    setEgressSaving(true);
    setError('');
    try {
      const { data } = await updateSettings({ egress_proxy: next });
      setGlobalSettings(s => ({ ...s, egress_proxy: next, egress_proxy_running: !!data?.egress_proxy_running }));
      if (data?.egress_proxy_error) setError(`${t('settings.egressProxyError')}: ${data.egress_proxy_error}`);
    } catch (e) {
      setError(`${t('settings.errors.save')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setEgressSaving(false);
    }
  };

  // Tool capability guard (agents/capability_guard.py): block | warn | off,
  // and whether a per-agent override needs a no-network container to count.
  // Machine-wide .env settings, applied live on the next save or build.
  const setCapabilityGuardMode = (mode) => stageGlobal({ capability_guard: mode });
  const toggleOverrideRequiresContainer = (next) => stageGlobal({ capability_override_requires_container: next });

  // Code execution (tools/run_code.py): the sandbox provider and what to do
  // when docker is down. Machine-wide .env settings that the backend also
  // changes in its running process, so the next run uses them at once.
  const setCodeRunnerProvider = (provider) => stageGlobal({ code_runner_provider: provider });
  const toggleCodeRunnerFallback = (next) => stageGlobal({ code_runner_fallback: next ? 'local' : 'none' });

  // Web access (tools/web.py): the search provider, key and result count
  // behind web_search, the fetch limits and the domain policy. Machine-wide
  // .env settings, read live by every process.
  const [webAccessSaving, setWebAccessSaving] = useState(false);
  const saveWebAccess = async (patch) => {
    setWebAccessSaving(true);
    setError('');
    try {
      await updateSettings(patch);
      const { data } = await getSettings();
      setGlobalSettings(data);
      return true;
    } catch (e) {
      setError(`${t('settings.errors.webAccessSave')}: ` + (e.response?.data?.detail || e.message));
      return false;
    } finally {
      setWebAccessSaving(false);
    }
  };

  // The global temperature (LLM_TEMPERATURE): what a model without its own on
  // the Models page runs at, unless the agent sets one. Machine-wide, written
  // to .env and applied in the running backend at once.
  const [temperatureSaving, setTemperatureSaving] = useState(false);
  const saveGlobalTemperature = async (value) => {
    setTemperatureSaving(true);
    setError('');
    try {
      await updateSettings({ temperature: value });
      setGlobalSettings(s => ({ ...s, temperature: value }));
      return true;
    } catch (e) {
      setError(`${t('settings.errors.temperatureSave')}: ` + (e.response?.data?.detail || e.message));
      return false;
    } finally {
      setTemperatureSaving(false);
    }
  };

  const toggleStreaming = (next) => stageGlobal({ agent_streaming: next });

  const savePolicy = async (patch) => {
    setPolicySaving(true);
    setError('');
    setPolicySaved(false);
    try {
      const { data } = await updateWorkspacePolicy(workspace, patch);
      setPolicy(data);
      if (patch.hooks !== undefined) setHooksText(JSON.stringify(data.hooks || {}, null, 2));
      setPolicySaved(true);
      setTimeout(() => setPolicySaved(false), 3000);
      return true;
    } catch (e) {
      setError(`${t('settings.errors.policySave')}: ` + (e.response?.data?.detail || e.message));
      return false;
    } finally {
      setPolicySaving(false);
    }
  };

  const toggleApproval = (next) => stagePolicy({ require_tool_approval: next });
  // Whether a person may switch on auto mode in this workspace's chats; the
  // chat reads it from the workspace summary, which Save updates with it.
  const toggleAutoApprove = (next) => stagePolicy({ allow_auto_approve: next });

  const saveHooks = async () => {
    let parsed;
    try {
      parsed = hooksText.trim() ? JSON.parse(hooksText) : {};
    } catch {
      setHooksError(t('settings.hooksInvalidJson'));
      return;
    }
    if (typeof parsed !== 'object' || Array.isArray(parsed) || parsed === null) {
      setHooksError(t('settings.hooksMustBeObject'));
      return;
    }
    setHooksError('');
    await savePolicy({ hooks: parsed });
  };

  // The folder's .hooks.json is never run; importing it is the owner choosing
  // to keep what it says.
  const importHooksFile = async () => {
    setPolicySaving(true);
    setError('');
    try {
      const { data } = await importWorkspaceHooksFile(workspace);
      setPolicy(data);
      setHooksText(JSON.stringify(data.hooks || {}, null, 2));
      setPolicySaved(true);
      setTimeout(() => setPolicySaved(false), 3000);
    } catch (e) {
      setError(`${t('settings.errors.policySave')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setPolicySaving(false);
    }
  };

  const fetchModels = async (provider) => {
    const baseUrl = provider === 'ollama'
      ? (getFieldValue('ollama_base_url') || 'http://localhost:11434')
      : (getFieldValue('lmstudio_base_url') || 'http://localhost:1234');
    setProviderTesting(s => ({ ...s, [provider]: true }));
    setFetchErrors(s => ({ ...s, [provider]: '' }));
    setProviderStatus(s => ({ ...s, [provider]: null }));
    try {
      const { data } = await testLocalModel(provider, baseUrl);
      if (data.ok) {
        if (!data.models?.length) setFetchErrors(s => ({ ...s, [provider]: t('settings.connectedNoModels') }));
      } else {
        setFetchErrors(s => ({ ...s, [provider]: data.error || t('settings.connectionFailed') }));
      }
      setProviderStatus(s => ({ ...s, [provider]: data }));
    } catch (e) {
      setFetchErrors(s => ({ ...s, [provider]: e.message }));
      setProviderStatus(s => ({ ...s, [provider]: { ok: false, error: e.message } }));
    } finally {
      setProviderTesting(s => ({ ...s, [provider]: false }));
    }
  };

  const testProvider = async (provider) => {
    if (provider === 'ollama' || provider === 'lmstudio') return fetchModels(provider);
    setProviderTesting(s => ({ ...s, [provider]: true }));
    setProviderStatus(s => ({ ...s, [provider]: null }));
    try {
      const payload = { provider };
      if (provider === 'openai') {
        if (getFieldValue('openai_api_key')) payload.api_key = getFieldValue('openai_api_key');
        if (getFieldValue('openai_base_url')) payload.base_url = getFieldValue('openai_base_url');
      } else if (provider === 'anthropic') {
        if (getFieldValue('anthropic_api_key')) payload.api_key = getFieldValue('anthropic_api_key');
      } else if (provider === 'google') {
        if (getFieldValue('google_api_key')) payload.api_key = getFieldValue('google_api_key');
      }
      const { data } = await postTestProvider(payload);
      setProviderStatus(s => ({ ...s, [provider]: data }));
    } catch (e) {
      setProviderStatus(s => ({ ...s, [provider]: { ok: false, error: e.message } }));
    } finally {
      setProviderTesting(s => ({ ...s, [provider]: false }));
    }
  };

  const shownGlobal = { ...globalSettings, ...globalPatch };
  const g = {
    ...shownGlobal,
    ...wsOverrides,
    openai_api_key: getFieldValue('openai_api_key'),
    anthropic_api_key: getFieldValue('anthropic_api_key'),
    google_api_key: getFieldValue('google_api_key'),
    langfuse_secret_key: getFieldValue('langfuse_secret_key'),
    langfuse_public_key: getFieldValue('langfuse_public_key'),
    rag_vector_db_api_key: getFieldValue('rag_vector_db_api_key'),
    rag_embedding_api_key: getFieldValue('rag_embedding_api_key'),
  };

  return {
    workspace, loading, saving, saved, savedScope, error, save, dirty, overridesDirty, hasOtherChanges, defaultModels,
    g, setG, masked, globalSettings: shownGlobal, stageGlobal, stagePolicy, setDraft,
    badgeProps: { wsOverrides, envDefinedFields },
    chatExecutionSaving, setChatExecution,
    egressSaving, setEgressProxy,
    policy: { ...policy, ...policyPatch }, hooksText, setHooksText, hooksError, setHooksError, policySaving, policySaved, toggleApproval,
    toggleAutoApprove, saveHooks, importHooksFile,
    savePolicy,
    streamingSaving: saving, toggleStreaming,
    temperatureSaving, saveGlobalTemperature,
    capabilityGuardSaving: saving, setCapabilityGuardMode, toggleOverrideRequiresContainer,
    codeRunnerSaving: saving, setCodeRunnerProvider, toggleCodeRunnerFallback,
    webAccessSaving, saveWebAccess,
    fetchErrors, providerStatus, providerTesting, testProvider,
  };
}
