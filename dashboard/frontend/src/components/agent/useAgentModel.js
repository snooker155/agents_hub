/**
 * The Model tab's form: which provider and model answer for this agent, the
 * key and base URL behind them, and the knobs that decide how long and how
 * freely it writes and how much it reasons first (effort). The efforts on
 * offer are the model's own (providers/reasoning_profile.py), so they are
 * looked up again whenever the form's provider or model changes.
 *
 * The setters come back out because the page's own load fills this form from
 * the agent spec; everything else about it is this tab's business.
 */
import { useEffect, useState } from 'react';
import { getAgentModel, getModelReasoning, testLocalModel, updateAgentModel } from '../../api';
import { EMPTY_MODEL } from './constants';

export function useAgentModel({ id, t, workspace }) {
  // Model config tab state
  const [modelForm, setModelForm] = useState(EMPTY_MODEL);
  const [modelHasApiKey, setModelHasApiKey] = useState(false);
  const [modelSaving, setModelSaving] = useState(false);
  const [modelMessage, setModelMessage] = useState('');
  const [modelShowKey, setModelShowKey] = useState(false);
  const [localModels, setLocalModels] = useState([]);   // fetched model list
  const [localModelsFetching, setLocalModelsFetching] = useState(false);
  const [localModelsError, setLocalModelsError] = useState('');
  // What the saved agent runs at and why (source), from the server.
  const [effectiveEffort, setEffectiveEffort] = useState(null);
  // A typed-in model's profile (efforts, default) and what inherit gives it.
  const [typedEffortInfo, setTypedEffortInfo] = useState(null);

  const handleFetchLocalModels = async () => {
    const provider = modelForm.provider; // 'ollama' | 'lmstudio'
    const baseUrl = modelForm.base_url ||
      (provider === 'ollama' ? 'http://localhost:11434' : 'http://localhost:1234');
    setLocalModelsFetching(true);
    setLocalModelsError('');
    setLocalModels([]);
    try {
      const { data } = await testLocalModel(provider, baseUrl);
      if (data.ok) {
        setLocalModels(data.models || []);
        if (!data.models?.length) setLocalModelsError(t('agentDetails.connectedNoModels'));
      } else {
        setLocalModelsError(data.error || t('agentDetails.connectionFailed'));
      }
    } catch (e) {
      setLocalModelsError(e.message);
    } finally {
      setLocalModelsFetching(false);
    }
  };

  const handleSaveModel = async () => {
    setModelSaving(true);
    setModelMessage('');
    try {
      const payload = {
        provider: modelForm.provider,
        model: modelForm.model,
        base_url: modelForm.base_url,
      };
      if (modelForm.api_key.trim()) {
        payload.api_key = modelForm.api_key.trim();
      }
      const tempVal = parseFloat(modelForm.temperature);
      if (modelForm.temperature.trim() === '') {
        payload.clear_temperature = true;
      } else if (!isNaN(tempVal)) {
        payload.temperature = tempVal;
      }
      payload.effort = modelForm.effort || '';
      const tokVal = parseInt(modelForm.max_tokens, 10);
      if (modelForm.max_tokens.trim() === '') {
        payload.clear_max_tokens = true;
      } else if (!isNaN(tokVal)) {
        payload.max_tokens = tokVal;
      }
      const resp = await updateAgentModel(id, payload, workspace);
      setModelHasApiKey(!!resp.data.has_api_key);
      setEffectiveEffort(resp.data.effective_effort || null);
      setModelForm(f => ({ ...f, api_key: '' }));
      setModelMessage(t('agentDetails.modelSettingsSaved'));
      setTimeout(() => setModelMessage(''), 3000);
    } catch (error) {
      setModelMessage(error.response?.data?.detail || t('agentDetails.errors.save'));
    } finally {
      setModelSaving(false);
    }
  };

  // Switching agents empties the form before the read below refills it, so the
  // previous agent's settings are never on screen attached to this one. Adjusted
  // during render rather than in an effect, so the old values never paint.
  const [formFor, setFormFor] = useState(id);
  if (formFor !== id) {
    setFormFor(id);
    setModelForm(EMPTY_MODEL);
    setModelMessage('');
    setEffectiveEffort(null);
  }

  useEffect(() => {
    getAgentModel(id, workspace)
      .then(r => {
        const d = r.data;
        setModelHasApiKey(!!d.has_api_key);
        setEffectiveEffort(d.effective_effort || null);
        setModelForm({
          provider: d.provider || 'inherit',
          model: d.model || '',
          api_key: '',
          base_url: d.base_url || '',
          temperature: d.temperature != null ? String(d.temperature) : '',
          max_tokens: d.max_tokens != null ? String(d.max_tokens) : '',
          effort: d.effort || '',
        });
      })
      .catch(() => {});
  }, [id, workspace]);

  // The efforts the form's model takes. An inherited model is the saved
  // answer's (it already knows which one the agent inherits); a model typed
  // here is asked about, a moment after the typing stops.
  const formProvider = modelForm.provider;
  const formModel = modelForm.model;
  useEffect(() => {
    if (formProvider === 'inherit') return undefined;
    let live = true;
    const timer = setTimeout(() => {
      getModelReasoning({ provider: formProvider, model: formModel })
        .then(r => { if (live) setTypedEffortInfo(r.data); })
        .catch(() => { if (live) setTypedEffortInfo(null); });
    }, 300);
    return () => { live = false; clearTimeout(timer); };
  }, [formProvider, formModel]);
  const effortInfo = formProvider === 'inherit' ? effectiveEffort : typedEffortInfo;

  return {
    modelForm, setModelForm, modelHasApiKey, setModelHasApiKey,
    modelSaving, modelMessage, modelShowKey, setModelShowKey,
    localModels, localModelsFetching, localModelsError,
    handleFetchLocalModels, handleSaveModel,
    effectiveEffort, effortInfo,
  };
}

export default useAgentModel;
