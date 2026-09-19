import { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { useWorkspace } from '../components/workspace';
import {
  Store,
  Shield,
  Wrench,
  Terminal,
  BookOpen,
  BrainCircuit,
  FileCode,
  Zap,
  Plus,
  Check,
  RefreshCw,
  Folder,
  Tag,
} from 'lucide-react';
import { getMarketplaceAgent, addAgentToWorkspace } from '../api';

import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';
const formatCategory = (c) => (c || 'other').replace(/_/g, ' ').replace(/\b\w/g, ch => ch.toUpperCase());

const MarketplaceAgent = () => {
  const { t } = useI18n();
  const { id } = useParams();
  const { selectedWorkspace } = useWorkspace();
  const [agent, setAgent] = useState(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [adding, setAdding] = useState(false);
  const [message, setMessage] = useState('');

  const isDefaultWs = !selectedWorkspace || selectedWorkspace === 'default';

  useEffect(() => {
    setLoading(true);
    setNotFound(false);
    getMarketplaceAgent(id, selectedWorkspace || 'default')
      .then(resp => setAgent(resp.data))
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [id, selectedWorkspace]);

  const handleAdd = async () => {
    if (isDefaultWs || !agent) return;
    setAdding(true);
    setMessage('');
    try {
      await addAgentToWorkspace(selectedWorkspace, agent.id);
      setAgent(prev => (prev ? { ...prev, in_workspace: true } : prev));
      setMessage(t('marketplaceAgent.addedToWorkspace', { workspace: selectedWorkspace }));
      setTimeout(() => setMessage(''), 4000);
    } catch (error) {
      alert(`${t('common.error')}: ` + (error.response?.data?.detail || error.message));
    } finally {
      setAdding(false);
    }
  };

  if (loading) return <div className="text-center py-10">{t('marketplaceAgent.loadingAgent')}</div>;
  if (notFound || !agent) {
    return (
      <PageContainer>
        <PageHeader
          icon={Shield}
          title={t('marketplaceAgent.agentNotFound')}
          backTo="/marketplace"
          backLabel={t('marketplaceAgent.backToMarketplace')}
        />
        <div className="text-center py-10 text-gray-500">{t('marketplaceAgent.agentNotFoundOnThe')}</div>
      </PageContainer>
    );
  }

  const definition = agent.definition || {};
  const tools = agent.tools || [];
  const toolCategories = (() => {
    const groups = {};
    tools.forEach((t) => {
      const cat = t.category || 'other';
      (groups[cat] = groups[cat] || []).push(t);
    });
    return Object.entries(groups)
      .map(([category, items]) => ({ category, items: items.sort((a, b) => a.id.localeCompare(b.id)) }))
      .sort((a, b) => a.category.localeCompare(b.category));
  })();
  const commands = agent.commands || [];
  const skills = agent.skills || [];
  const model = agent.model || {};

  return (
    <PageContainer>
      <PageHeader
        icon={Shield}
        title={agent.name}
        description={agent.id}
        backTo="/marketplace"
        backLabel={t('marketplaceAgent.backToMarketplace')}
        badges={<>
          <span className="text-[10px] bg-gray-100 text-gray-600 px-2 py-0.5 rounded uppercase font-semibold">
            {agent.domain || 'general'}
          </span>
          <span className="inline-flex items-center gap-1 text-[10px] bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded uppercase font-semibold">
            <Store className="w-3 h-3" /> {t('marketplaceAgent.marketplace')}
          </span>
        </>}
        actions={
          <div className="shrink-0 text-right">
            {agent.in_workspace ? (
              <span className="inline-flex items-center px-4 py-2 text-sm font-semibold bg-green-50 text-green-700 border border-green-200 rounded-lg">
                <Check className="w-4 h-4 mr-1.5" />
                {isDefaultWs ? t('marketplaceAgent.availableInDefault') : t('marketplaceAgent.inWorkspace', { workspace: selectedWorkspace })}
              </span>
            ) : (
              <button
                onClick={handleAdd}
                disabled={adding}
                className="inline-flex items-center px-4 py-2 text-sm font-bold bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 shadow-md transition-colors"
              >
                {adding ? <RefreshCw className="w-4 h-4 mr-1.5 animate-spin" /> : <Plus className="w-4 h-4 mr-1.5" />}
                {t('marketplaceAgent.addToWorkspace', { workspace: selectedWorkspace })}
              </button>
            )}
            {message && <p className="text-xs text-green-600 mt-2 text-right">{message}</p>}
          </div>
        }
      >
        {agent.description && (
          <p className="text-sm text-gray-600 mt-3 leading-relaxed max-w-3xl">{agent.description}</p>
        )}
        {agent.owner_workspace && (
          <p className="text-xs text-gray-400 mt-2 flex items-center gap-1">
            <Folder className="w-3 h-3" /> {t('marketplaceAgent.publishedFromWorkspace')}
            <span className="font-semibold text-gray-600">{agent.owner_workspace}</span>
          </p>
        )}
      </PageHeader>

      <div className="space-y-6">
        {/* Model */}
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
            <BrainCircuit className="w-4 h-4 text-indigo-500" /> {t('marketplaceAgent.model')}
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-x-8 gap-y-4 text-sm">
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('marketplaceAgent.provider')}</span>
              <span className="font-medium capitalize">
                {model.provider && model.provider !== 'inherit' ? model.provider : t('marketplaceAgent.inheritsWorkspaceGlobal')}
              </span>
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('marketplaceAgent.model')}</span>
              <span className="text-gray-700 text-xs break-all">{model.model || '—'}</span>
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('marketplaceAgent.temperature')}</span>
              <span className="text-gray-700 text-xs">{model.temperature != null ? model.temperature : 'inherit'}</span>
            </div>
            <div className="flex flex-col gap-0.5">
              <span className="text-[10px] text-gray-400 uppercase tracking-wider font-semibold">{t('marketplaceAgent.maxTokens')}</span>
              <span className="text-gray-700 text-xs">{model.max_tokens != null ? model.max_tokens : 'inherit'}</span>
            </div>
          </div>
        </div>

        {/* Definition */}
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
            <FileCode className="w-4 h-4 text-indigo-500" /> {t('marketplaceAgent.definition')}
          </h3>
          {[
            { key: 'instructions', label: 'instructions.md', icon: Terminal },
            { key: 'capabilities', label: 'capabilities.md', icon: Zap },
            { key: 'usage', label: 'usage.md', icon: BookOpen },
          ].map(({ key, label, icon: Icon }) => {
            const content = definition[key];
            if (!content) return null;
            return (
              <div key={key} className="mb-5 last:mb-0">
                <p className="text-xs font-semibold text-gray-500 mb-2 flex items-center gap-1.5">
                  <Icon className="w-3.5 h-3.5 text-indigo-400" /> {label}
                </p>
                <pre className="text-xs bg-gray-900 text-green-300 p-4 rounded-lg overflow-auto whitespace-pre-wrap max-h-80">
                  {content}
                </pre>
              </div>
            );
          })}
          {!definition.instructions && !definition.capabilities && !definition.usage && (
            <p className="text-sm text-gray-400">{t('marketplaceAgent.noDefinitionMarkdownAvailableFor')}</p>
          )}
        </div>

        {/* Tools */}
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
            <Wrench className="w-4 h-4 text-indigo-500" /> {t('marketplaceAgent.tools')}
            <span className="text-xs font-normal text-gray-400">({tools.length})</span>
          </h3>
          {tools.length === 0 ? (
            <p className="text-sm text-gray-400">{t('marketplaceAgent.thisAgentHasNoTools')}</p>
          ) : (
            <div className="space-y-4">
              {toolCategories.map(({ category, items }) => (
                <div key={category}>
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-2">
                    {formatCategory(category)}
                  </h4>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                    {items.map((tool) => (
                      <div key={tool.id} className="border border-gray-200 rounded-lg px-3 py-2 bg-gray-50">
                        <p className="text-sm font-medium text-gray-800">{tool.name || tool.id}</p>
                        {tool.description && (
                          <p className="text-xs text-gray-500 mt-0.5">{tool.description}</p>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Skills */}
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
            <BookOpen className="w-4 h-4 text-indigo-500" /> {t('marketplaceAgent.skills')}
            <span className="text-xs font-normal text-gray-400">({skills.length})</span>
            {agent.skills_enabled && (
              <span className="text-[10px] bg-green-100 text-green-700 px-2 py-0.5 rounded-full uppercase font-semibold">{t('marketplaceAgent.enabled')}</span>
            )}
          </h3>
          {skills.length === 0 ? (
            <p className="text-sm text-gray-400">
              {agent.skills_enabled
                ? t('marketplaceAgent.skillsEnabledNoneAuthored')
                : t('marketplaceAgent.noSkills')}
            </p>
          ) : (
            <div className="space-y-3">
              {skills.map((skill) => (
                <div key={skill.id} className="border border-gray-200 rounded-lg px-4 py-3">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className="text-sm font-semibold text-gray-800">{skill.name}</p>
                    {(skill.tags || []).map((tag) => (
                      <span key={tag} className="inline-flex items-center gap-0.5 text-[10px] bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded">
                        <Tag className="w-2.5 h-2.5" /> {tag}
                      </span>
                    ))}
                  </div>
                  {skill.description && <p className="text-xs text-gray-500 mt-1">{skill.description}</p>}
                  {(skill.steps || []).length > 0 && (
                    <ol className="list-decimal list-inside text-xs text-gray-600 mt-2 space-y-0.5">
                      {skill.steps.map((step, i) => <li key={i}>{step}</li>)}
                    </ol>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Commands */}
        <div className="bg-white p-6 shadow-md rounded-lg">
          <h3 className="text-base font-semibold text-gray-800 flex items-center gap-2 mb-4">
            <Terminal className="w-4 h-4 text-indigo-500" /> {t('marketplaceAgent.slashCommands')}
            <span className="text-xs font-normal text-gray-400">({commands.length})</span>
          </h3>
          {commands.length === 0 ? (
            <p className="text-sm text-gray-400">{t('marketplaceAgent.thisAgentHasNoCustom')}</p>
          ) : (
            <div className="divide-y divide-gray-100 border border-gray-200 rounded-xl overflow-hidden">
              {commands.map((cmd) => (
                <div key={cmd.name} className="flex items-start gap-4 px-4 py-3 bg-white hover:bg-gray-50 transition-colors">
                  <span className="text-sm font-semibold text-indigo-600 shrink-0 w-40">{cmd.name}</span>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-gray-700">{cmd.description}</p>
                    {cmd.template && (
                      <p className="text-xs text-gray-400 mt-0.5 truncate">{t('marketplaceAgent.template')}: {cmd.template}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </PageContainer>
  );
};

export default MarketplaceAgent;
