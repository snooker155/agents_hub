import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, Loader, Sparkles, X } from 'lucide-react';
import {
  flattenModelCatalog, getAgentLoopSettings, getModelsCatalog, updateAgentLoopSettings,
} from '../../api/agentLoop';
import { useI18n } from '../../i18n';

const selectCls = 'border border-gray-200 rounded-lg px-2 py-1 text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none';
const iconBtnCls = 'p-1 rounded text-gray-500 hover:text-gray-800 hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed';

const triState = (value) => (value === null || value === undefined ? '' : (value ? 'on' : 'off'));
const fromTriState = (value) => (value === '' ? null : value === 'on');

/**
 * The agent's loop settings (agents/agent_loop.py, fourth-cycle stage 2):
 * fallback models tried in order on a refusal, a rate limit or a server
 * error (agents/loop_ext/fallback.py), the JSON Schema the final answer
 * must match with a repair retry (agents/loop_ext/structured.py), and the
 * two tri-state loop toggles (tool search, compaction; None follows the
 * workspace/global default).
 */
export default function LoopSettingsCard({ agentId, agent: _agent, onSaved }) {
  const { t } = useI18n();
  const [catalog, setCatalog] = useState([]);
  const [fallbackModels, setFallbackModels] = useState([]);
  const [addModel, setAddModel] = useState('');
  const [schemaMode, setSchemaMode] = useState('free'); // 'free' | 'schema'
  const [schemaText, setSchemaText] = useState('');
  const [toolSearch, setToolSearch] = useState('');
  const [compaction, setCompaction] = useState('');
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  // Read through a ref so a language switch (a new `t`) does not refetch.
  const tRef = useRef(t);
  tRef.current = t;

  const applyLoaded = useCallback((body) => {
    setFallbackModels(Array.isArray(body?.fallback_models) ? [...body.fallback_models] : []);
    const schema = body?.output_schema;
    setSchemaMode(schema ? 'schema' : 'free');
    setSchemaText(schema ? JSON.stringify(schema, null, 2) : '');
    setToolSearch(triState(body?.tool_search));
    setCompaction(triState(body?.compaction));
    setDirty(false);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [{ data: body }, { data: rawCatalog }] = await Promise.all([
        getAgentLoopSettings(agentId), getModelsCatalog(),
      ]);
      applyLoaded(body);
      setCatalog(flattenModelCatalog(rawCatalog));
    } catch (e) {
      setError(e?.response?.data?.detail || tRef.current('agentLoop.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [agentId, applyLoaded]);

  useEffect(() => { load(); }, [load]);

  const markDirty = () => { setMessage(''); setDirty(true); };

  const addFallback = () => {
    if (!addModel || fallbackModels.includes(addModel)) return;
    setFallbackModels((prev) => [...prev, addModel]);
    setAddModel('');
    markDirty();
  };

  const removeFallback = (id) => {
    setFallbackModels((prev) => prev.filter((m) => m !== id));
    markDirty();
  };

  const moveFallback = (index, delta) => {
    setFallbackModels((prev) => {
      const next = [...prev];
      const target = index + delta;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
    markDirty();
  };

  const availableModels = catalog.filter((m) => !fallbackModels.includes(m.id));

  const parseSchema = () => {
    if (schemaMode === 'free') return { schema: null, error: '' };
    const text = schemaText.trim();
    if (!text) return { schema: null, error: t('agentLoop.schemaEmpty') };
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      return { schema: null, error: t('agentLoop.schemaInvalidJson', { message: e.message }) };
    }
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return { schema: null, error: t('agentLoop.schemaNotObject') };
    }
    return { schema: parsed, error: '' };
  };

  const save = async () => {
    const { schema, error: schemaError } = parseSchema();
    if (schemaError) {
      setError(schemaError);
      return;
    }
    setSaving(true);
    setMessage('');
    setError('');
    try {
      const { data: body } = await updateAgentLoopSettings(agentId, {
        fallback_models: fallbackModels,
        output_schema: schema,
        tool_search: fromTriState(toolSearch),
        compaction: fromTriState(compaction),
      });
      applyLoaded(body);
      setMessage(t('agentLoop.saved'));
      if (onSaved) onSaved();
    } catch (e) {
      setError(e?.response?.data?.detail || t('agentLoop.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="bg-white p-6 shadow-md rounded-lg mt-6" data-testid="loop-settings-card">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-indigo-600" />
          <h3 className="text-lg font-bold text-gray-900">{t('agentLoop.title')}</h3>
        </div>
        <button
          type="button"
          onClick={save}
          disabled={saving || !dirty || loading}
          className={`px-4 py-2 rounded-lg text-sm font-semibold ${
            saving || !dirty || loading
              ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
              : 'bg-indigo-600 text-white hover:bg-indigo-700'
          }`}
        >
          {saving ? t('common.saving') : t('agentLoop.save')}
        </button>
      </div>
      <p className="text-sm text-gray-600 mb-4">{t('agentLoop.intro')}</p>

      {loading ? (
        <Loader className="w-4 h-4 animate-spin text-gray-400" />
      ) : (
        <div className="space-y-6">
          {/* Fallback models */}
          <div>
            <h4 className="text-sm font-semibold text-gray-800 mb-1">{t('agentLoop.fallback.title')}</h4>
            <p className="text-xs text-gray-500 mb-3">{t('agentLoop.fallback.intro')}</p>

            {fallbackModels.length === 0 ? (
              <p className="text-sm text-gray-500 italic mb-3">{t('agentLoop.fallback.empty')}</p>
            ) : (
              <ol className="space-y-1.5 mb-3">
                {fallbackModels.map((id, index) => (
                  <li key={id} className="flex items-center gap-2 text-sm bg-gray-50 border border-gray-100 rounded-lg px-3 py-1.5">
                    <span className="text-xs text-gray-400 w-5 text-right">{index + 1}.</span>
                    <span className="font-mono flex-1">{id}</span>
                    <button type="button" onClick={() => moveFallback(index, -1)} disabled={index === 0}
                            aria-label={t('agentLoop.fallback.moveUp', { model: id })} className={iconBtnCls}>
                      <ArrowUp className="w-3.5 h-3.5" />
                    </button>
                    <button type="button" onClick={() => moveFallback(index, 1)} disabled={index === fallbackModels.length - 1}
                            aria-label={t('agentLoop.fallback.moveDown', { model: id })} className={iconBtnCls}>
                      <ArrowDown className="w-3.5 h-3.5" />
                    </button>
                    <button type="button" onClick={() => removeFallback(id)}
                            aria-label={t('agentLoop.fallback.remove', { model: id })} className={iconBtnCls}>
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </li>
                ))}
              </ol>
            )}

            <div className="flex items-center gap-2">
              <select
                value={addModel}
                onChange={(e) => setAddModel(e.target.value)}
                aria-label={t('agentLoop.fallback.addLabel')}
                className={`${selectCls} flex-1`}
              >
                <option value="">{t('agentLoop.fallback.addPlaceholder')}</option>
                {availableModels.map((m) => <option key={m.id} value={m.id}>{m.id}</option>)}
              </select>
              <button
                type="button"
                onClick={addFallback}
                disabled={!addModel}
                className="px-3 py-1.5 rounded-lg text-sm font-semibold bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {t('agentLoop.fallback.add')}
              </button>
            </div>
            {catalog.length === 0 && (
              <p className="text-xs text-gray-400 mt-2">{t('agentLoop.fallback.noCatalog')}</p>
            )}
          </div>

          {/* Output schema */}
          <div>
            <h4 className="text-sm font-semibold text-gray-800 mb-1">{t('agentLoop.schema.title')}</h4>
            <p className="text-xs text-gray-500 mb-3">{t('agentLoop.schema.intro')}</p>
            <div className="flex gap-4 mb-2 text-sm">
              <label className="flex items-center gap-1.5">
                <input type="radio" name="loop-schema-mode" value="free" checked={schemaMode === 'free'}
                       onChange={() => { setSchemaMode('free'); markDirty(); }} />
                {t('agentLoop.schema.freeText')}
              </label>
              <label className="flex items-center gap-1.5">
                <input type="radio" name="loop-schema-mode" value="schema" checked={schemaMode === 'schema'}
                       onChange={() => { setSchemaMode('schema'); markDirty(); }} />
                {t('agentLoop.schema.jsonSchema')}
              </label>
            </div>
            {schemaMode === 'schema' && (
              <textarea
                value={schemaText}
                onChange={(e) => { setSchemaText(e.target.value); markDirty(); }}
                rows={8}
                aria-label={t('agentLoop.schema.title')}
                placeholder={t('agentLoop.schema.placeholder')}
                className="w-full font-mono text-xs border border-gray-200 rounded-lg px-3 py-2 focus:ring-2 focus:ring-indigo-500 focus:outline-none"
              />
            )}
          </div>

          {/* Tool search / compaction */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <label className="flex flex-col gap-1 text-sm text-gray-700">
              <span className="font-medium">{t('agentLoop.toolSearch.title')}</span>
              <select value={toolSearch} onChange={(e) => { setToolSearch(e.target.value); markDirty(); }}
                      aria-label={t('agentLoop.toolSearch.title')} className={selectCls}>
                <option value="">{t('agentLoop.triState.automatic')}</option>
                <option value="on">{t('agentLoop.triState.on')}</option>
                <option value="off">{t('agentLoop.triState.off')}</option>
              </select>
              <span className="text-xs text-gray-500">{t('agentLoop.toolSearch.hint')}</span>
            </label>
            <label className="flex flex-col gap-1 text-sm text-gray-700">
              <span className="font-medium">{t('agentLoop.compaction.title')}</span>
              <select value={compaction} onChange={(e) => { setCompaction(e.target.value); markDirty(); }}
                      aria-label={t('agentLoop.compaction.title')} className={selectCls}>
                <option value="">{t('agentLoop.triState.automatic')}</option>
                <option value="on">{t('agentLoop.triState.on')}</option>
                <option value="off">{t('agentLoop.triState.off')}</option>
              </select>
              <span className="text-xs text-gray-500">{t('agentLoop.compaction.hint')}</span>
            </label>
          </div>

          {error && (
            <p className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>
          )}
          {message && <p className="text-xs text-gray-600">{message}</p>}
        </div>
      )}
    </div>
  );
}
