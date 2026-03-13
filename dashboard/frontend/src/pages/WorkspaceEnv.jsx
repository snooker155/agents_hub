import { useState, useEffect } from 'react';
import { Plus, Trash2, Save, RefreshCw, Eye, EyeOff, Variable, Info } from 'lucide-react';
import { useWorkspace } from '../components/WorkspaceContext';
import { getWorkspaces, getWorkspaceEnv, updateWorkspaceEnv } from '../api';

function EnvRow({ envKey, value, onChange, onDelete, isNew }) {
  const [show, setShow] = useState(isNew);
  const looksSecret = /key|secret|token|password|api|auth|pass/i.test(envKey);

  return (
    <div className="flex items-center gap-2 group">
      <input
        type="text"
        value={envKey}
        onChange={e => onChange(e.target.value, value)}
        placeholder="VARIABLE_NAME"
        className="w-48 border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none uppercase-placeholder"
        spellCheck={false}
      />
      <span className="text-gray-400 text-sm">=</span>
      <div className="relative flex-1">
        <input
          type={looksSecret && !show ? 'password' : 'text'}
          value={value}
          onChange={e => onChange(envKey, e.target.value)}
          placeholder="value"
          className="w-full border border-gray-300 rounded-lg px-3 py-2 pr-9 text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none"
          spellCheck={false}
        />
        {looksSecret && (
          <button
            type="button"
            onClick={() => setShow(s => !s)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
          >
            {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        )}
      </div>
      <button
        type="button"
        onClick={onDelete}
        className="p-2 text-gray-400 hover:text-red-500 opacity-0 group-hover:opacity-100 transition-opacity"
        title="Remove variable"
      >
        <Trash2 className="w-4 h-4" />
      </button>
    </div>
  );
}

export default function WorkspaceEnv() {
  const { selectedWorkspace, setSelectedWorkspace } = useWorkspace();
  const [workspaces, setWorkspaces] = useState([]);
  const [rows, setRows] = useState([]); // [{ key, value, isNew }]
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  // Load workspace list once
  useEffect(() => {
    getWorkspaces().then(r => setWorkspaces(r.data)).catch(() => {});
  }, []);

  // Load env vars whenever selected workspace changes
  useEffect(() => {
    if (!selectedWorkspace) return;
    setLoading(true);
    setError('');
    setSaved(false);
    getWorkspaceEnv(selectedWorkspace)
      .then(r => {
        const vars = r.data.env_vars || {};
        setRows(Object.entries(vars).map(([k, v]) => ({ key: k, value: v, isNew: false })));
      })
      .catch(e => setError('Failed to load: ' + (e.response?.data?.detail || e.message)))
      .finally(() => setLoading(false));
  }, [selectedWorkspace]);

  const updateRow = (idx, newKey, newValue) => {
    setRows(prev => prev.map((r, i) => i === idx ? { ...r, key: newKey, value: newValue } : r));
  };

  const deleteRow = (idx) => {
    setRows(prev => prev.filter((_, i) => i !== idx));
  };

  const addRow = () => {
    setRows(prev => [...prev, { key: '', value: '', isNew: true }]);
  };

  const handleSave = async () => {
    // Validate: no empty keys, no duplicate keys
    const keys = rows.map(r => r.key.trim()).filter(Boolean);
    const dupes = keys.filter((k, i) => keys.indexOf(k) !== i);
    if (rows.some(r => !r.key.trim())) {
      setError('All variable names must be non-empty.');
      return;
    }
    if (dupes.length) {
      setError(`Duplicate key(s): ${dupes.join(', ')}`);
      return;
    }

    setSaving(true);
    setError('');
    setSaved(false);
    try {
      const envVars = Object.fromEntries(rows.map(r => [r.key.trim(), r.value]));
      await updateWorkspaceEnv(selectedWorkspace, envVars);
      setRows(prev => prev.map(r => ({ ...r, isNew: false })));
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e) {
      setError('Failed to save: ' + (e.response?.data?.detail || e.message));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-3xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Variable className="w-6 h-6 text-indigo-500" />
          <h1 className="text-2xl font-bold text-gray-900">Workspace Env Vars</h1>
        </div>
        <button
          onClick={handleSave}
          disabled={saving || loading || !selectedWorkspace}
          className="flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50"
        >
          {saving ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
          {saving ? 'Saving…' : 'Save'}
        </button>
      </div>

      {/* Info banner */}
      <div className="flex gap-3 bg-blue-50 border border-blue-200 rounded-xl px-4 py-3 text-sm text-blue-700">
        <Info className="w-4 h-4 mt-0.5 shrink-0" />
        <span>
          These key-value pairs are stored in the workspace's <code className="font-mono text-xs bg-blue-100 rounded px-1">.workspace.json</code> and
          injected into agent tool calls as environment variables. Use them for workspace-specific
          secrets, endpoints, or configuration (e.g.&nbsp;<code className="font-mono text-xs bg-blue-100 rounded px-1">DATABASE_URL</code>,&nbsp;
          <code className="font-mono text-xs bg-blue-100 rounded px-1">API_KEY</code>).
        </span>
      </div>

      {/* Workspace selector */}
      <div className="flex items-center gap-3">
        <label className="text-sm font-medium text-gray-700 whitespace-nowrap">Workspace:</label>
        <select
          value={selectedWorkspace}
          onChange={e => setSelectedWorkspace(e.target.value)}
          className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
        >
          {workspaces.map(ws => (
            <option key={ws.name} value={ws.name}>{ws.name}</option>
          ))}
        </select>
      </div>

      {/* Status messages */}
      {saved && (
        <div className="bg-green-50 border border-green-200 text-green-700 rounded-lg px-4 py-3 text-sm">
          Environment variables saved.
        </div>
      )}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg px-4 py-3 text-sm">
          {error}
        </div>
      )}

      {/* Variables table */}
      <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-800">
            Variables
            {rows.length > 0 && (
              <span className="ml-2 text-xs font-normal text-gray-500">({rows.length})</span>
            )}
          </h2>
          <button
            onClick={addRow}
            disabled={loading}
            className="flex items-center gap-1.5 text-sm text-indigo-600 hover:text-indigo-800 font-medium disabled:opacity-50"
          >
            <Plus className="w-4 h-4" />
            Add variable
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-8">
            <RefreshCw className="w-5 h-5 animate-spin text-indigo-400" />
          </div>
        ) : rows.length === 0 ? (
          <div className="text-center py-10 text-gray-400 text-sm">
            <Variable className="w-8 h-8 mx-auto mb-2 opacity-30" />
            No environment variables yet. Click <span className="font-medium">Add variable</span> to get started.
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-xs font-medium text-gray-500 px-1">
              <span className="w-48">Name</span>
              <span className="w-4" />
              <span className="flex-1">Value</span>
              <span className="w-8" />
            </div>
            {rows.map((row, idx) => (
              <EnvRow
                key={idx}
                envKey={row.key}
                value={row.value}
                isNew={row.isNew}
                onChange={(k, v) => updateRow(idx, k, v)}
                onDelete={() => deleteRow(idx)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
