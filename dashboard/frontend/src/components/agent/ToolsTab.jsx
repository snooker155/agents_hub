import { updateAgentClarifyGate, updateAgentReasoning, updateAgentResponseFormat, updateAgentSelfDelegation } from '../../api';
import { CAPABILITY_LABELS } from '../../lib/capabilities';
import SystemAgentWarning from './SystemAgentWarning';
import { AlertTriangle, BrainCircuit, HelpCircle, Layers, Loader, MessageSquare, Repeat, Save, Share2, Wrench } from 'lucide-react';
import { useAgentPage } from './context';

/** Tools and capabilities, and who this agent may delegate to. */
export default function ToolsTab() {
  const {
    PLAN_FORMATS, THINKING_LEVELS, THINK_MODES, agent, allAgents, capabilityOverridden,
    capabilityViolation, clarifyGate, clarifyGateSaving, delegates, delegatesDirty,
    delegatesMessage, delegatesSaving, formatCategory, markDelegatesDirty,
    handleSaveDelegates, handleSaveTools, id, reasoningSettings, regularToolIds,
    responseFormat, responseFormatSaving, selectedTools, selfDelegation,
    selfDelegationSaving, setCategoryTools, setClarifyGate, setClarifyGateSaving,
    setDelegates, setDelegatesMessage, setReasoningSettings, setResponseFormat,
    setResponseFormatSaving, setSelfDelegation, setSelfDelegationSaving, t, toggleDelegate,
    toggleTool, toolCategories, toolsDirty, toolsMessage, toolsMeta, toolsSaving,
  } = useAgentPage();
  return (
        <div className="space-y-6">
          {agent.system && <SystemAgentWarning scope="tools" />}

          {/* ── Agent Behavior ── */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold flex items-center mb-1">
              <BrainCircuit className="w-5 h-5 mr-2 text-violet-600" />
              {t('agentDetails.agentBehavior')}
            </h3>
            <p className="text-xs text-gray-500 mb-4">
              {t('agentDetails.configureHowThisAgentThinks')}
            </p>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
              {/* Think card */}
              {(() => {
                const thinkToolOn = reasoningSettings.thinkEnabled;
                const nativeOn = (reasoningSettings.thinkingLevel || 'off') !== 'off';
                // Enable/disable applies to the whole Think block (native + tool).
                const blockEnabled = nativeOn || thinkToolOn;

                // Persist a partial reasoning change (state + server) in one place.
                const patchReasoning = (patch) => {
                  setReasoningSettings({ ...reasoningSettings, ...patch });
                  const payload = {};
                  if ('thinkingLevel' in patch) payload.thinking_level = patch.thinkingLevel;
                  if ('thinkEnabled' in patch) payload.think_enabled = patch.thinkEnabled;
                  if ('thinkMode' in patch) payload.think_mode = patch.thinkMode;
                  updateAgentReasoning(id, payload).catch(() => {});
                };

                // Tool segmented switcher: an "Off" segment plus the depth modes.
                const toolSegments = [{ value: 'off', label: t('agentDetails.off'), desc: t('agentDetails.noScratchpadTool') }, ...THINK_MODES];

                const segBtn = (selected, onClick, label, desc) => (
                  <button
                    key={label}
                    type="button"
                    title={desc}
                    onClick={onClick}
                    className={`flex-1 px-2 py-1.5 text-xs font-semibold border-l first:border-l-0 border-gray-200 transition-colors ${
                      selected ? 'bg-violet-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    {label}
                  </button>
                );

                return (
                  <div className={`rounded-xl border-2 p-4 transition-colors ${blockEnabled ? 'border-violet-300 bg-violet-50' : 'border-gray-200 bg-gray-50'}`}>
                    <div className="flex items-start justify-between gap-3 mb-3">
                      <div className="flex items-center gap-2">
                        <div className={`p-2 rounded-lg ${blockEnabled ? 'bg-violet-100' : 'bg-gray-200'}`}>
                          <BrainCircuit className={`w-4 h-4 ${blockEnabled ? 'text-violet-600' : 'text-gray-400'}`} />
                        </div>
                        <div>
                          <div className="font-semibold text-gray-900 text-sm">{t('agentDetails.think')}</div>
                          <div className="text-xs text-gray-500">{t('agentDetails.thinkBlockHint')}</div>
                        </div>
                      </div>
                      {/* Enable/disable the whole block: turns both native
                          thinking and the scratchpad tool off. */}
                      <button
                        type="button"
                        onClick={() => blockEnabled
                          ? patchReasoning({ thinkingLevel: 'off', thinkEnabled: false })
                          : patchReasoning({ thinkingLevel: 'medium' })}
                        className={`px-3 py-1 rounded-full text-xs font-semibold border shrink-0 ${
                          blockEnabled ? 'bg-violet-600 text-white border-violet-600' : 'bg-white text-gray-500 border-gray-300'
                        }`}
                      >
                        {blockEnabled ? 'Enabled' : 'Disabled'}
                      </button>
                    </div>
                    <p className="text-xs text-gray-600 mb-3">
                      <span className="font-medium">{t('agentDetails.nativeThinking')}</span> is a model parameter — how much the model reasons on its
                      own. The <span className="font-medium">{t('agentDetails.thinkTool')}</span> is a separate scratchpad the agent can call; pick its
                      depth or turn it off.
                    </p>

                    {blockEnabled && (
                      <div className="grid grid-cols-2 gap-3">
                        {/* Native thinking column */}
                        <div>
                          <label className="block text-xs font-medium text-gray-700 mb-1">{t('agentDetails.nativeThinking')}</label>
                          <div className="flex rounded-lg border border-gray-300 overflow-hidden bg-white">
                            {THINKING_LEVELS.filter(m => m.value !== 'off').map(m =>
                              segBtn(
                                reasoningSettings.thinkingLevel === m.value,
                                () => patchReasoning({ thinkingLevel: m.value }),
                                m.label,
                                m.desc,
                              )
                            )}
                          </div>
                        </div>
                        {/* Think tool column */}
                        <div>
                          <label className="block text-xs font-medium text-gray-700 mb-1">{t('agentDetails.thinkTool2')}</label>
                          <div className="flex rounded-lg border border-gray-300 overflow-hidden bg-white">
                            {toolSegments.map(m =>
                              m.value === 'off'
                                ? segBtn(!thinkToolOn, () => patchReasoning({ thinkEnabled: false }), m.label, m.desc)
                                : segBtn(
                                    thinkToolOn && reasoningSettings.thinkMode === m.value,
                                    () => patchReasoning({ thinkEnabled: true, thinkMode: m.value }),
                                    m.label,
                                    m.desc,
                                  )
                            )}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* Plan card */}
              {(() => {
                const enabled = reasoningSettings.planEnabled;
                return (
                  <div className={`rounded-xl border-2 p-4 transition-colors ${enabled ? 'border-indigo-300 bg-indigo-50' : 'border-gray-200 bg-gray-50'}`}>
                    <div className="flex items-start justify-between gap-3 mb-3">
                      <div className="flex items-center gap-2">
                        <div className={`p-2 rounded-lg ${enabled ? 'bg-indigo-100' : 'bg-gray-200'}`}>
                          <Layers className={`w-4 h-4 ${enabled ? 'text-indigo-600' : 'text-gray-400'}`} />
                        </div>
                        <div>
                          <div className="font-semibold text-gray-900 text-sm">{t('agentDetails.plan')}</div>
                          <div className="text-xs text-gray-500">{t('agentDetails.upfrontStructuredPlanning')}</div>
                        </div>
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          const next = { ...reasoningSettings, planEnabled: !enabled };
                          setReasoningSettings(next);
                          // The plan / save_plan / get_plan / list_plans / update_plan_status /
                          // delete_plan tools are auto-injected by the backend whenever this
                          // capability is on, so toggling the flag is all that's needed — they
                          // are not stored in the agent's regular tools list.
                          updateAgentReasoning(id, { plan_enabled: next.planEnabled }).catch(() => {});
                        }}
                        className={`px-3 py-1 rounded-full text-xs font-semibold border shrink-0 ${
                          enabled ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white text-gray-500 border-gray-300'
                        }`}
                      >
                        {enabled ? 'Enabled' : 'Disabled'}
                      </button>
                    </div>
                    <p className="text-xs text-gray-600 mb-3">
                      {t('agentDetails.letsTheAgentProduceA')}
                    </p>
                    {enabled && (
                      <div>
                        <label className="block text-xs font-medium text-gray-700 mb-1">{t('agentDetails.planningFormat')}</label>
                        <select
                          value={reasoningSettings.planFormat}
                          onChange={e => {
                            const next = { ...reasoningSettings, planFormat: e.target.value };
                            setReasoningSettings(next);
                            updateAgentReasoning(id, { plan_format: e.target.value }).catch(() => {});
                          }}
                          className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
                        >
                          {PLAN_FORMATS.map(f => (
                            <option key={f.value} value={f.value}>{f.label} — {f.desc}</option>
                          ))}
                        </select>
                      </div>
                    )}
                  </div>
                );
              })()}

              {/* Clarify card */}
              {(() => {
                const enabled = clarifyGate;
                return (
                  <div className={`rounded-xl border-2 p-4 transition-colors ${enabled ? 'border-amber-300 bg-amber-50' : 'border-gray-200 bg-gray-50'}`}>
                    <div className="flex items-start justify-between gap-3 mb-3">
                      <div className="flex items-center gap-2">
                        <div className={`p-2 rounded-lg ${enabled ? 'bg-amber-100' : 'bg-gray-200'}`}>
                          <HelpCircle className={`w-4 h-4 ${enabled ? 'text-amber-600' : 'text-gray-400'}`} />
                        </div>
                        <div>
                          <div className="font-semibold text-gray-900 text-sm">{t('agentDetails.clarify')}</div>
                          <div className="text-xs text-gray-500">{t('agentDetails.askBeforeActingOnGaps')}</div>
                        </div>
                      </div>
                      <button
                        type="button"
                        disabled={clarifyGateSaving}
                        onClick={() => {
                          const next = !enabled;
                          const prev = enabled;
                          setClarifyGate(next);
                          setClarifyGateSaving(true);
                          updateAgentClarifyGate(id, next)
                            .then((r) => setClarifyGate(!!r.data?.clarify_gate))
                            .catch(() => setClarifyGate(prev))
                            .finally(() => setClarifyGateSaving(false));
                        }}
                        className={`px-3 py-1 rounded-full text-xs font-semibold border shrink-0 disabled:opacity-50 ${
                          enabled ? 'bg-amber-500 text-white border-amber-500' : 'bg-white text-gray-500 border-gray-300'
                        }`}
                      >
                        {enabled ? 'Enabled' : 'Disabled'}
                      </button>
                    </div>
                    <p className="text-xs text-gray-600">
                      When the agent lacks key information, it asks a few clarifying questions and waits instead of guessing. In a task it pauses (awaiting input) until you answer; in chat it asks and continues once you reply.
                    </p>
                  </div>
                );
              })()}

              {/* Self-delegation card */}
              {(() => {
                const enabled = selfDelegation;
                return (
                  <div className={`rounded-xl border-2 p-4 transition-colors ${enabled ? 'border-indigo-300 bg-indigo-50' : 'border-gray-200 bg-gray-50'}`}>
                    <div className="flex items-start justify-between gap-3 mb-3">
                      <div className="flex items-center gap-2">
                        <div className={`p-2 rounded-lg ${enabled ? 'bg-indigo-100' : 'bg-gray-200'}`}>
                          <Repeat className={`w-4 h-4 ${enabled ? 'text-indigo-600' : 'text-gray-400'}`} />
                        </div>
                        <div>
                          <div className="font-semibold text-gray-900 text-sm">{t('agentDetails.selfDelegation')}</div>
                          <div className="text-xs text-gray-500">{t('agentDetails.letThisAgentCallItself')}</div>
                        </div>
                      </div>
                      <button
                        type="button"
                        disabled={selfDelegationSaving}
                        onClick={() => {
                          const next = !enabled;
                          const prev = enabled;
                          setSelfDelegation(next);
                          setSelfDelegationSaving(true);
                          updateAgentSelfDelegation(id, next)
                            .then((r) => setSelfDelegation(!!r.data?.allow_self_delegation))
                            .catch(() => setSelfDelegation(prev))
                            .finally(() => setSelfDelegationSaving(false));
                        }}
                        className={`px-3 py-1 rounded-full text-xs font-semibold border shrink-0 disabled:opacity-50 ${
                          enabled ? 'bg-indigo-500 text-white border-indigo-500' : 'bg-white text-gray-500 border-gray-300'
                        }`}
                      >
                        {enabled ? 'Enabled' : 'Disabled'}
                      </button>
                    </div>
                    <p className="text-xs text-gray-600">
                      Allows the agent to target its own id in <code>{t('agentDetails.runAgentTool')}</code> / <code>{t('agentDetails.assignAgentTool')}</code>. Off by default because a self-run recurses the same agent. Enable only for agents meant to hand a sub-goal back to themselves, and keep an eye on runaway loops.
                    </p>
                  </div>
                );
              })()}
            </div>
          </div>

          {/* ── Response Format ── */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <h3 className="text-lg font-bold flex items-center mb-1">
              <MessageSquare className="w-5 h-5 mr-2 text-teal-600" />
              {t('agentDetails.responseFormat')}
            </h3>
            <p className="text-xs text-gray-500 mb-4">
              Let this agent reply with interactive UI (buttons / a Telegram inline keyboard)
              instead of plain text. When enabled, the agent is taught a structured-reply
              convention; surfaces that understand it (web chat, Telegram) render the buttons,
              others fall back to text.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              {[
                ...['none', 'buttons', 'telegram', 'views'].map((value) => ({
                  value,
                  label: t(`agentDetails.responseFormats.${value}.label`),
                  desc: t(`agentDetails.responseFormats.${value}.desc`),
                })),
              ].map((opt) => {
                const active = responseFormat === opt.value;
                return (
                  <button
                    key={opt.value}
                    type="button"
                    disabled={responseFormatSaving}
                    onClick={() => {
                      if (active) return;
                      const prev = responseFormat;
                      setResponseFormat(opt.value);
                      setResponseFormatSaving(true);
                      updateAgentResponseFormat(id, opt.value)
                        .then((r) => setResponseFormat(r.data?.response_format || opt.value))
                        .catch(() => setResponseFormat(prev))
                        .finally(() => setResponseFormatSaving(false));
                    }}
                    className={`text-left rounded-xl border-2 p-4 transition-colors disabled:opacity-50 ${
                      active ? 'border-teal-300 bg-teal-50' : 'border-gray-200 bg-gray-50 hover:bg-gray-100'
                    }`}
                  >
                    <div className={`font-semibold text-sm ${active ? 'text-teal-700' : 'text-gray-900'}`}>
                      {opt.label}
                    </div>
                    <div className="text-xs text-gray-500 mt-1">{opt.desc}</div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* ── Regular Tools ── */}
          <div className="bg-white p-6 shadow-md rounded-lg">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold flex items-center">
                <Wrench className="w-5 h-5 mr-2 text-indigo-600" />
                Tools
                <span className="ml-2 px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 text-xs font-semibold">
                  {regularToolIds.filter(t => selectedTools.includes(t)).length}/{regularToolIds.length}
                </span>
              </h3>
              <button
                type="button"
                onClick={handleSaveTools}
                disabled={toolsSaving || !toolsDirty || (Boolean(capabilityViolation?.blocking) && !capabilityOverridden)}
                className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded-md hover:bg-indigo-700 disabled:opacity-50"
              >
                {toolsSaving ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}
                Save Tools
              </button>
            </div>
            {capabilityViolation && (
              <div className={`mb-4 rounded-lg border p-3 ${(!capabilityViolation.blocking || capabilityOverridden) ? 'border-amber-200 bg-amber-50' : 'border-red-200 bg-red-50'}`}>
                <div className={`text-sm font-bold flex items-center gap-2 ${(!capabilityViolation.blocking || capabilityOverridden) ? 'text-amber-800' : 'text-red-800'}`}>
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  {capabilityViolation.title}
                  {capabilityOverridden && capabilityViolation.blocking && (
                    <span className="px-1.5 py-0.5 rounded bg-amber-200 text-amber-900 text-[10px] font-semibold uppercase tracking-wide">
                      {t('agentDetails.overrideActive')}
                    </span>
                  )}
                </div>
                <div className={`text-xs mt-1.5 ${(!capabilityViolation.blocking || capabilityOverridden) ? 'text-amber-800' : 'text-red-700'}`}>
                  {capabilityViolation.explanation}
                </div>
                {/* Name the offending capabilities and exactly which tools granted
                    each, so the fix is obvious instead of a guessing game. */}
                <ul className="mt-2 space-y-1">
                  {capabilityViolation.capabilities.map((cap) => (
                    <li key={cap} className={`text-xs ${(!capabilityViolation.blocking || capabilityOverridden) ? 'text-amber-900' : 'text-red-800'}`}>
                      <span className="font-semibold">{CAPABILITY_LABELS[cap]}</span>
                      {' — '}
                      {(capabilityViolation.sources[cap] || []).join(', ') || '?'}
                    </li>
                  ))}
                </ul>
                <div className={`text-xs mt-2 ${(!capabilityViolation.blocking || capabilityOverridden) ? 'text-amber-700' : 'text-red-600'}`}>
                  {!capabilityViolation.blocking
                    ? t('agentDetails.capabilityAllowed')
                    : capabilityOverridden
                      ? t('agentDetails.capabilityOverridden')
                      : t('agentDetails.capabilityBlocked')}
                </div>
              </div>
            )}
            {toolsMessage && (
              <div className={`text-xs mb-3 ${toolsMessage === t('agentDetails.toolsUpdated') ? 'text-green-600' : 'text-red-600'}`}>
                {toolsMessage}
              </div>
            )}
            {toolCategories.length ? (
              <div className="space-y-5">
                {toolCategories.map(({ category, ids }) => {
                  const enabledCount = ids.filter(t => selectedTools.includes(t)).length;
                  const allOn = enabledCount === ids.length;
                  const noneOn = enabledCount === 0;
                  return (
                    <div key={category} className="border border-gray-100 rounded-lg overflow-hidden">
                      {/* Category header with master switch */}
                      <div className="flex items-center justify-between gap-3 px-4 py-2.5 bg-gray-50 border-b border-gray-100">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-gray-800">{formatCategory(category)}</span>
                          <span className="text-[11px] text-gray-400">{enabledCount}/{ids.length} on</span>
                        </div>
                        <button
                          type="button"
                          onClick={() => setCategoryTools(ids, !allOn)}
                          role="switch"
                          aria-checked={allOn}
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
                      {/* Tools in category */}
                      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 p-3">
                        {ids.map((tool) => {
                          const enabled = selectedTools.includes(tool);
                          const meta = toolsMeta[tool] || {};
                          return (
                            <div key={tool} className="p-3 border border-gray-100 rounded-lg bg-white flex items-center justify-between gap-3">
                              <div className="min-w-0">
                                <div className="text-sm font-semibold text-gray-800 truncate">{meta.label || tool}</div>
                                <div className="text-xs text-gray-500 mt-1 truncate">{meta.description || tool}</div>
                              </div>
                              <button
                                type="button"
                                onClick={() => toggleTool(tool)}
                                className={`px-2.5 py-1 rounded-full text-xs font-semibold border shrink-0 ${
                                  enabled ? 'bg-green-100 text-green-700 border-green-200' : 'bg-gray-100 text-gray-500 border-gray-200'
                                }`}
                              >
                                {enabled ? 'On' : 'Off'}
                              </button>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-sm text-gray-500 italic">{t('agentDetails.noToolsConfiguredForThis')}</p>
            )}
            <p className="text-xs text-gray-500 mt-3">
              Toggle tools on/off, then click <span className="font-semibold">{t('agentDetails.saveTools')}</span> to apply changes.
            </p>
          </div>

          {selectedTools.includes('run_agent_tool') && (
            <div className="bg-white p-6 shadow-md rounded-lg">
              <div className="flex items-center justify-between gap-3 mb-2">
                <div className="flex items-center gap-2">
                  <Share2 className="w-5 h-5 text-indigo-600" />
                  <h3 className="text-lg font-bold text-gray-900">{t('agentDetails.delegation')}</h3>
                </div>
                <button
                  type="button"
                  onClick={handleSaveDelegates}
                  disabled={delegatesSaving || !delegatesDirty.current}
                  className={`px-4 py-2 rounded-lg text-sm font-semibold ${
                    delegatesSaving || !delegatesDirty.current
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
                    onClick={() => { setDelegates([]); markDelegatesDirty(); setDelegatesMessage(''); }}
                    className="text-xs font-semibold text-gray-500 hover:text-gray-700"
                  >
                    {t('agentDetails.clearRestriction')}
                  </button>
                )}
              </div>

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
                          onClick={() => toggleDelegate(aid)}
                          className={`px-2.5 py-1 rounded-full text-xs font-semibold border shrink-0 ${
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

              {delegatesMessage && (
                <p className="text-xs text-gray-600 mt-3">{delegatesMessage}</p>
              )}
            </div>
          )}

        </div>
  );
}
