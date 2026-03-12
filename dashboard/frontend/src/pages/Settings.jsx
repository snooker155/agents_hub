import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { Eye, EyeOff, Save, RefreshCw } from 'lucide-react';

const api = axios.create({ baseURL: 'http://localhost:8000' });

const MODELS = [
  'gpt-4o', 'gpt-4o-mini', 'gpt-5', 'gpt-4-turbo', 'gpt-4', 'gpt-3.5-turbo',
];

const LOG_LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'];

function PasswordField({ label, name, value, onChange, placeholder }) {
  const [show, setShow] = useState(false);
  return (
    <div>
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <div className="relative">
        <input
          type={show ? 'text' : 'password'}
          name={name}
          value={value}
          onChange={onChange}
          placeholder={placeholder}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 pr-10 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono"
        />
        <button
          type="button"
          onClick={() => setShow(s => !s)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
        >
          {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
      </div>
    </div>
  );
}

export default function Settings() {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');
  const [masked, setMasked] = useState({
    openai_api_key_masked: '',
    langfuse_secret_key_masked: '',
    langfuse_public_key_masked: '',
  });

  // Editable form state
  const [form, setForm] = useState({
    openai_api_key: '',
    model: 'gpt-4o',
    temperature: '0.0',
    max_tokens: '15000',
    langfuse_secret_key: '',
    langfuse_public_key: '',
    langfuse_base_url: '',
    workspace_root: './out',
    orch_poll_interval: '5.0',
    orch_log_level: 'INFO',
  });

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const { data } = await api.get('/api/settings');
      setMasked({
        openai_api_key_masked: data.openai_api_key_masked,
        langfuse_secret_key_masked: data.langfuse_secret_key_masked,
        langfuse_public_key_masked: data.langfuse_public_key_masked,
      });
      setForm(f => ({
        ...f,
        model: data.model,
        temperature: String(data.temperature),
        max_tokens: String(data.max_tokens),
        langfuse_base_url: data.langfuse_base_url,
        workspace_root: data.workspace_root,
        orch_poll_interval: String(data.orch_poll_interval),
        orch_log_level: data.orch_log_level,
      }));
    } catch (e) {
      setError('Failed to load settings: ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleChange = e => {
    const { name, value } = e.target;
    setForm(f => ({ ...f, [name]: value }));
  };

  const handleSave = async () => {
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      // Only send fields that have non-empty values (so we don't overwrite keys with blanks)
      const payload = {};
      const secretFields = ['openai_api_key', 'langfuse_secret_key', 'langfuse_public_key'];
      for (const [k, v] of Object.entries(form)) {
        if (secretFields.includes(k)) {
          if (v.trim()) payload[k] = v.trim();
        } else {
          payload[k] = v;
        }
      }
      await api.put('/api/settings', payload);
      setSaved(true);
      // Refresh masked values
      await load();
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError('Failed to save: ' + (e.response?.data?.detail || e.message));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-6 h-6 animate-spin text-indigo-500" />
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
        >
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? 'Saving…' : 'Save Changes'}
        </button>
      </div>

      {saved && (
        <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg px-4 py-3 text-sm">
          Settings saved. Restart the backend for changes to take effect.
        </div>
      )}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {/* LLM Configuration */}
      <section className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 space-y-5">
        <h2 className="text-base font-semibold text-gray-800">LLM Configuration</h2>

        <div>
          <PasswordField
            label={`OpenAI API Key (current: ${masked.openai_api_key_masked})`}
            name="openai_api_key"
            value={form.openai_api_key}
            onChange={handleChange}
            placeholder="sk-… (leave blank to keep existing)"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Model</label>
          <div className="flex gap-2">
            <select
              name="model"
              value={MODELS.includes(form.model) ? form.model : '__custom__'}
              onChange={e => {
                if (e.target.value !== '__custom__') {
                  setForm(f => ({ ...f, model: e.target.value }));
                }
              }}
              className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
            >
              {MODELS.map(m => <option key={m} value={m}>{m}</option>)}
              {!MODELS.includes(form.model) && <option value="__custom__">{form.model}</option>}
            </select>
            <input
              type="text"
              name="model"
              value={form.model}
              onChange={handleChange}
              placeholder="or type custom model name"
              className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono"
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Temperature</label>
            <input
              type="number"
              name="temperature"
              value={form.temperature}
              onChange={handleChange}
              min="0" max="2" step="0.05"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Max Tokens</label>
            <input
              type="number"
              name="max_tokens"
              value={form.max_tokens}
              onChange={handleChange}
              min="256" step="256"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
            />
          </div>
        </div>
      </section>

      {/* Langfuse Observability */}
      <section className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 space-y-5">
        <h2 className="text-base font-semibold text-gray-800">Langfuse Observability</h2>

        <PasswordField
          label={`Secret Key (current: ${masked.langfuse_secret_key_masked})`}
          name="langfuse_secret_key"
          value={form.langfuse_secret_key}
          onChange={handleChange}
          placeholder="sk-lf-… (leave blank to keep existing)"
        />
        <PasswordField
          label={`Public Key (current: ${masked.langfuse_public_key_masked})`}
          name="langfuse_public_key"
          value={form.langfuse_public_key}
          onChange={handleChange}
          placeholder="pk-lf-… (leave blank to keep existing)"
        />
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Base URL</label>
          <input
            type="url"
            name="langfuse_base_url"
            value={form.langfuse_base_url}
            onChange={handleChange}
            placeholder="http://localhost:3000"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
          />
        </div>
      </section>

      {/* Orchestrator / System */}
      <section className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 space-y-5">
        <h2 className="text-base font-semibold text-gray-800">Orchestrator & System</h2>

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Workspace Root</label>
          <input
            type="text"
            name="workspace_root"
            value={form.workspace_root}
            onChange={handleChange}
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none font-mono"
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Poll Interval (s)</label>
            <input
              type="number"
              name="orch_poll_interval"
              value={form.orch_poll_interval}
              onChange={handleChange}
              min="1" step="1"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Log Level</label>
            <select
              name="orch_log_level"
              value={form.orch_log_level}
              onChange={handleChange}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
            >
              {LOG_LEVELS.map(l => <option key={l} value={l}>{l}</option>)}
            </select>
          </div>
        </div>
      </section>
    </div>
  );
}
