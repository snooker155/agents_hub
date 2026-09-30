import { updateAgentModel } from '../../api';
import { AlertCircle, BrainCircuit, Eye, EyeOff, Loader, Save, Trash2, Wifi } from 'lucide-react';
import { useAgentPage } from './context';

/** Which model answers, and how much it is allowed to think first. */
export default function ModelTab() {
  const {
    customBackends, handleFetchLocalModels, handleSaveModel, id, localModels,
    localModelsError, localModelsFetching, modelForm, modelHasApiKey, modelMessage,
    modelSaving, modelShowKey, setLocalModels, setLocalModelsError, setModelForm,
    setModelHasApiKey, setModelMessage, setModelShowKey, t,
  } = useAgentPage();
  return (
        <div className="space-y-6">
          {/* Header */}
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-lg font-bold text-gray-800 flex items-center gap-2">
                <BrainCircuit className="w-5 h-5 text-indigo-600" /> Model & Access Settings
              </h3>
              <p className="text-sm text-gray-500 mt-1">
                {t('agentDetails.overrideTheGlobalModelSettings')}
              </p>
            </div>
            <button
              onClick={handleSaveModel}
              disabled={modelSaving}
              className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-semibold disabled:opacity-50"
            >
              {modelSaving ? <Loader className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
              {modelSaving ? 'Saving…' : 'Save'}
            </button>
          </div>

          {modelMessage && (
            <div className={`rounded-lg px-4 py-2 text-sm ${modelMessage.includes('saved') ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
              {modelMessage}
            </div>
          )}

          {/* Provider */}
          <div className="bg-white p-6 shadow-md rounded-lg space-y-5">
            <h4 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">{t('agentDetails.provider')}</h4>
            <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
              {[
                { value: 'inherit',   label: t('agentDetails.inherit'), sub: t('agentDetails.globalSub') },
                { value: 'openai',    label: 'OpenAI',     sub: t('agentDetails.cloudSub') },
                { value: 'anthropic', label: 'Anthropic',  sub: 'Claude' },
                { value: 'google',    label: 'Google',     sub: 'Gemini' },
                { value: 'ollama',    label: 'Ollama',     sub: t('agentDetails.localSub') },
                { value: 'lmstudio', label: 'LM Studio',  sub: t('agentDetails.localSub') },
                ...customBackends.map(b => ({ value: b.id, label: b.label || b.id, sub: t('agentDetails.customSub') })),
              ].map(opt => (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => { setModelForm(f => ({ ...f, provider: opt.value })); setLocalModels([]); setLocalModelsError(''); }}
                  className={`flex flex-col items-center gap-0.5 px-3 py-3 rounded-xl border-2 text-sm font-semibold transition-colors ${
                    modelForm.provider === opt.value
                      ? 'border-indigo-600 bg-indigo-50 text-indigo-700'
                      : 'border-gray-200 text-gray-600 hover:border-gray-300 hover:bg-gray-50'
                  }`}
                >
                  {opt.label}
                  <span className="text-[10px] font-normal text-gray-400">{opt.sub}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Model name */}
          <div className="bg-white p-6 shadow-md rounded-lg space-y-5">
            <h4 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">{t('agentDetails.model2')}</h4>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.modelName')}</label>
              <p className="text-xs text-gray-500 mb-2">
                {modelForm.provider === 'inherit' && t('agentDetails.inheritingModel')}
                {modelForm.provider === 'openai' && 'e.g. gpt-4o, gpt-4o-mini, gpt-4-turbo'}
                {modelForm.provider === 'anthropic' && 'e.g. claude-opus-4-6, claude-sonnet-4-6, claude-haiku-4-5-20251001'}
                {modelForm.provider === 'google' && 'e.g. gemini-2.0-flash, gemini-1.5-pro'}
                {modelForm.provider === 'ollama' && t('agentDetails.ollamaModelHint')}
                {modelForm.provider === 'lmstudio' && t('agentDetails.lmstudioModelHint')}
                {customBackends.some(b => b.id === modelForm.provider) && t('agentDetails.customBackendModelHint')}
              </p>
              {(modelForm.provider === 'ollama' || modelForm.provider === 'lmstudio') && localModels.length > 0 ? (
                <select
                  value={modelForm.model}
                  onChange={e => setModelForm(f => ({ ...f, model: e.target.value }))}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                >
                  <option value="">{t('agentDetails.selectAModel')}</option>
                  {localModels.map(m => <option key={m} value={m}>{m}</option>)}
                </select>
              ) : (
                <input
                  type="text"
                  value={modelForm.model}
                  onChange={e => setModelForm(f => ({ ...f, model: e.target.value }))}
                  placeholder={modelForm.provider === 'inherit' ? t('agentDetails.inheritingPlaceholder') : t('agentDetails.enterModelName')}
                  disabled={modelForm.provider === 'inherit'}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none disabled:opacity-50 disabled:bg-gray-50"
                />
              )}
              {localModelsError && (
                <p className="text-xs text-red-600 mt-1 flex items-center gap-1"><AlertCircle className="w-3 h-3" />{localModelsError}</p>
              )}
            </div>

            {/* Base URL */}
            {(modelForm.provider === 'openai' || modelForm.provider === 'ollama' || modelForm.provider === 'lmstudio') && (
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.baseUrlOverride')}</label>
                <p className="text-xs text-gray-500 mb-2">
                  {modelForm.provider === 'openai' && t('agentDetails.openaiBaseUrlHint')}
                  {modelForm.provider === 'ollama' && t('agentDetails.ollamaBaseUrlHint')}
                  {modelForm.provider === 'lmstudio' && t('agentDetails.lmstudioBaseUrlHint')}
                </p>
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={modelForm.base_url}
                    onChange={e => { setModelForm(f => ({ ...f, base_url: e.target.value })); setLocalModels([]); setLocalModelsError(''); }}
                    placeholder={
                      modelForm.provider === 'ollama' ? 'http://localhost:11434' :
                      modelForm.provider === 'lmstudio' ? 'http://localhost:1234' :
                      'https://api.openai.com/v1'
                    }
                    className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                  />
                  {(modelForm.provider === 'ollama' || modelForm.provider === 'lmstudio') && (
                    <button
                      type="button"
                      onClick={handleFetchLocalModels}
                      disabled={localModelsFetching}
                      className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 whitespace-nowrap"
                    >
                      {localModelsFetching
                        ? <Loader className="w-4 h-4 animate-spin" />
                        : <Wifi className="w-4 h-4" />}
                      Fetch models
                    </button>
                  )}
                </div>
              </div>
            )}

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.temperatureOverride')}</label>
                <input
                  type="number"
                  value={modelForm.temperature}
                  onChange={e => setModelForm(f => ({ ...f, temperature: e.target.value }))}
                  placeholder={t('agentDetails.inheritGlobal')}
                  min="0" max="2" step="0.05"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
                <p className="text-xs text-gray-400 mt-1">{t('agentDetails.clearToInheritFromGlobal')}</p>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">{t('agentDetails.maxTokensOverride')}</label>
                <input
                  type="number"
                  value={modelForm.max_tokens}
                  onChange={e => setModelForm(f => ({ ...f, max_tokens: e.target.value }))}
                  placeholder={t('agentDetails.inheritGlobal')}
                  min="256" step="256"
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
                <p className="text-xs text-gray-400 mt-1">{t('agentDetails.clearToInheritFromGlobal')}</p>
              </div>
            </div>
          </div>

          {/* API Key override */}
          {(modelForm.provider !== 'inherit' && modelForm.provider !== 'ollama' && modelForm.provider !== 'lmstudio') && (
            <div className="bg-white p-6 shadow-md rounded-lg space-y-4">
              <h4 className="text-sm font-semibold text-gray-700 uppercase tracking-wider">{t('agentDetails.apiKeyOverride')}</h4>
              <p className="text-sm text-gray-500">
                Optionally store a per-agent API key. This overrides the key from global settings for this agent only.
                {modelHasApiKey && <span className="ml-1 text-green-600 font-medium">{t('agentDetails.aKeyIsCurrentlyStored')}</span>}
              </p>
              <div className="relative">
                <input
                  type={modelShowKey ? 'text' : 'password'}
                  value={modelForm.api_key}
                  onChange={e => setModelForm(f => ({ ...f, api_key: e.target.value }))}
                  placeholder={modelHasApiKey ? t('agentDetails.keyStored') : t('agentDetails.enterApiKey')}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 pr-10 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => setModelShowKey(s => !s)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                >
                  {modelShowKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
              {modelHasApiKey && (
                <button
                  type="button"
                  onClick={() => {
                    updateAgentModel(id, { clear_api_key: true })
                      .then(r => { setModelHasApiKey(!!r.data.has_api_key); setModelMessage(t('agentDetails.apiKeyRemoved')); setTimeout(() => setModelMessage(''), 3000); })
                      .catch(() => setModelMessage(t('agentDetails.errors.removeKey')));
                  }}
                  className="text-xs text-red-500 hover:text-red-700 flex items-center gap-1"
                >
                  <Trash2 className="w-3.5 h-3.5" /> {t('agentDetails.removeStoredKey')}
                </button>
              )}
            </div>
          )}
        </div>
  );
}
