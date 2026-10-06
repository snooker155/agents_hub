import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRightLeft, Loader } from 'lucide-react';
import {
  formatHistoryFilter, getAgentHandoffs, getAgents, parseHistoryFilter, updateAgentHandoffs,
} from '../../api/handoffs';
import { useWorkspace } from '../workspace';
import { useI18n } from '../../i18n';

const selectCls = 'border border-gray-200 rounded-lg px-2 py-1 text-sm focus:outline-none';
const FILTER_KINDS = ['full', 'summary', 'last_n', 'none'];
const DEFAULT_LAST_N = 10;
const MAX_LAST_N = 200;

const sameList = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

/**
 * Conversation handoff (chat/handoff.py, docs/handoffs.md): the agents this
 * one may give the conversation to in a chat, and how much of the
 * conversation the receiving agent sees by default. With no target the agent
 * has no handoff tool at all. A single handoff may only narrow the default
 * filter, so the widest one the operator allows is set here.
 */
export default function HandoffsCard({ agentId, agent, onSaved }) {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const workspace = selectedWorkspace || agent?.owner_workspace || undefined;
  const [agents, setAgents] = useState([]);
  const [saved, setSaved] = useState({ handoffs: [], history: 'full' });
  const [targets, setTargets] = useState([]);
  const [kind, setKind] = useState('full');
  const [lastN, setLastN] = useState(DEFAULT_LAST_N);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  // Read through a ref so a language switch (a new `t`) does not refetch.
  const tRef = useRef(t);
  tRef.current = t;

  const apply = useCallback((body) => {
    const list = Array.isArray(body?.handoffs) ? [...body.handoffs] : [];
    const history = body?.handoff_history || 'full';
    const parsed = parseHistoryFilter(history);
    setSaved({ handoffs: list, history });
    setTargets(list);
    setKind(parsed.kind);
    setLastN(parsed.kind === 'last_n' ? parsed.n : DEFAULT_LAST_N);
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [{ data: body }, { data: rows }] = await Promise.all([
        getAgentHandoffs(agentId), getAgents(workspace),
      ]);
      apply(body);
      const list = Array.isArray(rows) ? rows : (rows?.agents || rows?.items || []);
      setAgents(list.filter((a) => (a.id || a) !== agentId));
    } catch (e) {
      setError(e?.response?.data?.detail || tRef.current('handoffs.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [agentId, workspace, apply]);

  useEffect(() => { load(); }, [load]);

  const nValid = Number.isInteger(lastN) && lastN >= 1 && lastN <= MAX_LAST_N;
  const history = formatHistoryFilter(kind, lastN);
  const dirty = !sameList(targets, saved.handoffs) || history !== saved.history;
  // A target saved earlier that this workspace no longer lists still shows,
  // so it can be removed.
  const known = useMemo(() => new Set(agents.map((a) => a.id || a)), [agents]);
  const orphans = targets.filter((id) => !known.has(id));

  const toggle = (id) => {
    setMessage('');
    setTargets((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const save = async () => {
    if (kind === 'last_n' && !nValid) {
      setError(t('handoffs.lastNInvalid', { max: MAX_LAST_N }));
      return;
    }
    setSaving(true);
    setMessage('');
    setError('');
    try {
      const { data: body } = await updateAgentHandoffs(agentId, { handoffs: targets, handoff_history: history });
      apply(body);
      setMessage(t('handoffs.saved'));
      if (onSaved) onSaved();
    } catch (e) {
      setError(e?.response?.data?.detail || t('handoffs.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const blocked = saving || !dirty || loading;

  return (
    <div className="bg-white p-6 shadow-md rounded-lg mt-6" data-testid="handoffs-card">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <ArrowRightLeft className="w-5 h-5 text-indigo-600" />
          <h3 className="text-lg font-bold text-gray-900">{t('handoffs.title')}</h3>
        </div>
        <button
          type="button"
          onClick={save}
          disabled={blocked}
          className={`px-4 py-2 rounded-lg text-sm font-semibold ${
            blocked ? 'bg-gray-100 text-gray-400 cursor-not-allowed' : 'bg-indigo-600 text-white hover:bg-indigo-700'
          }`}
        >
          {saving ? t('common.saving') : t('handoffs.save')}
        </button>
      </div>
      <p className="text-sm text-gray-600 mb-4">{t('handoffs.intro')}</p>

      {loading ? (
        <Loader className="w-4 h-4 animate-spin text-gray-400" />
      ) : (
        <div className="space-y-6">
          <div>
            <h4 className="text-sm font-semibold text-gray-800 mb-1">{t('handoffs.targetsTitle')}</h4>
            <p className="text-xs text-gray-500 mb-3">
              {targets.length ? t('handoffs.targetsCount', { count: targets.length }) : t('handoffs.noTargets')}
            </p>
            {agents.length === 0 && orphans.length === 0 ? (
              <p className="text-sm text-gray-500 italic">{t('handoffs.noAgents')}</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {[...agents.map((a) => ({ id: a.id || a, name: a.name, description: a.description })),
                  ...orphans.map((id) => ({ id, name: id, description: t('handoffs.notInWorkspace') }))]
                  .map((a) => {
                    const on = targets.includes(a.id);
                    return (
                      <label
                        key={a.id}
                        className="p-3 border border-gray-100 rounded-lg bg-white flex items-center justify-between gap-3 cursor-pointer"
                      >
                        <span className="min-w-0">
                          <span className="block text-sm font-semibold text-gray-800 truncate">{a.name || a.id}</span>
                          <span className="block text-xs text-gray-500 mt-1 truncate">{a.description || a.id}</span>
                        </span>
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={() => toggle(a.id)}
                          aria-label={t('handoffs.allowTarget', { agent: a.name || a.id })}
                        />
                      </label>
                    );
                  })}
              </div>
            )}
          </div>

          <div>
            <h4 className="text-sm font-semibold text-gray-800 mb-1">{t('handoffs.historyTitle')}</h4>
            <p className="text-xs text-gray-500 mb-3">{t('handoffs.historyIntro')}</p>
            <div className="flex items-center gap-2 flex-wrap">
              <select
                value={kind}
                onChange={(e) => { setKind(e.target.value); setMessage(''); }}
                aria-label={t('handoffs.historyTitle')}
                className={selectCls}
              >
                {FILTER_KINDS.map((k) => <option key={k} value={k}>{t(`handoffs.filters.${k}`)}</option>)}
              </select>
              {kind === 'last_n' && (
                <label className="flex items-center gap-2 text-sm text-gray-700">
                  <input
                    type="number"
                    min={1}
                    max={MAX_LAST_N}
                    value={Number.isNaN(lastN) ? '' : lastN}
                    onChange={(e) => { setLastN(parseInt(e.target.value, 10)); setMessage(''); }}
                    aria-label={t('handoffs.lastNLabel')}
                    className={`${selectCls} w-20`}
                  />
                  {t('handoffs.lastNUnit')}
                </label>
              )}
            </div>
            <p className="text-xs text-gray-500 mt-2">{t(`handoffs.filterHints.${kind}`)}</p>
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
