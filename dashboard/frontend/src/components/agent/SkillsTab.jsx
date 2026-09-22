import { BookOpen, CheckCircle, ChevronUp, Loader, Plus, Save, Tag, Trash2 } from 'lucide-react';
import { useAgentPage } from './context';

/** The skills this agent has been taught. */
export default function SkillsTab() {
  const {
    handleDeleteSkill, handleSaveSkill, handleToggleSkillsEnabled, selectedWorkspace,
    setShowAddSkill, setSkillForm, showAddSkill, skillDeleteBusy, skillForm, skillSaving,
    skills, skillsConfigSaving, skillsEnabled, skillsLoading, skillsMessage, t,
  } = useAgentPage();
  return (
        <div className="space-y-5">

          {/* ── Configuration card ── */}
          <div className="bg-white rounded-xl border border-t-4 border-t-purple-500 border-gray-200 p-6 shadow-sm">
            <h3 className="text-base font-bold text-gray-900 mb-1 flex items-center gap-2">
              <BookOpen className="w-5 h-5 text-purple-500" /> {t('agentDetails.skillsConfiguration')}
            </h3>
            <p className="text-sm text-gray-500 mb-5">
              When enabled, relevant skills are automatically matched to the task description and injected before the agent starts.
              Agents can also discover and save new skills using the <code className="bg-gray-100 px-1 py-0.5 rounded text-xs">{t('agentDetails.saveSkill')}</code> tool.
            </p>
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium text-gray-700">{t('agentDetails.proceduralSkills')}</p>
                <p className="text-xs text-gray-400 mt-0.5">{t('agentDetails.scopeThisAgentWorkspaceSpecific')}</p>
              </div>
              <button
                onClick={() => handleToggleSkillsEnabled(!skillsEnabled)}
                disabled={skillsConfigSaving}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${skillsEnabled ? 'bg-purple-600' : 'bg-gray-200'}`}
              >
                {skillsConfigSaving
                  ? <Loader className="absolute w-3 h-3 animate-spin text-white left-1/2 -translate-x-1/2" />
                  : <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${skillsEnabled ? 'translate-x-6' : 'translate-x-1'}`} />
                }
              </button>
            </div>
          </div>

          {/* ── Skills list ── */}
          {!selectedWorkspace ? (
            <div className="bg-white rounded-xl border border-gray-200 p-10 text-center shadow-sm">
              <BookOpen className="w-8 h-8 text-gray-300 mx-auto mb-3" />
              <p className="text-sm text-gray-500">{t('agentDetails.selectAWorkspaceToView')}</p>
            </div>
          ) : (
            <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
              {/* Header */}
              <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
                <div>
                  <h3 className="text-sm font-bold text-gray-900 flex items-center gap-2">
                    <BookOpen className="w-4 h-4 text-purple-500" /> Skills
                    <span className="text-xs font-normal text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded-full">{selectedWorkspace}</span>
                  </h3>
                  {skillsMessage && (
                    <p className="text-xs text-green-600 mt-0.5 flex items-center gap-1">
                      <CheckCircle className="w-3 h-3" /> {skillsMessage}
                    </p>
                  )}
                </div>
                <button
                  onClick={() => setShowAddSkill(v => !v)}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-white bg-purple-600 rounded-lg hover:bg-purple-700"
                >
                  {showAddSkill ? <ChevronUp className="w-3.5 h-3.5" /> : <Plus className="w-3.5 h-3.5" />}
                  {showAddSkill ? t('common.cancel') : t('agentDetails.addSkill')}
                </button>
              </div>

              {/* Add skill form */}
              {showAddSkill && (
                <div className="px-5 py-4 bg-purple-50 border-b border-purple-100 space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">{t('agentDetails.name')}</label>
                      <input
                        type="text"
                        value={skillForm.name}
                        onChange={e => setSkillForm(f => ({ ...f, name: e.target.value }))}
                        placeholder={t('agentDetails.eGFixPythonImport')}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-semibold text-gray-600 mb-1">{t('agentDetails.tags')} <span className="text-gray-400 font-normal">({t('agentDetails.commaSeparated')})</span></label>
                      <input
                        type="text"
                        value={skillForm.tags}
                        onChange={e => setSkillForm(f => ({ ...f, tags: e.target.value }))}
                        placeholder={t('agentDetails.eGDebuggingPythonApi')}
                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
                      />
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-gray-600 mb-1">{t('agentDetails.whenToUseThisSkill')}</label>
                    <input
                      type="text"
                      value={skillForm.description}
                      onChange={e => setSkillForm(f => ({ ...f, description: e.target.value }))}
                      placeholder={t('agentDetails.eGWhenAPython')}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-purple-500"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-gray-600 mb-1">{t('agentDetails.steps')} <span className="text-gray-400 font-normal">({t('agentDetails.onePerLine')})</span></label>
                    <textarea
                      value={skillForm.steps}
                      onChange={e => setSkillForm(f => ({ ...f, steps: e.target.value }))}
                      rows={5}
                      placeholder={t('agentDetails.stepsPlaceholder')}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-purple-500 resize-none"
                    />
                  </div>
                  <div className="flex justify-end">
                    <button
                      onClick={handleSaveSkill}
                      disabled={skillSaving || !skillForm.name || !skillForm.description || !skillForm.steps.trim()}
                      className="inline-flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-purple-600 rounded-lg hover:bg-purple-700 disabled:opacity-40"
                    >
                      {skillSaving ? <Loader className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                      {skillSaving ? t('common.saving') : t('agentDetails.saveSkillButton')}
                    </button>
                  </div>
                </div>
              )}

              {/* Skills table */}
              {skillsLoading ? (
                <div className="flex justify-center py-10">
                  <Loader className="w-5 h-5 animate-spin text-purple-400" />
                </div>
              ) : skills.length === 0 ? (
                <div className="py-12 text-center">
                  <BookOpen className="w-8 h-8 text-gray-200 mx-auto mb-3" />
                  <p className="text-sm text-gray-500">{t('agentDetails.noSkillsYetForThis')} <strong>{selectedWorkspace}</strong>.</p>
                  <p className="text-xs text-gray-400 mt-1">{t('agentDetails.addOneAboveOrThe')} <code className="bg-gray-100 px-1 rounded">{t('agentDetails.saveSkill')}</code> {t('agentDetails.isCalled')}</p>
                </div>
              ) : (
                <div className="divide-y divide-gray-100">
                  {skills.map(skill => (
                    <div key={skill.id} className="px-5 py-4 hover:bg-gray-50 group">
                      <div className="flex items-start justify-between gap-4">
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2 flex-wrap mb-1">
                            <span className="font-semibold text-sm text-gray-900">{skill.name}</span>
                            <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${skill.source === 'user' ? 'bg-purple-100 text-purple-700' : 'bg-blue-100 text-blue-700'}`}>
                              {skill.source}
                            </span>
                            {skill.use_count > 0 && (
                              <span className="text-[10px] text-gray-400">{t('agentDetails.usedCount', { count: skill.use_count })}</span>
                            )}
                          </div>
                          <p className="text-xs text-gray-500 italic mb-2">{skill.description}</p>
                          {skill.tags.length > 0 && (
                            <div className="flex items-center gap-1 flex-wrap mb-2">
                              <Tag className="w-3 h-3 text-gray-300" />
                              {skill.tags.map(tag => (
                                <span key={tag} className="text-[10px] bg-gray-100 text-gray-500 px-1.5 py-0.5 rounded">{tag}</span>
                              ))}
                            </div>
                          )}
                          <ol className="space-y-0.5 pl-4">
                            {skill.steps.map((step, i) => (
                              <li key={i} className="text-xs text-gray-600 list-decimal">{step}</li>
                            ))}
                          </ol>
                        </div>
                        <button
                          onClick={() => handleDeleteSkill(skill.id)}
                          disabled={skillDeleteBusy[skill.id]}
                          className="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity text-gray-300 hover:text-red-500 mt-0.5"
                          title={t('agentDetails.deleteSkill')}
                        >
                          {skillDeleteBusy[skill.id]
                            ? <Loader className="w-4 h-4 animate-spin" />
                            : <Trash2 className="w-4 h-4" />
                          }
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
  );
}
