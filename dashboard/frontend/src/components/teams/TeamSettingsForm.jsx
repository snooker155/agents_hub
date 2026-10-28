import React, { useState } from 'react';
import { Crown, DoorOpen, Eye, Loader, Save, Trash2, Wand2, X } from 'lucide-react';
import { suggestTeamManifest } from '../../api';
import AgentSelect from '../flowEditor/AgentSelect';
import { InfoHint } from '../PageLayout';
import { modeHelp, modeOptions } from '../teamModes';
import { useI18n } from '../../i18n';

// Columns that follow the room the form has, not the window: the page has
// side columns and the build chat beside it, so a wide window can still
// leave the form narrow. As many columns of at least --fit-min as fit.
const FIT_GRID = 'grid grid-cols-[repeat(auto-fit,minmax(min(var(--fit-min),100%),1fr))]';

function MemberCard({ member, agents, onChange, onRemove, isLeader, isEntry }) {
  const { t } = useI18n();
  const [suggesting, setSuggesting] = useState(false);
  const suggest = async () => {
    setSuggesting(true);
    try {
      const { data } = await suggestTeamManifest(member.agent_id);
      onChange({ ...member, manifest: data.manifest, role: member.role || data.role });
    } catch { /* the suggestion is a convenience, not a requirement */ }
    setSuggesting(false);
  };
  const agent = agents.find((a) => a.id === member.agent_id);
  return (
    <div className="border border-gray-200 rounded-lg p-3 space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-sm font-semibold text-gray-900">
          {agent?.name || member.agent_id}
        </span>
        {isLeader && (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5">
            <Crown className="w-3 h-3" /> {t('teamDetails.lead2')}
          </span>
        )}
        {isEntry && (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-indigo-700 bg-indigo-50 border border-indigo-200 rounded-full px-2 py-0.5">
            <DoorOpen className="w-3 h-3" /> {t('teamDetails.takesTheRequest')}
          </span>
        )}
        <button onClick={onRemove} className="ml-auto p-1 text-gray-400 hover:text-red-600">
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
      <div className={`${FIT_GRID} [--fit-min:9rem] gap-2 items-end`}>
        <div>
          <label className="block text-[11px] font-semibold text-gray-500 mb-0.5">
            {t('teamDetails.nameTeammatesUse')}
          </label>
          <input
            value={member.name}
            onChange={(e) => onChange({ ...member, name: e.target.value })}
            placeholder={agent?.name || member.agent_id}
            className="w-full text-sm border border-gray-300 rounded-lg px-2 py-1.5"
          />
        </div>
        <div>
          <label className="block text-[11px] font-semibold text-gray-500 mb-0.5">{t('teamDetails.role')}</label>
          <input
            value={member.role}
            onChange={(e) => onChange({ ...member, role: e.target.value })}
            placeholder={t('teamDetails.reviewer')}
            className="w-full text-sm border border-gray-300 rounded-lg px-2 py-1.5"
          />
        </div>
      </div>
      <div>
        <div className="flex items-center justify-between mb-0.5">
          <label className="block text-[11px] font-semibold text-gray-500">
            {t('teamDetails.manifestWhatThisMemberWill')}
          </label>
          <button
            onClick={suggest}
            className="inline-flex items-center gap-1 text-[11px] font-semibold text-indigo-600 hover:text-indigo-800"
          >
            {suggesting ? <Loader className="w-3 h-3 animate-spin" /> : <Wand2 className="w-3 h-3" />}
            {t('teamDetails.suggest')}
          </button>
        </div>
        <textarea
          value={member.manifest} rows={3}
          onChange={(e) => onChange({ ...member, manifest: e.target.value })}
          placeholder={t('teamDetails.reviewsEveryDiffBeforeIt')}
          className="w-full text-sm border border-gray-300 rounded-lg px-2 py-1.5"
        />
        {!member.manifest.trim() && (
          <p className="text-[11px] text-amber-700 mt-1">
            {t('teamDetails.withoutAManifestTheOther')}
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * The team's settings, the page the Settings button switches to: the name,
 * how the team is run, the charter, the roster with each member's manifest,
 * and the limits of a run.
 */
export default function TeamSettingsForm({
  draft, agents, onChange, onSave, saving, briefing, onShowBriefing, onCloseBriefing,
}) {
  const { t } = useI18n();
  const set = onChange;
  const setMember = (idx, member) =>
    set({ members: draft.members.map((m, i) => (i === idx ? member : m)) });
  const entryId = draft.entry_agent_id || draft.members[0]?.agent_id;
  return (
          <div className="mx-auto w-full max-w-6xl bg-white rounded-xl border border-gray-200 p-5 shadow-sm space-y-5">
            {/* One row: the name, how the team is run, and, in autonomous mode,
                who takes the request. */}
            <div className={`${FIT_GRID} [--fit-min:15rem] gap-4 items-end`}>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teamDetails.name')}</label>
                <input
                  value={draft.name} onChange={(e) => set({ name: e.target.value })}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                />
              </div>
              <div>
                <div className="flex items-center gap-1 mb-1 h-4">
                  <label className="text-xs font-semibold text-gray-600">{t('teamDetails.howItIsRun')}</label>
                  <InfoHint text={modeHelp(draft.mode, t)} label={t('teamDetails.howItIsRun')} />
                </div>
                <select
                  value={draft.mode} onChange={(e) => set({ mode: e.target.value })}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                >
                  {modeOptions(t).map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </div>
              {draft.mode === 'autonomous' && (
                <div>
                  <div className="flex items-center gap-1 mb-1 h-4">
                    <label className="text-xs font-semibold text-gray-600">
                      <DoorOpen className="w-3.5 h-3.5 inline mr-1 -mt-0.5" /> {t('teamDetails.whoTakesTheRequest')}
                    </label>
                    <InfoHint text={t('teamDetails.entryHelp')} label={t('teamDetails.whoTakesTheRequest')} />
                  </div>
                  <AgentSelect
                    testId="team-entry-picker"
                    agents={draft.members.map((m) => {
                      const a = agents.find((x) => x.id === m.agent_id);
                      return { ...a, id: m.agent_id, name: m.name || a?.name || m.agent_id };
                    })}
                    value={draft.entry_agent_id || ''}
                    onChange={(id) => set({ entry_agent_id: id || null })}
                    invalid={false}
                    singleLine
                    // The default names the member it stands for on the same
                    // line, since the field has no second one.
                    head={{
                      id: '',
                      name: draft.members[0]
                        ? `${t('teamDetails.firstOnTheRoster')} (${draft.members[0].name || draft.members[0].agent_id})`
                        : t('teamDetails.firstOnTheRoster'),
                    }}
                  />
                </div>
              )}
            </div>

            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teamDetails.description')}</label>
              <textarea
                value={draft.description} onChange={(e) => set({ description: e.target.value })}
                rows={2}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
              />
            </div>

            <div>
              <label className="block text-xs font-semibold text-gray-600 mb-1">
                {t('teamDetails.charterTheSystemPromptEvery')}
              </label>
              <textarea
                value={draft.charter} onChange={(e) => set({ charter: e.target.value })}
                rows={4}
                placeholder={t('teamDetails.whatThisTeamIsWhat')}
                className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
              />
              <p className="text-xs text-gray-500 mt-1">
                {t('teamDetails.charterHelp')}
              </p>
            </div>

            {draft.mode === 'centralized' && (
              <div className={`${FIT_GRID} [--fit-min:15rem] gap-4 items-end`}>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">
                    <Crown className="w-3.5 h-3.5 inline mr-1 -mt-0.5" /> {t('teamDetails.teamLead')}
                  </label>
                  <AgentSelect
                    testId="team-lead-picker"
                    agents={agents}
                    value={draft.leader_agent_id || ''}
                    onChange={(id) => set({ leader_agent_id: id || null })}
                    placeholder={t('teamDetails.selectAnAgent')}
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">
                    {t('teamDetails.nameTheTeamCallsThe')}
                  </label>
                  <input
                    value={draft.leader_name}
                    onChange={(e) => set({ leader_name: e.target.value })}
                    placeholder={t('teamDetails.lead')}
                    className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                  />
                </div>
              </div>
            )}

            {/* Roster */}
            <div>
              <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                <h3 className="text-sm font-bold text-gray-700 uppercase tracking-wide">{t('teamDetails.roster')}</h3>
                <div className="flex min-w-0 flex-1 items-center justify-end gap-2">
                  <div className="w-full max-w-[18rem]">
                    <AgentSelect
                      testId="team-add-member-picker"
                      agents={agents.filter((a) => !draft.members.some((m) => m.agent_id === a.id))}
                      value=""
                      onChange={(id) => {
                        const a = agents.find((x) => x.id === id);
                        set({
                          members: [...draft.members, {
                            agent_id: id, name: a?.name || id,
                            role: '', manifest: '', goal: '', memory_horizon: 10,
                          }],
                        });
                      }}
                      invalid={false}
                      placeholder={t('teamDetails.addAnAgent')}
                    />
                  </div>
                </div>
              </div>
              {draft.mode === 'autonomous' && (
                <p className="text-xs text-gray-500 mb-2">
                  {t('teamDetails.rosterOrderHelp')}
                </p>
              )}
              <div className={`${FIT_GRID} [--fit-min:20rem] gap-3 items-start`}>
                {draft.members.length === 0 && (
                  <p className="text-sm text-gray-500 italic col-span-full">
                    {t('teamDetails.noMembersYetATeam')}
                  </p>
                )}
                {draft.members.map((m, i) => (
                  <MemberCard
                    key={`${m.agent_id}-${i}`} member={m} agents={agents}
                    isLeader={draft.leader_agent_id === m.agent_id}
                    isEntry={draft.mode === 'autonomous' && m.agent_id === entryId}
                    onChange={(next) => setMember(i, next)}
                    onRemove={() => set({ members: draft.members.filter((_, j) => j !== i) })}
                  />
                ))}
              </div>
            </div>

            <div className={`${FIT_GRID} [--fit-min:10rem] gap-4 items-end`}>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teamDetails.maxRounds')}</label>
                <input
                  type="number" min={1} max={30} value={draft.max_rounds}
                  onChange={(e) => set({ max_rounds: Number(e.target.value) })}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teamDetails.parallelTurns')}</label>
                <input
                  type="number" min={1} max={12} value={draft.max_concurrent}
                  onChange={(e) => set({ max_concurrent: Number(e.target.value) })}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teamDetails.turnTimeoutS')}</label>
                <input
                  type="number" min={30} value={draft.turn_timeout}
                  onChange={(e) => set({ turn_timeout: Number(e.target.value) })}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">{t('teamDetails.costCeiling')}</label>
                <input
                  type="number" step="0.01" min={0} value={draft.cost_ceiling ?? ''}
                  onChange={(e) => set({ cost_ceiling: e.target.value === '' ? null : Number(e.target.value) })}
                  placeholder={t('teamDetails.none')}
                  className="w-full text-sm border border-gray-300 rounded-lg px-3 py-2"
                />
              </div>
            </div>

            <div className="flex items-center gap-6 flex-wrap">
              <label className="inline-flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox" checked={draft.allow_direct_messages}
                  onChange={(e) => set({ allow_direct_messages: e.target.checked })}
                />
                {t('teamDetails.allowDirectMessages')} (<code>{t('teamDetails.name2')}</code>)
              </label>
              <label className="inline-flex items-center gap-2 text-sm text-gray-700">
                <input
                  type="checkbox" checked={draft.synthesize}
                  onChange={(e) => set({ synthesize: e.target.checked })}
                />
                {t('teamDetails.closeWithOneWrittenAnswer')}
              </label>
            </div>

            <div className="flex flex-wrap justify-between items-center gap-2">
              <button
                onClick={() => onShowBriefing(draft.members[0]?.agent_id)}
                className="inline-flex items-center px-3 py-2 text-xs font-semibold text-gray-700 border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                <Eye className="w-3.5 h-3.5 mr-1.5" /> {t('teamDetails.seeWhatAMemberIs')}
              </button>
              <button
                onClick={onSave} disabled={saving}
                className="inline-flex items-center px-4 py-2 text-sm font-semibold text-white bg-indigo-600 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              >
                {saving ? <Loader className="w-4 h-4 mr-1.5 animate-spin" /> : <Save className="w-4 h-4 mr-1.5" />}
                {t('common.save')}
              </button>
            </div>

            {briefing && (
              <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="text-xs font-bold uppercase tracking-wide text-gray-500">
                    {t('teamDetails.briefingTitle', { agent: briefing.agent_id })}
                  </h4>
                  <button onClick={onCloseBriefing} className="p-1 text-gray-400 hover:text-gray-600">
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
                <pre className="text-xs text-gray-700 whitespace-pre-wrap max-h-80 overflow-auto">
                  {briefing.prompt}
                </pre>
              </div>
            )}
          </div>
  );
}
