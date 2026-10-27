import CapabilityBanner from './CapabilityBanner';
import SystemAgentWarning from './SystemAgentWarning';
import { InfoHint } from '../PageLayout';
import ToolVisibilityCard from './ToolVisibilityCard';
import { packToolGroups } from './toolGroupLayout';
import { AlertTriangle, ChevronDown, ChevronRight, Loader, Lock, Save, Search, Share2, ShieldCheck, Wrench, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import useToolPolicy, { TOOL_POLICY_INHERIT } from './useToolPolicy';
import useWorkspaceIsolation from '../workspace/useWorkspaceIsolation';
import { useAgentPage } from './context';
import { Link } from 'react-router-dom';
import { getWorkspaceRoles } from '../../api';
import { toolCategoryLabel, toolDescription, toolLabel } from '../../lib/toolText';

// The tools the delegates allowlist applies to (tools/langchain_tools.py,
// _delegation_blocked, and tools/delegation.py): with none of them on, the
// allowlist has nothing to restrict and the card is shown inactive.
const DELEGATION_TOOLS = ['run_agent_tool', 'delegate_task_tool', 'assign_agent_tool'];

// Every tool card has one width and one height (a title of up to two lines,
// a description of up to three, the policy select). The width is set so two
// groups that together hold a row's worth of cards (2 + 2, 3 + 1) fill a row
// exactly; a group alone in its row spans the whole width, its cards centred.
const CARD_MIN_REM = 13;
const CARD_GAP_PX = 12; // between two cards of a group
const GROUP_EDGE_PX = 13; // a group's padding 12 + border 1
const GROUP_GAP_PX = 20; // between two groups in a row
const GROUP_ROW_GAP_PX = 24; // between two rows of groups
const CARD_HEIGHT = '8.75rem';

const DECISION_CLS = {
  run: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  deny: 'bg-red-50 text-red-700 border-red-200',
  ask: 'bg-amber-50 text-amber-800 border-amber-200',
};

/** Tools, their permission policy and capabilities, and who this agent may delegate to. */
export default function ToolsTab() {
  const {
    agent, allAgents, capabilityOverridden, capabilityViolation, capabilityOverride,
    capabilityOverrideSaving, capabilityGuardInfo, capabilitySoftened, handleToggleCapabilityOverride,
    delegatesViolation, toolsServerWarning, autoTools, delegates, delegatesDirty, delegatesMessage,
    delegatesSaving, markDelegatesDirty, handleSaveDelegates, handleSaveTools, id,
    regularToolIds, selectedTools, setCategoryTools, setDelegates, setDelegatesMessage, t,
    toggleDelegate, toggleTool, toolCategories, toolsDirty, toolsMessage, toolsMeta, toolsSaving,
    fetchData, selectedWorkspace,
  } = useAgentPage();

  // Workspace roles (agents/roles.py): `@coder` and the like may be allowed
  // like an agent, and reach whichever agent holds the role in the workspace.
  const [roleRows, setRoleRows] = useState([]);
  const rolesWorkspace = selectedWorkspace || agent?.owner_workspace || 'default';
  useEffect(() => {
    let cancelled = false;
    getWorkspaceRoles(rolesWorkspace)
      .then(({ data }) => { if (!cancelled) setRoleRows(data.roles || []); })
      .catch(() => { if (!cancelled) setRoleRows([]); });
    return () => { cancelled = true; };
  }, [rolesWorkspace]);

  // The per-tool permission policy, edited inside each tool's card, with its
  // default in the card header. Saved together with the tool list.
  const policy = useToolPolicy({
    agentId: id,
    workspace: selectedWorkspace || agent?.owner_workspace || undefined,
    onSaved: fetchData,
    loadFailedText: t('toolPolicy.loadFailed'),
    saveFailedText: t('toolPolicy.saveFailed'),
  });

  // When the workspace is isolated, a tool outside its allowlist cannot be
  // added (common/isolation.py check_agent_tools, 400 at save time): show it
  // disabled here rather than let the save round trip just to refuse it.
  const isolation = useWorkspaceIsolation(selectedWorkspace || agent?.owner_workspace || undefined);
  const isolationAllowed = isolation.data?.isolated ? new Set(isolation.data.allowed_tools || []) : null;
  const isolationBlocked = (toolId) => Boolean(isolationAllowed) && !isolationAllowed.has(toolId);
  const modeLabel = (mode) => t(`toolPolicy.modes.${mode}`);
  const sourceLabel = (source) => t(`toolPolicy.sources.${source}`);

  const [decisionsOpen, setDecisionsOpen] = useState(false);

  // The search in the header narrows the groups and the added tools to the
  // cards whose name, description, id or group matches every word typed.
  const [toolQuery, setToolQuery] = useState('');
  const queryWords = toolQuery.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const matchesQuery = (...texts) => {
    if (!queryWords.length) return true;
    const hay = texts.filter(Boolean).join(' ').toLowerCase();
    return queryWords.every((w) => hay.includes(w));
  };
  const shownCategories = toolCategories
    .map(({ category, ids }) => ({
      category,
      ids,
      shown: ids.filter((tool) => matchesQuery(
        tool, toolLabel(t, tool, toolsMeta[tool]?.label),
        toolDescription(t, tool, toolsMeta[tool]?.description), toolCategoryLabel(t, category),
      )),
    }))
    .filter(({ shown }) => shown.length > 0);
  const shownAutoTools = (Array.isArray(autoTools) ? autoTools : []).filter((tool) => matchesQuery(
    tool.id, toolLabel(t, tool.id, tool.label), toolDescription(t, tool.id, tool.description),
    t(`agentDetails.autoToolReasons.${tool.reason}`),
  ));

  // Groups are as many cards wide as they have cards, at most a row's worth,
  // ordered so as many rows as possible are whole (toolGroupLayout.js). A
  // row may end short of the edge: cards never stretch to fill it.
  const groupsRef = useRef(null);
  const [groupsWidth, setGroupsWidth] = useState(0);
  const [remPx, setRemPx] = useState(16);
  useEffect(() => {
    const el = groupsRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const observer = new ResizeObserver(([entry]) => {
      setRemPx(parseFloat(getComputedStyle(document.documentElement).fontSize) || 16);
      setGroupsWidth(entry.contentRect.width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [toolCategories.length]);
  // What a row of two groups leaves for its cards and the gaps between them.
  const twoGroupRoom = groupsWidth - 4 * GROUP_EDGE_PX - GROUP_GAP_PX;
  const cardMin = CARD_MIN_REM * remPx;
  // Until the section is measured (and in tests), four cards a row.
  const groupCols = !groupsWidth ? 4
    : Math.max(1, Math.floor((twoGroupRoom + 2 * CARD_GAP_PX) / (cardMin + CARD_GAP_PX)));
  const cardWidth = !groupsWidth ? null
    : groupCols >= 2 ? (twoGroupRoom - (groupCols - 2) * CARD_GAP_PX) / groupCols
      : groupsWidth - 2 * GROUP_EDGE_PX;
  const groupWidth = (cards) => cards * cardWidth + (cards - 1) * CARD_GAP_PX + 2 * GROUP_EDGE_PX;
  const packedCategories = packToolGroups(
    shownCategories.map((group) => {
      const span = Math.min(group.shown.length, groupCols);
      return { ...group, span, width: cardWidth ? groupWidth(span) : 0 };
    }),
    {
      cols: groupCols,
      // Side by side in one row: by their real widths once the section is
      // measured, by card count before.
      fits: (items) => (cardWidth
        ? items.reduce((sum, g) => sum + g.width, 0) + (items.length - 1) * GROUP_GAP_PX <= groupsWidth + 0.5
        : items.reduce((sum, g) => sum + g.span, 0) <= groupCols),
    },
  );
  const cardColumns = (n) => (cardWidth ? `repeat(${n}, ${cardWidth}px)` : `repeat(${n}, minmax(0, 1fr))`);

  const blockedSave = Boolean(capabilityViolation?.blocking) && !capabilitySoftened;
  const anyDirty = toolsDirty || policy.dirty;
  const saveDisabled = toolsSaving || policy.saving || !anyDirty || (toolsDirty && blockedSave);
  const handleSaveAll = async () => {
    if (toolsDirty) await handleSaveTools();
    if (policy.dirty) await policy.save();
  };

  // The policy select inside a tool card: compact, its effective mode in the
  // tooltip.
  const cardSelectCls = 'border border-gray-200 rounded px-1 py-0 h-6 text-xs leading-none bg-white text-gray-700 focus:outline-none w-auto min-w-0 max-w-full';
  const delegationActive = DELEGATION_TOOLS.some((tid) => selectedTools.includes(tid));
  return (
        <div className="space-y-6">
          {agent.system && <SystemAgentWarning scope="tools" />}

          {/* What the model is shown and when tool search takes over. An
              imported (remote) agent runs its own loop elsewhere. */}
          {agent.type !== 'remote' && (
            <ToolVisibilityCard
              agentId={id}
              workspace={selectedWorkspace || agent?.owner_workspace || undefined}
              refreshKey={`${(agent.tools || []).join(',')}|${(autoTools || []).map((x) => x.id).join(',')}`}
              t={t}
            />
          )}

          {/* ── Regular Tools ── a narrower margin than other cards, so the
              groups get the room. */}
          <div className="bg-white p-4 shadow-md rounded-lg">
            {/* The title row with Save stays at the top of the page while the
                tool list below it scrolls. */}
            <div className="sticky top-0 z-20 -mx-4 px-4 py-3 -mt-3 mb-3 bg-white border-b border-gray-400 dark:border-[color:var(--neutral-border-500)] flex flex-wrap items-center justify-between gap-3" data-testid="tools-header">
              <h3 className="text-lg font-bold flex items-center">
                <Wrench className="w-5 h-5 mr-2 text-indigo-600" />
                {t('agentDetails.tools')}
                <span className="ml-2 px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 text-xs font-semibold">
                  {regularToolIds.filter(t => selectedTools.includes(t)).length}/{regularToolIds.length}
                </span>
                <InfoHint text={t('agentDetails.toolsSaveHint')} label={t('agentDetails.toolsAbout')} className="ml-1.5 font-normal" />
              </h3>
              <label className="relative flex-1 min-w-[12rem] max-w-sm">
                <Search className="w-4 h-4 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
                <input
                  type="text"
                  value={toolQuery}
                  onChange={(e) => setToolQuery(e.target.value)}
                  placeholder={t('agentDetails.toolsSearchPlaceholder')}
                  aria-label={t('agentDetails.toolsSearchPlaceholder')}
                  className="w-full border border-gray-300 rounded-lg pl-8 pr-8 py-1.5 text-sm bg-white text-gray-900 focus:outline-none focus:border-indigo-400"
                />
                {toolQuery && (
                  <button type="button" onClick={() => setToolQuery('')} aria-label={t('common.clear', { defaultValue: 'Clear' })}
                          className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-700">
                    <X className="w-4 h-4" />
                  </button>
                )}
              </label>
              <div className="flex flex-wrap items-center gap-3 min-w-0">
                {/* The policy every tool without a mode of its own gets. */}
                <label className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-gray-700 min-w-0" title={t('toolPolicy.defaultHint')}>
                  <ShieldCheck className="w-4 h-4 text-indigo-600 shrink-0" />
                  <span className="font-medium">{t('toolPolicy.defaultMode')}</span>
                  <select
                    value={policy.draft['*'] || TOOL_POLICY_INHERIT}
                    disabled={policy.loading}
                    onChange={(e) => policy.setMode('*', e.target.value)}
                    aria-label={t('toolPolicy.defaultMode')}
                    className="border border-gray-200 rounded-lg px-2 py-1 text-xs bg-white focus:outline-none max-w-full"
                  >
                    <option value={TOOL_POLICY_INHERIT}>{t('toolPolicy.modes.inherit')}</option>
                    {policy.modes.map((m) => <option key={m} value={m}>{modeLabel(m)}</option>)}
                  </select>
                  {policy.data?.default && (
                    <span className="text-[11px] text-gray-500 basis-full sm:basis-auto">
                      {t('toolPolicy.effectiveValue', { mode: modeLabel(policy.data.default.mode), source: sourceLabel(policy.data.default.source) })}
                    </span>
                  )}
                </label>
                <button
                  type="button"
                  onClick={handleSaveAll}
                  disabled={saveDisabled}
                  className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-50"
                >
                  {(toolsSaving || policy.saving) ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}
                  {t('agentDetails.saveTools')}
                </button>
              </div>
            </div>
            {/* Capability guard escape hatch: with the switch on, a blocked
                combination on this agent (own tools or reached through a
                delegate) is saved and shown as a warning instead of refused.
                A system workspace agent never gets the switch. */}
            {!agent?.system && (
              <label className={`mb-4 flex items-start gap-3 rounded-lg border p-3 cursor-pointer ${capabilityOverride ? 'border-amber-300 bg-amber-50' : 'border-gray-200 bg-gray-50'}`}>
                <input
                  type="checkbox"
                  className="mt-0.5 accent-amber-600"
                  checked={capabilityOverride}
                  disabled={capabilityOverrideSaving}
                  onChange={(e) => handleToggleCapabilityOverride(e.target.checked)}
                />
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-gray-900">{t('agentDetails.capabilityOverrideToggle')}</span>
                  <span className="block text-xs text-gray-600 mt-0.5">{t('agentDetails.capabilityOverrideHint')}</span>
                  {(capabilityGuardInfo?.guard_mode || 'block') !== 'block' && (
                    <span className="block text-xs text-amber-700 mt-1">
                      {(capabilityGuardInfo?.guard_mode || 'block') === 'warn' ? t('agentDetails.capabilityGuardWarnGlobal') : t('agentDetails.capabilityGuardOffGlobal')}
                      {' '}
                      <Link to="/settings" className="underline">{t('agentDetails.capabilityGuardSettingsLink')}</Link>
                    </span>
                  )}
                  {capabilityOverride && capabilityGuardInfo?.honoured_at_build !== false && capabilityGuardInfo?.honoured_in_chat === false && (
                    <span className="block text-xs text-amber-700 mt-1">
                      {t('agentDetails.capabilityOverrideNotInChat')}
                    </span>
                  )}
                  {capabilityOverride && (capabilityGuardInfo?.guard_mode || 'block') === 'block' && capabilityGuardInfo?.honoured_at_build === false && (
                    <span className="block text-xs text-red-700 mt-1">
                      {t('agentDetails.capabilityOverrideNotHonoured')}
                      {' '}
                      <Link to="/settings" className="underline">{t('agentDetails.capabilityGuardSettingsLink')}</Link>
                    </span>
                  )}
                </span>
              </label>
            )}
            <CapabilityBanner
              violation={capabilityViolation || toolsServerWarning}
              softened={capabilitySoftened}
              overrideActive={capabilityOverridden}
              t={t}
              footer={(() => {
                const v = capabilityViolation || toolsServerWarning;
                if (!v) return null;
                if (!v.blocking) return t('agentDetails.capabilityAllowed');
                if (capabilityOverridden) return t('agentDetails.capabilityOverrideAccepted');
                if ((capabilityGuardInfo?.guard_mode || 'block') !== 'block') return t('agentDetails.capabilityGuardWarnGlobal');
                return t('agentDetails.capabilityBlocked');
              })()}
            />
            {toolsMessage && (
              <div className={`text-xs mb-3 ${toolsMessage === t('agentDetails.toolsUpdated') ? 'text-green-600' : 'text-red-600'}`}>
                {toolsMessage}
              </div>
            )}
            {policy.error && (
              <div className="text-xs mb-3 text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{policy.error}</div>
            )}
            {policy.saved && !policy.error && (
              <div className="text-xs mb-3 text-green-600">{t('toolPolicy.saved')}</div>
            )}
            {queryWords.length > 0 && shownCategories.length === 0 && shownAutoTools.length === 0 && (
              <p className="text-sm text-gray-600 mb-3">{t('agentDetails.toolsSearchEmpty', { q: toolQuery.trim() })}</p>
            )}
            {toolCategories.length ? (
              <div ref={groupsRef} className="flex flex-wrap" style={{ columnGap: GROUP_GAP_PX, rowGap: GROUP_ROW_GAP_PX }}>
                {packedCategories.map(({ category, ids, shown, span, width, full }) => {
                  const enabledCount = ids.filter(t => selectedTools.includes(t)).length;
                  // The switch acts on the cards in view: with a search on,
                  // only the matching ones.
                  const shownOn = shown.filter(t => selectedTools.includes(t)).length;
                  const allOn = shownOn === shown.length;
                  const noneOn = shownOn === 0;
                  return (
                    <div key={category} className="border border-gray-200 bg-gray-50 rounded-lg overflow-hidden min-w-0"
                         style={full ? { width: '100%' } : width ? { width } : { flex: `${span} 1 0%` }}
                         data-testid={`tool-category-${category}`}>
                      {/* Category header with master switch */}
                      {/* A long name wraps instead of being cut: a one-card
                          group is only one card wide. */}
                      <div className="flex items-center justify-between gap-3 px-3 py-2.5 bg-gray-50 border-b border-gray-200">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="text-sm font-semibold leading-snug text-gray-900 break-words min-w-0">{toolCategoryLabel(t, category)}</span>
                          <span className={`shrink-0 text-[11px] px-1.5 py-0.5 rounded-full ${enabledCount ? 'bg-green-100 text-green-700' : 'bg-gray-200 text-gray-500'}`}>
                            {enabledCount}/{ids.length}
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() => setCategoryTools(shown, !allOn)}
                          role="switch"
                          aria-checked={allOn}
                          aria-label={`${toolCategoryLabel(t, category)}: ${allOn ? t('agentDetails.disableAllInCategory') : t('agentDetails.enableAllInCategory')}`}
                          title={allOn ? t('agentDetails.disableAllInCategory') : t('agentDetails.enableAllInCategory')}
                          className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors ${
                            allOn ? 'bg-indigo-600' : noneOn ? 'bg-gray-300' : 'bg-indigo-300'
                          }`}
                        >
                          <span
                            className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                              allOn ? 'translate-x-5' : 'translate-x-1'
                            }`}
                          />
                        </button>
                      </div>
                      {/* Tool cards: the whole card toggles the tool, the
                          policy select inside it does not. */}
                      {(
                        <div className="grid p-3" style={{ gridTemplateColumns: cardColumns(span), gap: CARD_GAP_PX, justifyContent: 'center' }}>
                          {shown.map((tool) => {
                            const enabled = selectedTools.includes(tool);
                            const meta = toolsMeta[tool] || {};
                            const row = policy.effective[tool];
                            const locked = row?.source === 'never_gated';
                            const mode = policy.draft[tool] || TOOL_POLICY_INHERIT;
                            const blocked = isolationBlocked(tool);
                            const label = toolLabel(t, tool, meta.label);
                            const description = toolDescription(t, tool, meta.description);
                            return (
                              <div
                                key={tool}
                                role="checkbox"
                                aria-checked={enabled}
                                aria-disabled={blocked || undefined}
                                aria-label={label}
                                tabIndex={blocked ? -1 : 0}
                                title={blocked ? t('isolation.toolHint') : undefined}
                                onClick={() => { if (!blocked) toggleTool(tool); }}
                                onKeyDown={(e) => {
                                  if (blocked) return;
                                  if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); toggleTool(tool); }
                                }}
                                style={{ height: CARD_HEIGHT }}
                                className={`group rounded-lg border p-2.5 select-none transition-colors flex flex-col gap-1.5 min-w-0 overflow-hidden ${
                                  blocked
                                    ? 'cursor-not-allowed opacity-60 border-gray-200 bg-gray-50'
                                    : enabled
                                      ? 'cursor-pointer border-green-300 bg-green-50 hover:bg-green-100'
                                      : 'cursor-pointer border-gray-300 bg-white hover:border-indigo-300 hover:bg-indigo-50/40'
                                }`}
                              >
                                <div className="flex items-start justify-between gap-2">
                                  <div className="min-w-0 flex-1">
                                    <div className={`text-sm font-semibold leading-snug break-words line-clamp-2 ${enabled ? 'text-green-900' : 'text-gray-900'}`} title={tool}>{label}</div>
                                    <div className={`text-xs leading-4 mt-0.5 break-words line-clamp-3 ${enabled ? 'text-green-800' : 'text-gray-600'}`} title={description || tool}>{description || tool}</div>
                                    {policy.groupOf[tool] && (
                                      <div className="text-[11px] text-gray-400 mt-0.5">{t('toolPolicy.fromGroup', { group: policy.groupOf[tool] })}</div>
                                    )}
                                    {blocked && (
                                      <div className="text-[11px] text-amber-700 mt-0.5 flex items-center gap-1" data-testid={`isolation-blocked-${tool}`}>
                                        <Lock className="w-3 h-3 shrink-0" /> {t('isolation.toolHint')}
                                      </div>
                                    )}
                                  </div>
                                  <span className={`mt-0.5 h-5 w-5 shrink-0 rounded-full border flex items-center justify-center text-xs font-bold ${
                                    enabled ? 'bg-green-500 border-green-500 text-white' : 'border-gray-300 text-transparent group-hover:border-indigo-300'
                                  }`}>✓</span>
                                </div>
                                <div className="flex items-center gap-1 mt-auto min-w-0" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                                  <ShieldCheck className={`w-3.5 h-3.5 shrink-0 ${mode ? 'text-indigo-600' : 'text-gray-400'}`} />
                                  <select
                                    value={mode}
                                    disabled={locked || policy.loading}
                                    onChange={(e) => policy.setMode(tool, e.target.value)}
                                    aria-label={t('toolPolicy.modeFor', { tool: label })}
                                    title={row ? t('toolPolicy.effectiveValue', { mode: modeLabel(row.mode), source: sourceLabel(row.source) }) : t('toolPolicy.defaultHint')}
                                    className={cardSelectCls}
                                  >
                                    <option value={TOOL_POLICY_INHERIT}>{t('toolPolicy.modes.inherit')}</option>
                                    {policy.modes.map((m) => <option key={m} value={m}>{modeLabel(m)}</option>)}
                                  </select>
                                </div>
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-sm text-gray-500 italic">{t('agentDetails.noToolsConfiguredForThis')}</p>
            )}

            {/* Tools the factory adds on top of the record: the handoff tool
                once targets exist, think and the plan store, skills tools,
                ask_user behind the clarify gate, the memory pool tools. Not
                toggleable here; the setting named on the card is. The tool
                policy still applies to them by id, so the select stays. */}
            {shownAutoTools.length > 0 && (
              <div className="border border-dashed border-gray-300 bg-gray-50 rounded-lg overflow-hidden" style={{ marginTop: GROUP_ROW_GAP_PX }} data-testid="tool-category-auto">
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-4 py-2.5 bg-gray-50 border-b border-gray-200">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-sm font-semibold text-gray-900 truncate">{t('agentDetails.autoTools')}</span>
                    {/* Counted: what every chat holds. One only some runs get
                        (a project chat's graph reader) is shown apart. */}
                    <span className="text-[11px] px-1.5 py-0.5 rounded-full bg-gray-200 text-gray-600">
                      {autoTools.filter((x) => x.always !== false).length}
                    </span>
                    {autoTools.some((x) => x.always === false) && (
                      <span className="text-[11px] text-gray-500">
                        {t('agentDetails.autoToolsConditional', { n: autoTools.filter((x) => x.always === false).length })}
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-gray-600 min-w-0">{t('agentDetails.autoToolsHint')}</span>
                </div>
                <div className="grid p-3" style={{ gridTemplateColumns: cardColumns(groupCols), gap: CARD_GAP_PX, justifyContent: 'center' }}>
                  {shownAutoTools.map((tool) => {
                    const row = policy.effective[tool.id];
                    const locked = row?.source === 'never_gated';
                    const mode = policy.draft[tool.id] || TOOL_POLICY_INHERIT;
                    const label = toolLabel(t, tool.id, tool.label);
                    const description = toolDescription(t, tool.id, tool.description);
                    return (
                      <div
                        key={tool.id}
                        aria-label={label}
                        style={{ height: CARD_HEIGHT }}
                        className="rounded-lg border border-gray-300 bg-white p-2.5 flex flex-col gap-1.5 min-w-0 overflow-hidden"
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0 flex-1">
                            <div className="text-sm font-semibold leading-snug text-gray-900 break-words line-clamp-2" title={tool.id}>{label}</div>
                            <div className="text-xs leading-4 text-gray-600 mt-0.5 break-words line-clamp-2" title={description || tool.id}>{description || tool.id}</div>
                            <div className="text-[11px] leading-4 text-indigo-700 mt-0.5 break-words line-clamp-1">{t(`agentDetails.autoToolReasons.${tool.reason}`)}</div>
                          </div>
                          <span className="mt-0.5 shrink-0 text-[10px] font-semibold uppercase tracking-wide text-gray-400">{t('agentDetails.autoToolBadge')}</span>
                        </div>
                        <div className="flex items-center gap-1 mt-auto min-w-0">
                          <ShieldCheck className={`w-3.5 h-3.5 shrink-0 ${mode ? 'text-indigo-600' : 'text-gray-400'}`} />
                          <select
                            value={mode}
                            disabled={locked || policy.loading}
                            onChange={(e) => policy.setMode(tool.id, e.target.value)}
                            aria-label={t('toolPolicy.modeFor', { tool: label })}
                            title={row ? t('toolPolicy.effectiveValue', { mode: modeLabel(row.mode), source: sourceLabel(row.source) }) : t('toolPolicy.defaultHint')}
                            className={cardSelectCls}
                          >
                            <option value={TOOL_POLICY_INHERIT}>{t('toolPolicy.modes.inherit')}</option>
                            {policy.modes.map((m) => <option key={m} value={m}>{modeLabel(m)}</option>)}
                          </select>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {policy.dirty && <p className="text-xs text-gray-500 mt-3">{t('toolPolicy.effectiveAfterSave')}</p>}

            {/* What the policy decided lately, and who makes the auto calls,
                below a divider across the whole card. */}
            <div className="-mx-4 px-4 mt-6 border-t border-gray-400 dark:border-[color:var(--neutral-border-500)] pt-4">
              <button
                type="button"
                onClick={() => setDecisionsOpen((v) => !v)}
                aria-expanded={decisionsOpen}
                className="flex items-center gap-2 text-sm font-semibold text-gray-800"
              >
                {decisionsOpen ? <ChevronDown className="w-4 h-4 text-gray-400" /> : <ChevronRight className="w-4 h-4 text-gray-400" />}
                {t('toolPolicy.recent')}
                <span className="text-[11px] font-normal text-gray-400">{policy.decisions.length}</span>
              </button>
              {decisionsOpen && (
                <div className="mt-2">
                  <p className="text-xs text-gray-500 mb-2">
                    {policy.data?.classifier_model
                      ? t('toolPolicy.classifier', { model: policy.data.classifier_model })
                      : t('toolPolicy.classifierDefault')}
                  </p>
                  {policy.decisions.length === 0 ? (
                    <p className="text-sm text-gray-500 italic">{t('toolPolicy.noDecisions')}</p>
                  ) : (
                    <ul className="space-y-1.5" data-testid="tool-policy-decisions">
                      {policy.decisions.map((d, i) => (
                        <li key={d.id || `${d.fingerprint}-${i}`} className="text-xs text-gray-700 flex flex-wrap items-center gap-2">
                          <span className={`px-2 py-0.5 rounded-full border font-semibold ${DECISION_CLS[d.decision] || 'bg-gray-50 text-gray-700 border-gray-200'}`}>
                            {t(`toolPolicy.decision.${d.decision}`)}
                          </span>
                          <span className="font-mono">{d.tool}</span>
                          <span className="text-gray-500">{t(`toolPolicy.by.${d.by}`)}</span>
                          {d.reason && <span className="text-gray-600">{d.reason}</span>}
                          {d.at && <span className="text-gray-400">{new Date(d.at).toLocaleString()}</span>}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          </div>

          {(
            <div className={`bg-white p-6 shadow-md rounded-lg ${delegationActive ? '' : 'opacity-60'}`} data-testid="delegation-card" aria-disabled={!delegationActive}>
              <div className="flex items-center justify-between gap-3 mb-2">
                <div className="flex items-center gap-2">
                  <Share2 className={`w-5 h-5 ${delegationActive ? 'text-indigo-600' : 'text-gray-400'}`} />
                  <h3 className="text-lg font-bold text-gray-900">{t('agentDetails.delegation')}</h3>
                </div>
                <button
                  type="button"
                  onClick={handleSaveDelegates}
                  disabled={!delegationActive || delegatesSaving || !delegatesDirty.current}
                  className={`px-4 py-2 rounded-lg text-sm font-semibold ${
                    !delegationActive || delegatesSaving || !delegatesDirty.current
                      ? 'bg-gray-100 text-gray-400 cursor-not-allowed'
                      : 'bg-indigo-600 text-white hover:bg-indigo-700'
                  }`}
                >
                  {delegatesSaving ? t('common.saving') : t('agentDetails.saveDelegation')}
                </button>
              </div>
              <p className="text-sm text-gray-600 mb-4">
                {t('agentDetails.delegationIntro')} <code className="text-xs bg-gray-100 px-1 py-0.5 rounded">{t('agentDetails.runAgentTool')}</code>.
                {t('agentDetails.delegationSelect')} <span className="font-semibold">{t('agentDetails.allUnselected')}</span> {t('agentDetails.delegationNoRestriction')}
              </p>
              {!delegationActive && (
                <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-4" data-testid="delegation-inactive">
                  {t('agentDetails.delegationInactive')}
                </p>
              )}

              <div className="flex items-center justify-between mb-2">
                <span className={`text-xs font-semibold px-2 py-1 rounded-full ${
                  delegates.length ? 'bg-amber-100 text-amber-700' : 'bg-green-100 text-green-700'
                }`}>
                  {delegates.length
                    ? t('agentDetails.restrictedToCount', { count: delegates.length })
                    : t('agentDetails.noRestriction')}
                </span>
                {delegates.length > 0 && (
                  <button
                    type="button"
                    disabled={!delegationActive}
                    onClick={() => { setDelegates([]); markDelegatesDirty(); setDelegatesMessage(''); }}
                    className="text-xs font-semibold text-gray-500 hover:text-gray-700 disabled:cursor-not-allowed"
                  >
                    {t('agentDetails.clearRestriction')}
                  </button>
                )}
              </div>

              {roleRows.length > 0 && (
                <div className="mb-4" data-testid="delegation-roles">
                  <p className="text-xs font-semibold text-gray-500 mb-1">{t('agentDetails.delegationRoles')}</p>
                  <p className="text-xs text-gray-500 mb-2">{t('agentDetails.delegationRolesHint')}</p>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    {roleRows.map((r) => {
                      const enabled = delegates.includes(r.ref);
                      return (
                        <div key={r.ref} className="p-3 border border-indigo-100 rounded-lg bg-indigo-50/40 flex items-center justify-between gap-3">
                          <div className="min-w-0">
                            <div className="text-sm font-semibold text-gray-800 truncate">
                              {t(`workspaceDetails.roles.names.${r.role}`)} <code className="text-[11px] text-gray-500">{r.ref}</code>
                            </div>
                            <div className="text-xs text-gray-500 mt-1 truncate">
                              {t('agentDetails.delegationRoleHolder', { agent: r.agent_name || r.agent || r.default })}
                            </div>
                          </div>
                          <button
                            type="button"
                            disabled={!delegationActive}
                            onClick={() => toggleDelegate(r.ref)}
                            className={`px-2.5 py-1 rounded-full text-xs font-semibold border shrink-0 disabled:cursor-not-allowed ${
                              enabled ? 'bg-green-100 text-green-700 border-green-200' : 'bg-gray-100 text-gray-500 border-gray-200'
                            }`}
                          >
                            {enabled ? 'Allowed' : 'Off'}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {allAgents.length > 0 ? (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {allAgents.map((a) => {
                    const aid = a.id || a;
                    const enabled = delegates.includes(aid);
                    return (
                      <div key={aid} className="p-3 border border-gray-100 rounded-lg bg-white flex items-center justify-between gap-3">
                        <div className="min-w-0">
                          <div className="text-sm font-semibold text-gray-800 truncate">{a.name || aid}</div>
                          <div className="text-xs text-gray-500 mt-1 truncate">{a.description || aid}</div>
                        </div>
                        <button
                          type="button"
                          disabled={!delegationActive}
                          onClick={() => toggleDelegate(aid)}
                          className={`px-2.5 py-1 rounded-full text-xs font-semibold border shrink-0 disabled:cursor-not-allowed ${
                            enabled ? 'bg-green-100 text-green-700 border-green-200' : 'bg-gray-100 text-gray-500 border-gray-200'
                          }`}
                        >
                          {enabled ? 'Allowed' : 'Off'}
                        </button>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="text-sm text-gray-500 italic">{t('agentDetails.noOtherAgentsAvailableIn')}</p>
              )}

              {/* Which delegate closed the combination, and through which hop.
                  Only the server can tell: the delegates' tool lists are not
                  in this page. Red when the save was refused, amber when it
                  went through on the override or in warn mode. */}
              <div className="mt-4">
                <CapabilityBanner
                  violation={delegatesViolation}
                  softened={capabilitySoftened}
                  overrideActive={capabilityOverridden}
                  t={t}
                  footer={delegatesViolation
                    ? (!delegatesViolation.blocking
                      ? t('agentDetails.capabilityAllowed')
                      : capabilityOverridden
                        ? t('agentDetails.capabilityOverrideAccepted')
                        : (capabilityGuardInfo?.guard_mode || 'block') !== 'block'
                          ? t('agentDetails.capabilityGuardWarnGlobal')
                          : t('agentDetails.delegationBlockedHint'))
                    : null}
                />
              </div>
              {delegatesMessage && (
                <p className={`text-xs mt-3 ${delegatesMessage === t('agentDetails.delegationRefused') || delegatesMessage === t('agentDetails.errors.updateDelegation') ? 'text-red-600' : 'text-gray-600'}`}>{delegatesMessage}</p>
              )}
            </div>
          )}

        </div>
  );
}
