import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  FlaskConical, Plus, Play, Trash2, Loader, ChevronRight, ChevronDown,
  AlertTriangle, DollarSign, CheckCircle, XCircle, X, Save, History, GitCompare,
} from 'lucide-react';
import {
  getEvalSets, createEvalSet, getEvalSet, deleteEvalSet, addEvalCase,
  deleteEvalCase, estimateEvalRun, runEvalSet, getEvalRuns, getEvalRun,
  getEvalRunDiff, getEvalGraders, getAgents,
  getEvalChat, clearEvalChat, stopEvalChat, evalChatUrl,
} from '../api';
import { useWorkspace } from '../components/workspace';
import EntityChat from '../components/EntityChat';
import { usePageChat } from '../components/pageChat/pageChat';
import { ChatColumn, ChatToggle, FILL_COLUMN, useChatColumn } from '../components/ChatColumn';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
/**
 * Evals — batch replay across cases x configs, scored by graders.
 *
 * Two panes: the dataset (cases + graders) on the left, the score matrix on the
 * right. Every cell shows its score *and* opens the raw output, because a
 * grader -- especially an LLM judge -- is itself unreliable and a number on its
 * own is not evidence.
 */

const scoreColor = (score, ok = true) => {
  if (!ok) return 'bg-gray-100 text-gray-500 border-gray-200';
  if (score >= 0.9) return 'bg-green-100 text-green-800 border-green-300';
  if (score >= 0.6) return 'bg-amber-100 text-amber-800 border-amber-300';
  if (score > 0) return 'bg-orange-100 text-orange-800 border-orange-300';
  return 'bg-red-100 text-red-800 border-red-300';
};

const pct = (n) => `${Math.round((n || 0) * 100)}%`;

/**
 * The Eval Agent's chat, as one descriptor. Workspace-level rather than
 * per-set: the first thing anyone wants is a set that does not exist yet, which
 * a chat pinned to a row could not build.
 *
 * A descriptor rather than a component because the same conversation has two
 * possible homes — the column beside the page, and the floating page-chat
 * panel. Both read this, so they can never become two chats.
 *
 * The callbacks are memoised on the workspace because EntityChat loads its
 * transcript in an effect keyed on them.
 */
function useEvalChatDescriptor(workspace, onChanged) {
  const { t } = useI18n();

  const loadChat = useCallback(() => getEvalChat(workspace), [workspace]);
  const clearChat = useCallback(() => clearEvalChat(workspace), [workspace]);
  const stopChat = useCallback(() => stopEvalChat(workspace), [workspace]);

  const onEvent = useCallback((ev) => {
    if (ev.type === 'evals') onChanged();
  }, [onChanged]);

  return useMemo(() => ({
    scope: `evals:${workspace || ''}`,
    path: evalChatUrl(workspace),
    loadChat, clearChat, stopChat, onEvent,
    title: t('evals.agentChat'),
    emptyHint: t('evals.agentChatHint'),
    suggestions: [
      t('evals.chatSuggestBuild'),
      t('evals.chatSuggestCompare'),
      t('evals.chatSuggestExplain'),
    ],
  }), [workspace, loadChat, clearChat, stopChat, onEvent, t]);
}

