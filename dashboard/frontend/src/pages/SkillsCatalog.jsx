import { useState, useEffect, useCallback } from 'react';
import { useWorkspace } from '../components/workspace';
import {
  GraduationCap,
  Globe2,
  Folder,
  Plus,
  Check,
  Search,
  RefreshCw,
  Trash2,
  Pencil,
  Share2,
  Download,
  Bot,
  Tag,
  X,
  ChevronDown,
  ChevronRight,
  ListOrdered,
} from 'lucide-react';
import {
  getSkills,
  getSkillTargets,
  getMarketplaceSkills,
  createSkill,
  updateSkill,
  updateSkillSharing,
  installSkill,
  deleteSkill,
} from '../api';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
const EMPTY_DRAFT = { name: '', description: '', steps: '', tags: '', agent_id: '' };

/**
 * Skills catalog.
 *
 * Two views over the same object. "My workspace" is what this workspace owns:
 * catalog entries (attached to nobody) and skills attached to an agent, which
 * is what actually puts them in that agent's prompt. "Global catalog" is every
 * skill any workspace has published — installing one copies it here, so the
 * original's author can never rewrite what your agents read.
 */
const SkillsCatalog = () => {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const workspace = selectedWorkspace || 'default';

  const [tab, setTab] = useState('workspace');
  const [mine, setMine] = useState([]);
  const [global, setGlobal] = useState([]);
  const [targets, setTargets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [message, setMessage] = useState('');
  const [busyId, setBusyId] = useState(null);
  const [expanded, setExpanded] = useState({});

  const [editor, setEditor] = useState(null);   // { draft, id | null }
  const [installFor, setInstallFor] = useState(null); // skill being attached/installed
  const [installAgent, setInstallAgent] = useState('');

  const notify = (text) => {
    setMessage(text);
    setTimeout(() => setMessage(''), 5000);
  };

  const fetchData = useCallback(async () => {
    try {
      const [minResp, globalResp, targetResp] = await Promise.all([
        getSkills(workspace),
        getMarketplaceSkills(workspace),
        getSkillTargets(workspace),
      ]);
      setMine(minResp.data || []);
      setGlobal(globalResp.data || []);
      setTargets(targetResp.data || []);
    } catch (error) {
      console.error('Error fetching skills:', error);
    } finally {
      setLoading(false);
    }
  }, [workspace]);

  useEffect(() => {
    setLoading(true);
    fetchData();
  }, [fetchData]);

  const matches = (skill) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return (
      (skill.name || '').toLowerCase().includes(q) ||
      (skill.description || '').toLowerCase().includes(q) ||
      (skill.tags || []).some((t) => t.toLowerCase().includes(q)) ||
      (skill.agent_id || '').toLowerCase().includes(q)
    );
  };

  const visibleMine = mine.filter(matches);
  const visibleGlobal = global.filter(matches);

  // ── Actions ────────────────────────────────────────────────────────────────

  const openCreate = () => setEditor({ id: null, draft: { ...EMPTY_DRAFT } });

  const openEdit = (skill) => setEditor({
    id: skill.id,
    draft: {
      name: skill.name,
      description: skill.description,
      steps: (skill.steps || []).join('\n'),
      tags: (skill.tags || []).join(', '),
      agent_id: skill.agent_id || '',
    },
  });

  const saveEditor = async () => {
    const { id, draft } = editor;
    const steps = draft.steps.split('\n').map((s) => s.trim()).filter(Boolean);
    const tags = draft.tags.split(',').map((t) => t.trim()).filter(Boolean);
    if (!draft.name.trim()) return alert(t('skillsCatalog.needsName'));
    if (steps.length === 0) return alert(t('skillsCatalog.needsStep'));
    setBusyId(id || 'new');
    try {
      if (id) {
        await updateSkill(id, { name: draft.name, description: draft.description, steps, tags });
        notify(`Skill "${draft.name}" updated.`);
      } else {
        const resp = await createSkill({
          workspace,
          name: draft.name,
          description: draft.description,
          steps,
          tags,
          agent_id: draft.agent_id || '',
        });
        notify(
          resp.data.skills_enabled_updated
            ? `Skill "${draft.name}" created and skills turned on for ${draft.agent_id}.`
            : `Skill "${draft.name}" created.`
        );
      }
      setEditor(null);
      await fetchData();
    } catch (error) {
      alert(`${t('common.error')}: ` + (error.response?.data?.detail || error.message));
    } finally {
      setBusyId(null);
    }
  };

  const togglePublish = async (skill) => {
    setBusyId(skill.id);
    try {
      await updateSkillSharing(skill.id, !skill.shared);
      notify(
        skill.shared
          ? t('skillsCatalog.withdrawn', { name: skill.name })
          : t('skillsCatalog.publishedNotice', { name: skill.name })
      );
      await fetchData();
    } catch (error) {
      alert(`${t('common.error')}: ` + (error.response?.data?.detail || error.message));
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (skill) => {
    const where = skill.agent_id
      ? t('skillsCatalog.detachAndDelete', { agent: skill.agent_id })
      : t('skillsCatalog.justDelete');
    if (!window.confirm(t('skillsCatalog.confirmRemove', { name: skill.name, where }))) return;
    setBusyId(skill.id);
    try {
      await deleteSkill(skill.id);
      notify(t('skillsCatalog.removed', { name: skill.name }));
      await fetchData();
    } catch (error) {
      alert(`${t('common.error')}: ` + (error.response?.data?.detail || error.message));
    } finally {
      setBusyId(null);
    }
  };

  const openInstall = (skill) => {
    setInstallFor(skill);
    setInstallAgent('');
  };

  const confirmInstall = async () => {
    const skill = installFor;
    setBusyId(skill.id);
    try {
      const resp = await installSkill(skill.id, { workspace, agent_id: installAgent || '' });
      const target = installAgent
        ? t('skillsCatalog.targetAgent', { agent: installAgent })
        : t('skillsCatalog.targetWorkspace', { workspace });
      notify(
        resp.data.already_present
          ? t('skillsCatalog.alreadyInstalled', { name: skill.name, target })
          : t('skillsCatalog.installed', { name: skill.name, target })
            + (resp.data.skills_enabled_updated ? ` ${t('skillsCatalog.skillsTurnedOn')}` : '')
      );
      setInstallFor(null);
      await fetchData();
    } catch (error) {
      alert(`${t('common.error')}: ` + (error.response?.data?.detail || error.message));
    } finally {
      setBusyId(null);
    }
  };

  const toggleExpanded = (id) => setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));

  // ── Card ───────────────────────────────────────────────────────────────────

  const StepList = ({ skill, steps }) => (
    <div className="mb-3">
      <button
        onClick={() => toggleExpanded(skill.id)}
        className="inline-flex items-center gap-1 text-[11px] font-semibold text-gray-500 hover:text-indigo-600"
      >
        {expanded[skill.id] ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        <ListOrdered className="w-3 h-3" />
        {steps.length} step{steps.length === 1 ? '' : 's'}
      </button>
      {expanded[skill.id] && (
        <ol className="mt-2 pl-4 list-decimal space-y-1 text-xs text-gray-600 bg-gray-50 rounded p-2 border border-gray-100">
          {steps.map((step, i) => <li key={i}>{step}</li>)}
        </ol>
      )}
    </div>
  );

  const TagRow = ({ tags }) => (
    <div className="flex flex-wrap gap-1 mb-3 min-h-[22px]">
      {(tags || []).map((t) => (
        <span key={t} className="text-[10px] bg-white border border-gray-200 px-1.5 py-0.5 rounded text-gray-600 inline-flex items-center gap-1">
          <Tag className="w-2.5 h-2.5" /> {t}
        </span>
      ))}
    </div>
  );

  return (
    <PageContainer className="space-y-6">
      <PageHeader
        icon={GraduationCap}
        title={t('skillsCatalog.skillsCatalog')}
        description={t('skillsCatalog.reusableProceduresAgentsCanFollow')}
        actions={<>
          <div className="flex rounded-lg border border-gray-200 overflow-hidden">
            <button
              onClick={() => setTab('workspace')}
              className={`inline-flex items-center px-3 py-2 text-xs font-semibold transition-colors ${
                tab === 'workspace' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              <Folder className="w-3.5 h-3.5 mr-1.5" /> My workspace ({mine.length})
            </button>
            <button
              onClick={() => setTab('global')}
              className={`inline-flex items-center px-3 py-2 text-xs font-semibold transition-colors ${
                tab === 'global' ? 'bg-indigo-600 text-white' : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              <Globe2 className="w-3.5 h-3.5 mr-1.5" /> Global catalog ({global.length})
            </button>
          </div>
          <div className="relative">
            <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder={t('skillsCatalog.searchSkills')}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="pl-9 pr-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none w-56"
            />
          </div>
          <button
            onClick={openCreate}
            className="inline-flex items-center px-3 py-2 text-xs font-semibold bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors"
          >
            <Plus className="w-3.5 h-3.5 mr-1.5" /> {t('skillsCatalog.newSkill')}
          </button>
        </>}
      />

      {message && (
        <div className="bg-green-50 border border-green-200 text-green-700 text-sm rounded-xl px-4 py-3 flex items-center gap-2">
          <Check className="w-4 h-4 shrink-0" /> {message}
        </div>
      )}

      {loading ? (
        <div className="flex flex-col items-center justify-center py-20 bg-white rounded-xl border border-dashed border-gray-200">
          <RefreshCw className="w-8 h-8 text-indigo-400 animate-spin mb-4" />
          <p className="text-gray-500 font-medium">{t('skillsCatalog.loadingSkills')}</p>
        </div>
      ) : tab === 'workspace' ? (
        visibleMine.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-20 bg-white rounded-xl border border-dashed border-gray-200">
            <GraduationCap className="w-10 h-10 text-gray-300 mb-3" />
            <p className="text-gray-500 font-medium">
              {query ? t('skillsCatalog.noSkillsMatch') : t('skillsCatalog.noSkillsInWorkspace', { workspace })}
            </p>
            {!query && (
              <p className="text-gray-400 text-sm mt-1 max-w-md text-center">
                Write one here, or install a published skill from the global catalog.
                A skill only reaches an agent once it is attached to it.
              </p>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {visibleMine.map((skill) => (
              <div key={skill.id} className="bg-white rounded-lg border border-gray-100 p-4 shadow-sm hover:shadow-md transition-all flex flex-col">
                <div className="flex items-start justify-between gap-2 mb-3">
                  <div className="flex items-start gap-2 min-w-0 flex-1">
                    <div className="p-1.5 rounded bg-indigo-50 text-indigo-600">
                      <GraduationCap className="w-4 h-4" />
                    </div>
                    <div className="min-w-0">
                      <div className="text-sm font-semibold text-gray-900 truncate" title={skill.name}>{skill.name}</div>
                      <div className="text-[11px] text-gray-400">
                        {skill.source === 'agent' ? t('skillsCatalog.learnedByAgent') : t('skillsCatalog.writtenByUser')}
                        {skill.use_count > 0 && ` · ${t('skillsCatalog.usedCount', { count: skill.use_count })}`}
                      </div>
                    </div>
                  </div>
                  {skill.shared && (
                    <span className="text-[10px] bg-emerald-50 text-emerald-700 border border-emerald-200 px-2 py-0.5 rounded uppercase font-semibold shrink-0" title={t('skillsCatalog.publishedToTheGlobalCatalog')}>
                      {t('skillsCatalog.published')}
                    </span>
                  )}
                </div>

                <p className="text-xs text-gray-500 leading-relaxed mb-3 line-clamp-3 min-h-[3em]">
                  {skill.description || t('skillsCatalog.noDescription')}
                </p>

                <TagRow tags={skill.tags} />
                <StepList skill={skill} steps={skill.steps || []} />

                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] mb-4">
                  {skill.agent_id ? (
                    <span className="inline-flex items-center gap-1 text-indigo-600" title={t('skillsCatalog.attachedToThisAgent')}>
                      <Bot className="w-3 h-3" /> {skill.agent?.name || skill.agent_id}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-amber-600" title={t('skillsCatalog.notAttachedToAnyAgent')}>
                      <Bot className="w-3 h-3" /> {t('skillsCatalog.catalogEntry')}
                    </span>
                  )}
                  {skill.origin_skill_id && (
                    <span className="inline-flex items-center gap-1 text-gray-400" title={t('skillsCatalog.installedCopyOfAPublished')}>
                      <Download className="w-3 h-3" /> {t('skillsCatalog.installedCopy')}
                    </span>
                  )}
                </div>

                <div className="mt-auto grid grid-cols-2 gap-2">
                  <button
                    onClick={() => openInstall(skill)}
                    disabled={busyId === skill.id}
                    title={t('skillsCatalog.attachACopyOfThis')}
                    className="inline-flex items-center justify-center px-2 py-1.5 text-xs font-semibold bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 transition-colors"
                  >
                    <Bot className="w-3.5 h-3.5 mr-1" /> {t('skillsCatalog.attach')}
                  </button>
                  <button
                    onClick={() => togglePublish(skill)}
                    disabled={busyId === skill.id}
                    title={skill.shared ? t('skillsCatalog.withdrawTitle') : t('skillsCatalog.publishTitle')}
                    className={`inline-flex items-center justify-center px-2 py-1.5 text-xs font-semibold rounded border transition-colors disabled:opacity-50 ${
                      skill.shared
                        ? 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100'
                        : 'border-gray-200 text-gray-600 hover:bg-gray-50'
                    }`}
                  >
                    <Share2 className="w-3.5 h-3.5 mr-1" /> {skill.shared ? 'Published' : 'Publish'}
                  </button>
                  <button
                    onClick={() => openEdit(skill)}
                    className="inline-flex items-center justify-center px-2 py-1.5 text-xs font-semibold border border-gray-200 text-gray-600 rounded hover:bg-gray-50 transition-colors"
                  >
                    <Pencil className="w-3.5 h-3.5 mr-1" /> {t('skillsCatalog.edit')}
                  </button>
                  <button
                    onClick={() => remove(skill)}
                    disabled={busyId === skill.id}
                    className="inline-flex items-center justify-center px-2 py-1.5 text-xs font-semibold border border-red-200 text-red-600 rounded hover:bg-red-50 disabled:opacity-50 transition-colors"
                  >
                    <Trash2 className="w-3.5 h-3.5 mr-1" /> {skill.agent_id ? 'Detach' : 'Delete'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )
      ) : visibleGlobal.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 bg-white rounded-xl border border-dashed border-gray-200">
          <Globe2 className="w-10 h-10 text-gray-300 mb-3" />
          <p className="text-gray-500 font-medium">
            {query ? t('skillsCatalog.noSkillsMatch') : t('skillsCatalog.noSkillsPublished')}
          </p>
          {!query && (
            <p className="text-gray-400 text-sm mt-1 max-w-md text-center">
              {t('skillsCatalog.publishOneOfYourWorkspace')}
            </p>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {visibleGlobal.map((skill) => (
            <div key={skill.id} className="bg-white rounded-lg border border-gray-100 p-4 shadow-sm hover:shadow-md transition-all flex flex-col">
              <div className="flex items-start justify-between gap-2 mb-3">
                <div className="flex items-start gap-2 min-w-0 flex-1">
                  <div className="p-1.5 rounded bg-emerald-50 text-emerald-600">
                    <Globe2 className="w-4 h-4" />
                  </div>
                  <div className="min-w-0">
                    <div className="text-sm font-semibold text-gray-900 truncate" title={skill.name}>{skill.name}</div>
                    <div className="text-[11px] text-gray-400">
                      {t('skillsCatalog.stepCount', { count: skill.steps_count })}
                      {skill.use_count > 0 && ` · ${t('skillsCatalog.usedCount', { count: skill.use_count })}`}
                    </div>
                  </div>
                </div>
              </div>

              <p className="text-xs text-gray-500 leading-relaxed mb-3 line-clamp-3 min-h-[3em]">
                {skill.description || t('skillsCatalog.noDescription')}
              </p>

              <TagRow tags={skill.tags} />
              <StepList skill={skill} steps={skill.steps || []} />

              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-400 mb-4">
                <span className="inline-flex items-center gap-1" title={t('skillsCatalog.publishedFromThisWorkspace')}>
                  <Folder className="w-3 h-3" /> {skill.owner_workspace}
                </span>
                {skill.authored_for && (
                  <span className="inline-flex items-center gap-1" title={t('skillsCatalog.writtenForThisAgent')}>
                    <Bot className="w-3 h-3" /> for {skill.authored_for.name}
                  </span>
                )}
              </div>

              <div className="mt-auto flex items-center gap-2">
                {skill.in_workspace ? (
                  <span className="flex-1 inline-flex items-center justify-center px-2 py-1.5 text-xs font-semibold bg-green-50 text-green-700 border border-green-200 rounded">
                    <Check className="w-3.5 h-3.5 mr-1" /> {t('skillsCatalog.inWorkspace')}
                  </span>
                ) : (
                  <button
                    onClick={() => openInstall(skill)}
                    disabled={busyId === skill.id}
                    className="flex-1 inline-flex items-center justify-center px-2 py-1.5 text-xs font-semibold bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50 transition-colors"
                  >
                    {busyId === skill.id
                      ? <RefreshCw className="w-3.5 h-3.5 mr-1 animate-spin" />
                      : <Download className="w-3.5 h-3.5 mr-1" />}
                    Install
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create / edit modal */}
      {editor && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
              <h3 className="text-lg font-bold text-gray-800 flex items-center gap-2">
                <GraduationCap className="w-5 h-5 text-indigo-600" />
                {editor.id ? t('skillsCatalog.editSkill') : t('skillsCatalog.newSkillIn', { workspace })}
              </h3>
              <button onClick={() => setEditor(null)} className="text-gray-400 hover:text-gray-600">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-6 space-y-4">
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">{t('skillsCatalog.name')}</label>
                <input
                  value={editor.draft.name}
                  onChange={(e) => setEditor({ ...editor, draft: { ...editor.draft, name: e.target.value } })}
                  placeholder={t('skillsCatalog.triageABugReport')}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">
                  {t('skillsCatalog.whenToUseIt')}
                </label>
                <textarea
                  value={editor.draft.description}
                  onChange={(e) => setEditor({ ...editor, draft: { ...editor.draft, description: e.target.value } })}
                  rows={2}
                  placeholder={t('skillsCatalog.matchedAgainstIncomingInstructionsWrite')}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">{t('skillsCatalog.stepsOnePerLine')}</label>
                <textarea
                  value={editor.draft.steps}
                  onChange={(e) => setEditor({ ...editor, draft: { ...editor.draft, steps: e.target.value } })}
                  rows={8}
                  placeholder={t('skillsCatalog.stepsPlaceholder')}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm font-mono focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                />
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-gray-600 mb-1">{t('skillsCatalog.tagsCommaSeparated')}</label>
                  <input
                    value={editor.draft.tags}
                    onChange={(e) => setEditor({ ...editor, draft: { ...editor.draft, tags: e.target.value } })}
                    placeholder={t('skillsCatalog.debuggingPython')}
                    className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                  />
                </div>
                {!editor.id && (
                  <div>
                    <label className="block text-xs font-semibold text-gray-600 mb-1">{t('skillsCatalog.attachToAgent')}</label>
                    <select
                      value={editor.draft.agent_id}
                      onChange={(e) => setEditor({ ...editor, draft: { ...editor.draft, agent_id: e.target.value } })}
                      className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                    >
                      <option value="">{t('skillsCatalog.catalogEntryNoAgent')}</option>
                      {targets.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name} ({t.skills_count} skill{t.skills_count === 1 ? '' : 's'})
                        </option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            </div>
            <div className="flex justify-end gap-2 px-6 py-4 border-t border-gray-100">
              <button
                onClick={() => setEditor(null)}
                className="px-4 py-2 text-sm font-semibold border border-gray-200 text-gray-600 rounded-lg hover:bg-gray-50"
              >
                {t('skillsCatalog.cancel')}
              </button>
              <button
                onClick={saveEditor}
                disabled={busyId !== null}
                className="px-4 py-2 text-sm font-semibold bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              >
                {editor.id ? t('skillsCatalog.saveChanges') : t('skillsCatalog.createSkill')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Install / attach modal */}
      {installFor && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-lg">
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
              <h3 className="text-lg font-bold text-gray-800 flex items-center gap-2">
                <Download className="w-5 h-5 text-indigo-600" /> Install "{installFor.name}"
              </h3>
              <button onClick={() => setInstallFor(null)} className="text-gray-400 hover:text-gray-600">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="p-6 space-y-4">
              <p className="text-xs text-gray-500">
                A copy is created in workspace <strong>{workspace}</strong>. Attaching it to an agent is
                what puts it in that agent's prompt — leave the agent unset to keep it as a catalog
                entry for now. Installing turns on that agent's skills setting if it is off.
              </p>
              <div>
                <label className="block text-xs font-semibold text-gray-600 mb-1">{t('skillsCatalog.agent')}</label>
                <select
                  value={installAgent}
                  onChange={(e) => setInstallAgent(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:ring-2 focus:ring-indigo-500 focus:outline-none"
                >
                  <option value="">{t('skillsCatalog.workspaceCatalogOnlyNoAgent')}</option>
                  {targets.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}{t.skills_enabled ? '' : ' — skills off'}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex justify-end gap-2 px-6 py-4 border-t border-gray-100">
              <button
                onClick={() => setInstallFor(null)}
                className="px-4 py-2 text-sm font-semibold border border-gray-200 text-gray-600 rounded-lg hover:bg-gray-50"
              >
                {t('skillsCatalog.cancel')}
              </button>
              <button
                onClick={confirmInstall}
                disabled={busyId === installFor.id}
                className="px-4 py-2 text-sm font-semibold bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50"
              >
                {t('skillsCatalog.install')}
              </button>
            </div>
          </div>
        </div>
      )}
    </PageContainer>
  );
};

export default SkillsCatalog;
