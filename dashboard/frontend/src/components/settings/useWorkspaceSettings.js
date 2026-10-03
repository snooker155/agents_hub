// The state behind WorkspaceSettingsSections.jsx: one workspace's settings
// overrides, the global values they fall back to, its tool policy, and the
// provider connection tests. Used by the Settings page and by the Settings tab
// of a workspace.
import { useState, useEffect, useCallback } from 'react';
import {
  getSettings, updateSettings, testProvider as postTestProvider, testLocalModel,
  getWorkspaceSettingsOverrides, updateWorkspaceSettingsOverrides,
  getWorkspacePolicy, updateWorkspacePolicy,
} from '../../api';
import { useI18n } from '../../i18n';

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
  const [error, setError] = useState('');

  const [globalSettings, setGlobalSettings] = useState({});
  const [envDefinedFields, setEnvDefinedFields] = useState([]);
  const [masked, setMasked] = useState({});
  const [wsOverrides, setWsOverrides] = useState({});

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
  // changes how every agent process on this machine builds its LLM. Saved on
  // toggle rather than via the workspace Save button, which writes elsewhere.
  const [streamingSaving, setStreamingSaving] = useState(false);

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

      setWsOverrides(overridesResp?.data?.overrides || {});

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

  const save = async () => {
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      const payload = {};
      OVERRIDE_FIELDS.forEach((k) => { if (wsOverrides[k]) payload[k] = wsOverrides[k]; });
      await updateWorkspaceSettingsOverrides(workspace, payload);
      await load();
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
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

  // Tool capability guard (agents/capability_guard.py): block | warn | off,
  // and whether a per-agent override needs a no-network container to count.
  // Machine-wide .env settings, applied live on the next save or build.
  const [capabilityGuardSaving, setCapabilityGuardSaving] = useState(false);
  const saveCapabilityGuard = async (patch) => {
    setCapabilityGuardSaving(true);
    setError('');
    try {
      await updateSettings(patch);
      setGlobalSettings(s => ({ ...s, ...patch }));
    } catch (e) {
      setError(`${t('settings.errors.capabilityGuardSave')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setCapabilityGuardSaving(false);
    }
  };
  const setCapabilityGuardMode = (mode) => saveCapabilityGuard({ capability_guard: mode });
  const toggleOverrideRequiresContainer = (next) => saveCapabilityGuard({ capability_override_requires_container: next });

  // Code execution (tools/run_code.py): the sandbox provider and what to do
  // when docker is down. Machine-wide .env settings that the backend also
  // changes in its running process, so the next run uses them at once.
  const [codeRunnerSaving, setCodeRunnerSaving] = useState(false);
  const saveCodeRunner = async (patch) => {
    setCodeRunnerSaving(true);
    setError('');
    try {
      await updateSettings(patch);
      setGlobalSettings(s => ({ ...s, ...patch }));
    } catch (e) {
      setError(`${t('settings.errors.codeRunnerSave')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setCodeRunnerSaving(false);
    }
  };
  const setCodeRunnerProvider = (provider) => saveCodeRunner({ code_runner_provider: provider });
  const toggleCodeRunnerFallback = (next) => saveCodeRunner({ code_runner_fallback: next ? 'local' : 'none' });

  // Web search (tools/web.py): provider, key and result count behind the
  // web_search tool. Machine-wide .env settings, read live by every process.
  const [webSearchSaving, setWebSearchSaving] = useState(false);
  const saveWebSearch = async (patch) => {
    setWebSearchSaving(true);
    setError('');
    try {
      await updateSettings(patch);
      const { data } = await getSettings();
      setGlobalSettings(data);
      return true;
    } catch (e) {
      setError(`${t('settings.errors.webSearchSave')}: ` + (e.response?.data?.detail || e.message));
      return false;
    } finally {
      setWebSearchSaving(false);
    }
  };

  const toggleStreaming = async (next) => {
    setStreamingSaving(true);
    setError('');
    try {
      await updateSettings({ agent_streaming: next });
      setGlobalSettings(s => ({ ...s, agent_streaming: next }));
    } catch (e) {
      setError(`${t('settings.errors.streamingSave')}: ` + (e.response?.data?.detail || e.message));
    } finally {
      setStreamingSaving(false);
    }
  };

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

  const toggleApproval = (next) => savePolicy({ require_tool_approval: next });

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

  const g = {
    ...globalSettings,
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
    workspace, loading, saving, saved, error, save,
    g, setG, masked, globalSettings,
    badgeProps: { wsOverrides, envDefinedFields },
    chatExecutionSaving, setChatExecution,
    policy, hooksText, setHooksText, hooksError, setHooksError, policySaving, policySaved, toggleApproval, saveHooks,
    savePolicy,
    streamingSaving, toggleStreaming,
    capabilityGuardSaving, setCapabilityGuardMode, toggleOverrideRequiresContainer,
    codeRunnerSaving, setCodeRunnerProvider, toggleCodeRunnerFallback,
    webSearchSaving, saveWebSearch,
    fetchErrors, providerStatus, providerTesting, testProvider,
  };
}