export default function Evals() {
  const { t } = useI18n();
  const { selectedWorkspace: currentWorkspace } = useWorkspace();
  const [sets, setSets] = useState([]);
  const [selected, setSelected] = useState(null);
  const [runs, setRuns] = useState([]);
  const [activeRun, setActiveRun] = useState(null);
  const [graderCatalog, setGraderCatalog] = useState([]);
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [estimate, setEstimate] = useState(null);
  const [message, setMessage] = useState('');
  const [showCreate, setShowCreate] = useState(false);
  const [showCase, setShowCase] = useState(false);
  const [cellDetail, setCellDetail] = useState(null);
  const [expandedCases, setExpandedCases] = useState({});
  const [diffResult, setDiffResult] = useState(null);
  const [diffing, setDiffing] = useState(false);

  // Run configuration: which agent/model columns the sweep compares.
  const [configs, setConfigs] = useState([]);
  const [costCeiling, setCostCeiling] = useState('');
  const chat = useChatColumn(false);

  const loadSets = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await getEvalSets(currentWorkspace);
      setSets(data.eval_sets || []);
    } catch {
      setSets([]);
    } finally {
      setLoading(false);
    }
  }, [currentWorkspace]);

  useEffect(() => { loadSets(); }, [loadSets]);

  // The same chat the column shows, offered to the floating panel as well.
  const evalChat = useEvalChatDescriptor(currentWorkspace, loadSets);
  usePageChat(evalChat);

  useEffect(() => {
    (async () => {
      try {
        const [g, a] = await Promise.all([getEvalGraders(), getAgents(currentWorkspace)]);
        setGraderCatalog(g.data.graders || []);
        setAgents(a.data.agents || a.data || []);
      } catch { /* catalogs are optional decoration */ }
    })();
  }, [currentWorkspace]);

  const selectSet = async (id) => {
    setActiveRun(null);
    setEstimate(null);
    setMessage('');
    setDiffResult(null);
    try {
      const [{ data: set }, { data: hist }] = await Promise.all([
        getEvalSet(id), getEvalRuns(id),
      ]);
      setSelected(set);
      setRuns(hist.eval_runs || []);
      setConfigs(set.agent_id
        ? [{ agent_id: set.agent_id, provider: '', model: '', label: 'baseline', repeats: 1 }]
        : []);
      if ((hist.eval_runs || []).length) {
        const { data: latest } = await getEvalRun(hist.eval_runs[0].eval_run_id);
        setActiveRun(latest);
      }
    } catch {
      setMessage(t('evals.loadFailed'));
    }
  };

  // The run right before the one on screen, in this set's history (newest
  // first) — what "compare with previous" means without asking the user to
  // pick two runs by hand.
  const previousRun = activeRun
    ? runs[runs.findIndex((r) => r.eval_run_id === activeRun.eval_run_id) + 1]
    : null;

  const handleCompareWithPrevious = async () => {
    if (!activeRun || !previousRun) return;
    setDiffing(true);
    setMessage('');
    try {
      const { data } = await getEvalRunDiff(previousRun.eval_run_id, activeRun.eval_run_id);
      setDiffResult(data);
    } catch (e) {
      setMessage(e.response?.data?.detail || t('evals.diffFailed'));
    } finally {
      setDiffing(false);
    }
  };

  const handleEstimate = async () => {
    if (!selected) return;
    try {
      const { data } = await estimateEvalRun(selected.eval_set_id, { configs });
      setEstimate(data);
    } catch (e) {
      setMessage(e.response?.data?.detail || t('evals.estimateFailed'));
    }
  };

  const handleRun = async () => {
    if (!selected) return;
    setRunning(true);
    setMessage('');
    setDiffResult(null);
    try {
      const { data } = await runEvalSet(selected.eval_set_id, {
        configs,
        workspace: currentWorkspace,
        cost_ceiling: costCeiling ? parseFloat(costCeiling) : null,
      });
      setActiveRun(data);
      const { data: hist } = await getEvalRuns(selected.eval_set_id);
      setRuns(hist.eval_runs || []);
      if (data.status === 'stopped') setMessage(`Sweep stopped: ${data.error}`);
    } catch (e) {
      setMessage(e.response?.data?.detail || t('evals.runFailed'));
    } finally {
      setRunning(false);
    }
  };

  const handleDeleteSet = async (id) => {
    await deleteEvalSet(id);
    if (selected?.eval_set_id === id) { setSelected(null); setActiveRun(null); }
    loadSets();
  };

  const configLabels = (activeRun?.configs || []).map((c) => c.label);

  return (
    <PageContainer>
      <PageHeader
        icon={FlaskConical}
        title={t('evals.evals')}
        description={t('evals.runAFixedSetOf')}
        actions={<>
          <ChatToggle open={chat.open} onToggle={chat.toggle}
                      label={t('evals.agentChat')} />
          <button
            onClick={() => setShowCreate(true)}
            className="inline-flex items-center px-3 py-2 text-sm font-medium text-white bg-indigo-600 rounded-lg hover:bg-indigo-700"
          >
            <Plus className="w-4 h-4 mr-1.5" /> {t('evals.newEvalSet')}
          </button>
        </>}
      />

      {message && (
        <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" /> {message}
        </div>
      )}

      <div className={chat.gridClass}>
        <div className={chat.mainClass}>
      {/* The set list keeps its width whether or not the chat is open; the
          matrix beside it is the fluid pane and absorbs the difference. */}
      <div className="grid grid-cols-1 lg:grid-cols-[320px_1fr] gap-6">
        {/* ── Eval sets list ── */}
        <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm h-fit">
          <h2 className="text-sm font-bold text-gray-700 uppercase tracking-wide mb-3">{t('evals.evalSets')}</h2>
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-gray-500 py-4">
              <Loader className="w-4 h-4 animate-spin" /> {t('evals.loading')}
            </div>
          ) : sets.length === 0 ? (
            <p className="text-sm text-gray-500 italic py-4">
              No eval sets yet. Create one, then seed it with cases from real runs
              on the Messages page.
            </p>
          ) : (
            <ul className="space-y-1">
              {sets.map((s) => (
                <li key={s.eval_set_id}>
                  <div
                    className={`w-full flex items-center justify-between gap-2 px-3 py-2 rounded-lg text-left transition-colors ${
                      selected?.eval_set_id === s.eval_set_id
                        ? 'bg-indigo-50 border border-indigo-200'
                        : 'hover:bg-gray-50 border border-transparent'
                    }`}
                  >
                    <button onClick={() => selectSet(s.eval_set_id)} className="min-w-0 flex-1 text-left">
                      <div className="text-sm font-semibold text-gray-900 truncate">{s.name}</div>
                      <div className="text-xs text-gray-500">
                        {s.case_count} case{s.case_count === 1 ? '' : 's'}
                        {s.agent_id ? ` · ${s.agent_id}` : ''}
                      </div>
                    </button>
                    <button
                      onClick={() => handleDeleteSet(s.eval_set_id)}
                      title={t('evals.deleteEvalSet')}
                      className="p-1 text-gray-400 hover:text-red-600 shrink-0"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* ── Selected set ── */}
        <div className="space-y-6">
          {!selected ? (
            <div className="bg-white rounded-xl border border-gray-200 p-10 text-center text-sm text-gray-500">
              {t('evals.selectAnEvalSetTo')}
            </div>
          ) : (
            <>
              {/* Run controls */}
              <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
                <div className="flex items-start justify-between gap-4 mb-4">
                  <div className="min-w-0">
                    <h2 className="text-lg font-bold text-gray-900">{selected.name}</h2>
                    {selected.description && (
                      <p className="text-sm text-gray-500 mt-0.5">{selected.description}</p>
                    )}
                    <p className="text-xs text-gray-400 mt-1">
                      {selected.cases.length} cases ·{' '}
                      {selected.graders.map((g) => g.kind).join(', ') || 'no graders'}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button
                      onClick={handleEstimate}
                      disabled={!configs.length}
                      className="inline-flex items-center px-3 py-2 text-xs font-semibold text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-lg hover:bg-indigo-100 disabled:opacity-50"
                    >
                      <DollarSign className="w-3.5 h-3.5 mr-1" /> {t('evals.estimate')}
                    </button>
                    <button
                      onClick={handleRun}
                      disabled={running || !configs.length || !selected.cases.length}
                      className="inline-flex items-center px-3 py-2 text-xs font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                    >
                      {running
                        ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" />
                        : <Play className="w-3.5 h-3.5 mr-1" />}
                      {running ? t('common.running') : t('evals.runSweep')}
                    </button>
                  </div>
                </div>

                {/* Config columns */}
                <div className="border-t border-gray-100 pt-4">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-bold text-gray-500 uppercase">
                      Configs (matrix columns)
                    </span>
                    <button
                      onClick={() => setConfigs([...configs, {
                        agent_id: selected.agent_id || agents[0]?.id || '',
                        provider: '', model: '', label: '', repeats: 1,
                      }])}
                      className="text-xs font-semibold text-indigo-600 hover:text-indigo-800"
                    >
                      {t('evals.addConfig')}
                    </button>
                  </div>
                  {configs.length === 0 && (
                    <p className="text-xs text-gray-500 italic">
                      {t('evals.addAtLeastOneAgent')}
                    </p>
                  )}
                  <div className="space-y-2">
                    {configs.map((c, i) => (
                      <div key={i} className="flex items-center gap-2">
                        <select
                          value={c.agent_id}
                          onChange={(e) => {
                            const next = [...configs];
                            next[i] = { ...c, agent_id: e.target.value };
                            setConfigs(next);
                          }}
                          className="text-xs border border-gray-300 rounded-md px-2 py-1.5 flex-1 min-w-0"
                        >
                          <option value="">{t('evals.selectAgent')}</option>
                          {agents.map((a) => (
                            <option key={a.id} value={a.id}>{a.name || a.id}</option>
                          ))}
                        </select>
                        <input
                          value={c.model}
                          onChange={(e) => {
                            const next = [...configs];
                            next[i] = { ...c, model: e.target.value };
                            setConfigs(next);
                          }}
                          placeholder={t('evals.modelOverrideBlankAgentDefault')}
                          className="text-xs border border-gray-300 rounded-md px-2 py-1.5 flex-1 min-w-0"
                        />
                        <input
                          value={c.label}
                          onChange={(e) => {
                            const next = [...configs];
                            next[i] = { ...c, label: e.target.value };
                            setConfigs(next);
                          }}
                          placeholder={t('evals.columnLabel')}
                          className="text-xs border border-gray-300 rounded-md px-2 py-1.5 w-36"
                        />
                        <input
                          type="number" min="1" max="10"
                          value={c.repeats || 1}
                          onChange={(e) => {
                            const next = [...configs];
                            const n = parseInt(e.target.value, 10);
                            next[i] = { ...c, repeats: Number.isFinite(n) ? n : 1 };
                            setConfigs(next);
                          }}
                          title={t('evals.repeatsHint')}
                          className="text-xs border border-gray-300 rounded-md px-2 py-1.5 w-16"
                        />
                        <button
                          onClick={() => setConfigs(configs.filter((_, j) => j !== i))}
                          className="p-1 text-gray-400 hover:text-red-600"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                  <div className="flex items-center gap-2 mt-3">
                    <label className="text-xs text-gray-500">{t('evals.costCeiling')}</label>
                    <input
                      value={costCeiling}
                      onChange={(e) => setCostCeiling(e.target.value)}
                      placeholder={t('evals.none')}
                      className="text-xs border border-gray-300 rounded-md px-2 py-1 w-24"
                    />
                    <span className="text-xs text-gray-400">
                      {t('evals.stopsTheWholeSweepNot')}
                    </span>
                  </div>
                </div>

                {estimate && (
                  <div className="mt-4 rounded-lg border border-indigo-100 bg-indigo-50 p-3">
                    <div className="text-xs font-bold text-indigo-900 mb-1">
                      {t('evals.projectedSpend')}: ${estimate.estimated_total_cost.toFixed(4)}
                    </div>
                    <div className="text-xs text-indigo-800">
                      {t('evals.estimateBreakdown', { cases: estimate.cases, configs: estimate.configs, calls: estimate.llm_calls })}
                      {estimate.uses_llm_judge && (
                        <> · {t('evals.includesJudgeCost', { cost: estimate.estimated_grader_cost.toFixed(4) })}</>
                      )}
                    </div>
                    <div className="text-[11px] text-indigo-600 mt-1">
                      {t(`evals.${estimate.note_key}`, { defaultValue: estimate.note })}
                    </div>
                  </div>
                )}
              </div>

              {/* Cases */}
              <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide">{t('evals.cases')}</h3>
                  <button
                    onClick={() => setShowCase(true)}
                    className="text-xs font-semibold text-indigo-600 hover:text-indigo-800"
                  >
                    {t('evals.addCase2')}
                  </button>
                </div>
                {selected.cases.length === 0 ? (
                  <p className="text-sm text-gray-500 italic">
                    {t('evals.noCasesYet')}
                  </p>
                ) : (
                  <ul className="divide-y divide-gray-100">
                    {selected.cases.map((c) => (
                      <li key={c.case_id} className="py-2">
                        <div className="flex items-start justify-between gap-3">
                          <button
                            onClick={() => setExpandedCases((p) => ({ ...p, [c.case_id]: !p[c.case_id] }))}
                            className="flex items-start gap-1.5 min-w-0 flex-1 text-left"
                          >
                            {expandedCases[c.case_id]
                              ? <ChevronDown className="w-3.5 h-3.5 mt-0.5 text-gray-400 shrink-0" />
                              : <ChevronRight className="w-3.5 h-3.5 mt-0.5 text-gray-400 shrink-0" />}
                            <span className="text-sm text-gray-800 truncate">{c.input || '(empty)'}</span>
                          </button>
                          <button
                            onClick={async () => {
                              await deleteEvalCase(selected.eval_set_id, c.case_id);
                              selectSet(selected.eval_set_id);
                              loadSets();
                            }}
                            className="p-1 text-gray-400 hover:text-red-600 shrink-0"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                        {expandedCases[c.case_id] && (
                          <div className="ml-5 mt-2 space-y-2 text-xs">
                            <div>
                              <div className="font-semibold text-gray-500 uppercase text-[10px]">{t('evals.input')}</div>
                              <pre className="whitespace-pre-wrap text-gray-700 bg-gray-50 rounded p-2 mt-0.5">{c.input}</pre>
                            </div>
                            {c.expected && (
                              <div>
                                <div className="font-semibold text-gray-500 uppercase text-[10px]">{t('evals.expected')}</div>
                                <pre className="whitespace-pre-wrap text-gray-700 bg-gray-50 rounded p-2 mt-0.5">{c.expected}</pre>
                              </div>
                            )}
                            {c.rubric && (
                              <div>
                                <div className="font-semibold text-gray-500 uppercase text-[10px]">{t('evals.rubric')}</div>
                                <pre className="whitespace-pre-wrap text-gray-700 bg-gray-50 rounded p-2 mt-0.5">{c.rubric}</pre>
                              </div>
                            )}
                            {c.source_run_id && (
                              <a href={`/messages/${c.source_run_id}`} className="text-indigo-600 hover:underline">
                                seeded from run {c.source_run_id.slice(0, 12)}
                              </a>
                            )}
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* Score matrix */}
              {activeRun && (
                <div className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm">
                  <div className="flex items-center justify-between mb-4">
                    <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide">
                      {t('evals.scoreMatrix')}
                    </h3>
                    <div className="flex items-center gap-3">
                      <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${
                        activeRun.status === 'completed' ? 'bg-green-100 text-green-700'
                        : activeRun.status === 'stopped' ? 'bg-amber-100 text-amber-700'
                        : 'bg-red-100 text-red-700'
                      }`}>
                        {activeRun.status}
                      </span>
                      <span className="text-xs text-gray-500">
                        ${(activeRun.total_cost || 0).toFixed(4)}
                      </span>
                      {runs.length > 1 && (
                        <select
                          onChange={async (e) => {
                            const { data } = await getEvalRun(e.target.value);
                            setActiveRun(data);
                            setDiffResult(null);
                          }}
                          value={activeRun.eval_run_id}
                          className="text-xs border border-gray-300 rounded-md px-2 py-1"
                        >
                          {runs.map((r) => (
                            <option key={r.eval_run_id} value={r.eval_run_id}>
                              {new Date(r.started_at).toLocaleString()}
                            </option>
                          ))}
                        </select>
                      )}
                      {previousRun && (
                        <button
                          onClick={handleCompareWithPrevious}
                          disabled={diffing}
                          title={t('evals.compareWithPreviousHint')}
                          className="inline-flex items-center px-2.5 py-1 text-xs font-semibold text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-lg hover:bg-indigo-100 disabled:opacity-50"
                        >
                          {diffing
                            ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" />
                            : <GitCompare className="w-3.5 h-3.5 mr-1" />}
                          {t('evals.compareWithPrevious')}
                        </button>
                      )}
                    </div>
                  </div>

                  {diffResult && (
                    <div className="mb-5 rounded-lg border border-gray-200 p-3">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-xs font-bold text-gray-700 uppercase tracking-wide">
                          {t('evals.diffResult')}
                        </span>
                        <button onClick={() => setDiffResult(null)} className="p-1 text-gray-400 hover:text-gray-700">
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </div>
                      <div className="flex flex-wrap items-center gap-2 text-xs mb-2">
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-green-100 text-green-800 font-semibold">
                          {t('evals.fixedCount', { count: diffResult.summary.fixed })}
                        </span>
                        <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-red-100 text-red-800 font-semibold">
                          {t('evals.regressedCount', { count: diffResult.summary.regressed })}
                        </span>
                        <span className="text-gray-500">
                          {t('evals.sameCount', { count: diffResult.summary.same })}
                        </span>
                        <span className="text-gray-400 ml-auto">
                          {pct(diffResult.summary.pass_rate_a)} → {pct(diffResult.summary.pass_rate_b)}
                        </span>
                      </div>
                      {diffResult.cases.filter((c) => c.change === 'fixed' || c.change === 'regressed').length === 0 ? (
                        <p className="text-xs text-gray-500 italic">{t('evals.noChangedCases')}</p>
                      ) : (
                        <ul className="space-y-1">
                          {diffResult.cases
                            .filter((c) => c.change === 'fixed' || c.change === 'regressed')
                            .map((c) => (
                              <li key={`${c.case_id}-${c.config_a || c.config_b}`} className="flex items-center gap-2 text-xs">
                                {c.change === 'fixed'
                                  ? <CheckCircle className="w-3.5 h-3.5 text-green-600 shrink-0" />
                                  : <XCircle className="w-3.5 h-3.5 text-red-600 shrink-0" />}
                                <span className="font-mono text-gray-500">{c.case_id.slice(0, 12)}</span>
                                <span className="text-gray-400">·</span>
                                <span className="text-gray-700">{c.config_a || c.config_b}</span>
                              </li>
                            ))}
                        </ul>
                      )}
                    </div>
                  )}

                  {/* Per-config headline: the one number, with the counts behind it. */}
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-5">
                    {configLabels.map((label) => {
                      const s = activeRun.summary[label] || {};
                      const repeated = (s.total || 0) > Object.keys(s.cases || {}).length;
                      return (
                        <div key={label} className="rounded-lg border border-gray-200 p-3">
                          <div className="text-xs font-semibold text-gray-500 truncate">{label}</div>
                          <div className="text-2xl font-bold text-gray-900 mt-1">{pct(s.score)}</div>
                          <div className="text-xs text-gray-500 mt-0.5">
                            {s.passed || 0}/{s.total || 0} passed
                            {s.errors ? ` · ${s.errors} errored` : ''}
                          </div>
                          {repeated && (
                            <div className="text-[11px] text-gray-500 mt-0.5">
                              {t('evals.passRate')} {pct(s.pass_rate)} · {t('evals.std')} {(s.std || 0).toFixed(2)}
                              {s.unstable_cases > 0 && (
                                <span className="ml-1.5 inline-flex items-center px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-800 font-semibold">
                                  {t('evals.unstableCases', { count: s.unstable_cases })}
                                </span>
                              )}
                            </div>
                          )}
                          <div className="text-[11px] text-gray-400 mt-0.5">
                            ${(s.cost || 0).toFixed(4)}
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-gray-200">
                          <th className="text-left py-2 pr-4 text-xs font-bold text-gray-500 uppercase">{t('evals.case')}</th>
                          {configLabels.map((l) => (
                            <th key={l} className="text-center py-2 px-2 text-xs font-bold text-gray-500 uppercase whitespace-nowrap">{l}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {(activeRun.cases || []).map((c) => (
                          <tr key={c.case_id} className="border-b border-gray-50">
                            <td className="py-2 pr-4 max-w-md">
                              <div className="text-xs text-gray-700 truncate">{c.input}</div>
                            </td>
                            {configLabels.map((label) => {
                              const cell = activeRun.matrix?.[c.case_id]?.[label];
                              if (!cell) {
                                return <td key={label} className="text-center py-2 px-2 text-xs text-gray-300">—</td>;
                              }
                              const caseStats = activeRun.summary?.[label]?.cases?.[c.case_id];
                              const repeated = caseStats && caseStats.attempts > 1;
                              const title = repeated
                                ? `${t('evals.passRate')} ${pct(caseStats.pass_rate)} · ${t('evals.std')} ${caseStats.std.toFixed(2)}`
                                : t('evals.openTheRawOutputBehind');
                              return (
                                <td key={label} className="text-center py-2 px-2">
                                  <button
                                    onClick={() => setCellDetail({ cell, case: c, label, caseStats })}
                                    title={title}
                                    className={`inline-flex items-center gap-1 px-2 py-1 rounded-md border text-xs font-semibold ${scoreColor(cell.score, cell.ok)}`}
                                  >
                                    {cell.ok
                                      ? (cell.passed ? <CheckCircle className="w-3 h-3" /> : <XCircle className="w-3 h-3" />)
                                      : <AlertTriangle className="w-3 h-3" />}
                                    {cell.ok ? pct(cell.score) : 'err'}
                                  </button>
                                  {repeated && caseStats.unstable && (
                                    <div className="mt-1">
                                      <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-800 text-[10px] font-semibold">
                                        {t('evals.unstable')}
                                      </span>
                                    </div>
                                  )}
                                </td>
                              );
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {!activeRun && runs.length === 0 && (
                <div className="bg-white rounded-xl border border-gray-200 p-6 text-center text-sm text-gray-500 flex items-center justify-center gap-2">
                  <History className="w-4 h-4" /> {t('evals.noSweepsYetConfigureThe')}
                </div>
              )}
            </>
          )}
        </div>
      </div>
        </div>

        {chat.open && (
          <ChatColumn>
            <EntityChat {...evalChat} {...FILL_COLUMN} />
          </ChatColumn>
        )}
      </div>

      {showCreate && (
        <CreateSetModal
          agents={agents}
          graderCatalog={graderCatalog}
          workspace={currentWorkspace}
          onClose={() => setShowCreate(false)}
          onCreated={(s) => { setShowCreate(false); loadSets(); selectSet(s.eval_set_id); }}
        />
      )}

      {showCase && selected && (
        <AddCaseModal
          evalSetId={selected.eval_set_id}
          onClose={() => setShowCase(false)}
          onAdded={() => { setShowCase(false); selectSet(selected.eval_set_id); loadSets(); }}
        />
      )}

      {cellDetail && (
        <CellDetailModal detail={cellDetail} onClose={() => setCellDetail(null)} />
      )}
    </PageContainer>
  );
}


function CreateSetModal({ agents, graderCatalog, workspace, onClose, onCreated }) {
  const { t } = useI18n();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [agentId, setAgentId] = useState('');
  const [graders, setGraders] = useState([{ kind: 'substring', params: {}, weight: 1 }]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    if (!name.trim()) { setError(t('evals.nameRequired')); return; }
    setSaving(true);
    try {
      const { data } = await createEvalSet({
        name, description, workspace, agent_id: agentId || null, cases: [], graders,
      });
      onCreated(data);
    } catch (e) {
      setError(e.response?.data?.detail || t('evals.createFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={t('evals.newEvalSet')} onClose={onClose}>
      {error && <div className="text-xs text-red-600 mb-3">{error}</div>}
      <Field label={t('evals.name')}>
        <input value={name} onChange={(e) => setName(e.target.value)}
               className="w-full text-sm border border-gray-300 rounded-md px-3 py-2" />
      </Field>
      <Field label={t('evals.description')}>
        <input value={description} onChange={(e) => setDescription(e.target.value)}
               className="w-full text-sm border border-gray-300 rounded-md px-3 py-2" />
      </Field>
      <Field label={t('evals.defaultAgent')} hint={t('evals.usedAsTheBaselineColumn')}>
        <select value={agentId} onChange={(e) => setAgentId(e.target.value)}
                className="w-full text-sm border border-gray-300 rounded-md px-3 py-2">
          <option value="">{t('evals.none2')}</option>
          {agents.map((a) => <option key={a.id} value={a.id}>{a.name || a.id}</option>)}
        </select>
      </Field>
      <Field label={t('evals.graders')} hint={t('evals.everyGraderMustPassFor')}>
        <div className="space-y-2">
          {graders.map((g, i) => (
            <div key={i} className="flex items-center gap-2">
              <select
                value={g.kind}
                onChange={(e) => {
                  const next = [...graders];
                  next[i] = { ...g, kind: e.target.value };
                  setGraders(next);
                }}
                className="text-sm border border-gray-300 rounded-md px-2 py-1.5 flex-1"
              >
                {graderCatalog.map((gc) => (
                  <option key={gc.kind} value={gc.kind}>
                    {gc.kind}{gc.costs_tokens ? ` ${t('evals.costsTokens')}` : ''}
                  </option>
                ))}
              </select>
              <input
                type="number" step="0.5" min="0" value={g.weight}
                onChange={(e) => {
                  const next = [...graders];
                  next[i] = { ...g, weight: parseFloat(e.target.value) || 0 };
                  setGraders(next);
                }}
                title={t('evals.weightInTheCombinedScore')}
                className="text-sm border border-gray-300 rounded-md px-2 py-1.5 w-20"
              />
              <button onClick={() => setGraders(graders.filter((_, j) => j !== i))}
                      className="p-1 text-gray-400 hover:text-red-600">
                <X className="w-4 h-4" />
              </button>
            </div>
          ))}
          <button
            onClick={() => setGraders([...graders, { kind: 'substring', params: {}, weight: 1 }])}
            className="text-xs font-semibold text-indigo-600 hover:text-indigo-800"
          >
            {t('evals.addGrader')}
          </button>
        </div>
      </Field>
      <ModalActions onClose={onClose} onSubmit={submit} saving={saving} label={t('evals.create')} />
    </Modal>
  );
}


function AddCaseModal({ evalSetId, onClose, onAdded }) {
  const { t } = useI18n();
  const [input, setInput] = useState('');
  const [expected, setExpected] = useState('');
  const [rubric, setRubric] = useState('');
  const [fromRunId, setFromRunId] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const submit = async () => {
    setSaving(true);
    try {
      await addEvalCase(evalSetId, {
        input, expected: expected || null, rubric: rubric || null,
        from_run_id: fromRunId || null,
      });
      onAdded();
    } catch (e) {
      setError(e.response?.data?.detail || t('evals.addCaseFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal title={t('evals.addCase')} onClose={onClose}>
      {error && <div className="text-xs text-red-600 mb-3">{error}</div>}
      <Field label={t('evals.seedFromRunId')} hint={t('evals.optionalPullsTheRunS')}>
        <input value={fromRunId} onChange={(e) => setFromRunId(e.target.value)}
               placeholder={t('evals.run')} className="w-full text-sm border border-gray-300 rounded-md px-3 py-2 font-mono" />
      </Field>
      <Field label={t('evals.input')} hint={t('evals.theUserMessageSentTo')}>
        <textarea value={input} onChange={(e) => setInput(e.target.value)} rows={3}
                  className="w-full text-sm border border-gray-300 rounded-md px-3 py-2" />
      </Field>
      <Field label={t('evals.expected')} hint={t('evals.referenceAnswerForTheDeterministic')}>
        <textarea value={expected} onChange={(e) => setExpected(e.target.value)} rows={3}
                  className="w-full text-sm border border-gray-300 rounded-md px-3 py-2" />
      </Field>
      <Field label={t('evals.rubric')} hint={t('evals.instructionForAnLlmJudge')}>
        <textarea value={rubric} onChange={(e) => setRubric(e.target.value)} rows={2}
                  className="w-full text-sm border border-gray-300 rounded-md px-3 py-2" />
      </Field>
      <ModalActions onClose={onClose} onSubmit={submit} saving={saving} label={t('evals.add')} />
    </Modal>
  );
}


function CellDetailModal({ detail, onClose }) {
  const { t } = useI18n();
  const { cell, case: c, label, caseStats } = detail;
  const repeated = caseStats && caseStats.attempts > 1;
  return (
    <Modal title={`${label} — ${c.input.slice(0, 60)}`} onClose={onClose} wide>
      <div className="space-y-4 text-sm">
        <div className="flex items-center gap-4">
          <div className={`px-3 py-1.5 rounded-lg border font-bold ${scoreColor(cell.score, cell.ok)}`}>
            {cell.ok ? pct(cell.score) : 'errored'}
          </div>
          <div className="text-xs text-gray-500">
            {cell.duration_ms}ms · {cell.inbound_tokens}+{cell.outbound_tokens} tokens ·
            ${cell.cost.toFixed(4)}
            {cell.attempt > 1 && ` · ${t('evals.attempt', { n: cell.attempt })}`}
          </div>
          {cell.run_id && (
            <a href={`/messages/${cell.run_id}`} className="text-xs text-indigo-600 hover:underline ml-auto">
              {t('evals.openFullRunTrace')}
            </a>
          )}
        </div>

        {repeated && (
          <div className="rounded-lg border border-gray-200 p-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-600">
            <span>{t('evals.attempts')}: {caseStats.attempts}</span>
            <span>{t('evals.passRate')}: {pct(caseStats.pass_rate)}</span>
            <span>{t('evals.std')}: {caseStats.std.toFixed(3)}</span>
            <span>{t('evals.range')}: {pct(caseStats.min)} {t('evals.to')} {pct(caseStats.max)}</span>
            {caseStats.unstable && (
              <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-800 font-semibold">
                {t('evals.unstable')}
              </span>
            )}
          </div>
        )}

        {cell.error && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-800">
            {cell.error}
          </div>
        )}

        {/* Per-grader breakdown: which check failed, not just that one did. */}
        {Object.keys(cell.scores || {}).length > 0 && (
          <div>
            <div className="text-xs font-bold text-gray-500 uppercase mb-1.5">{t('evals.graders')}</div>
            <ul className="space-y-1.5">
              {Object.entries(cell.scores).map(([kind, g]) => (
                <li key={kind} className="flex items-start gap-2 text-xs">
                  {g.passed
                    ? <CheckCircle className="w-3.5 h-3.5 text-green-600 mt-0.5 shrink-0" />
                    : <XCircle className="w-3.5 h-3.5 text-red-600 mt-0.5 shrink-0" />}
                  <div className="min-w-0">
                    <span className="font-semibold text-gray-800">{kind}</span>
                    <span className="text-gray-500"> — {pct(g.score)}</span>
                    <div className="text-gray-600">{g.detail}</div>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* The raw output always sits next to the number: a grader is itself
            unreliable, and the score alone is not evidence. */}
        <div>
          <div className="text-xs font-bold text-gray-500 uppercase mb-1.5">{t('evals.agentOutput')}</div>
          <pre className="whitespace-pre-wrap text-xs text-gray-800 bg-gray-50 rounded-lg p-3 max-h-72 overflow-auto">
            {cell.output || '(empty)'}
          </pre>
        </div>

        {c.expected && (
          <div>
            <div className="text-xs font-bold text-gray-500 uppercase mb-1.5">{t('evals.expected')}</div>
            <pre className="whitespace-pre-wrap text-xs text-gray-800 bg-gray-50 rounded-lg p-3 max-h-40 overflow-auto">
              {c.expected}
            </pre>
          </div>
        )}
      </div>
    </Modal>
  );
}


// ── Small shared shells ──────────────────────────────────────────────────────

function Modal({ title, children, onClose, wide }) {
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className={`bg-white rounded-xl shadow-xl w-full ${wide ? 'max-w-3xl' : 'max-w-lg'} max-h-[85vh] overflow-auto`}>
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200 sticky top-0 bg-white">
          <h3 className="text-sm font-bold text-gray-900 truncate">{title}</h3>
          <button onClick={onClose} className="p-1 text-gray-400 hover:text-gray-700">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

function Field({ label, hint, children }) {
  return (
    <div className="mb-4">
      <label className="block text-xs font-bold text-gray-500 uppercase mb-1">{label}</label>
      {hint && <p className="text-xs text-gray-400 mb-1.5">{hint}</p>}
      {children}
    </div>
  );
}

function ModalActions({ onClose, onSubmit, saving, label }) {
  const { t } = useI18n();
  return (
    <div className="flex justify-end gap-2 pt-2">
      <button onClick={onClose} className="px-3 py-2 text-sm font-semibold text-gray-600 hover:text-gray-900">
        {t('evals.cancel')}
      </button>
      <button
        onClick={onSubmit} disabled={saving}
        className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
      >
        {saving ? <Loader className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}
        {label}
      </button>
    </div>
  );
}
