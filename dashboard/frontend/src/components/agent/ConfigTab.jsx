import { ChatColumn, FILL_COLUMN } from '../ChatColumn';
import EntityChat from '../EntityChat';
import SystemAgentWarning from './SystemAgentWarning';
import { BookOpen, FileCode, Loader, Save, Terminal, Zap } from 'lucide-react';
import { useAgentPage } from './context';

/** The definition: the prompt files alone. Versions, guardrails, the pulse and
 * experiments each have a tab of their own. */
export default function ConfigTab() {
  const {
    agent, agentDefinition, defChat, defDraft, defError, defSaving,
    definitionChat, handleDefinitionDraftChange,
    handleResetDefinitionField, handleSaveDefinitionField, t,
  } = useAgentPage();
  return (
        <>
        <div className={defChat.gridClass}>
          <div className={`space-y-6 ${defChat.mainClass}`}>
          {agent.system && <SystemAgentWarning scope="config" />}

          {[
            { key: 'instructions', label: 'instructions.md', desc: t('agentDetails.definitions.instructions'), icon: Terminal, required: true },
            { key: 'capabilities', label: 'capabilities.md', desc: t('agentDetails.definitions.capabilities'), icon: Zap, required: false },
            { key: 'usage',        label: 'usage.md',        desc: t('agentDetails.definitions.usage'), icon: BookOpen, required: false },
          ].map(({ key, label, desc, icon: Icon, required }) => {
            const original = agentDefinition[key] || '';
            const draft = defDraft[key] || '';
            const dirty = draft !== original;
            const saving = defSaving[key];
            const error = defError[key];
            const cannotDelete = required && !draft.trim();
            return (
              <div key={key} className="bg-white p-6 shadow-md rounded-lg">
                <div className="flex items-start justify-between gap-3 mb-3">
                  <div>
                    <h3 className="text-lg font-bold flex items-center">
                      <Icon className="w-5 h-5 mr-2 text-indigo-600" />
                      {label}
                      {required && <span className="ml-2 text-[10px] uppercase tracking-wide font-bold text-red-500">{t('agentDetails.required')}</span>}
                    </h3>
                    <p className="text-xs text-gray-500 mt-1">{desc}</p>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <button
                      onClick={() => handleResetDefinitionField(key)}
                      disabled={!dirty || saving}
                      className="px-3 py-1.5 text-xs font-semibold text-gray-600 bg-gray-100 rounded hover:bg-gray-200 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {t('agentDetails.reset')}
                    </button>
                    <button
                      onClick={() => handleSaveDefinitionField(key)}
                      disabled={!dirty || saving || cannotDelete}
                      title={cannotDelete ? t('agentDetails.instructionsRequired') : undefined}
                      className="inline-flex items-center px-3 py-1.5 text-xs font-semibold text-white bg-indigo-600 rounded hover:bg-indigo-700 disabled:opacity-40 disabled:cursor-not-allowed"
                    >
                      {saving ? <Loader className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Save className="w-3.5 h-3.5 mr-1" />}
                      Save
                    </button>
                  </div>
                </div>
                <textarea
                  value={draft}
                  onChange={e => handleDefinitionDraftChange(key, e.target.value)}
                  spellCheck={false}
                  className="w-full font-mono text-xs bg-gray-900 text-green-300 p-4 rounded-lg min-h-[200px] resize-y border border-gray-800 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  placeholder={required ? t('agentDetails.definitionRequired') : t('agentDetails.definitionOptional')}
                />
                {error && (
                  <p className="text-xs text-red-600 mt-2">{error}</p>
                )}
                {dirty && !error && (
                  <p className="text-xs text-amber-600 mt-2">{t('agentDetails.unsavedChanges')}</p>
                )}
              </div>
            );
          })}

          {agentDefinition.system_prompt && (
            <div className="bg-white p-6 shadow-md rounded-lg">
              <h3 className="text-lg font-bold mb-2 flex items-center">
                <FileCode className="w-5 h-5 mr-2 text-indigo-600" />
                Assembled System Prompt (read-only)
              </h3>
              <p className="text-xs text-gray-500 mb-3">
                {t('agentDetails.whatTheAgentActuallyReceives')}
              </p>
              <pre className="text-xs bg-gray-900 text-green-300 p-4 rounded-lg overflow-auto whitespace-pre-wrap max-h-96">
                {agentDefinition.system_prompt}
              </pre>
            </div>
          )}
          </div>

          {defChat.open && definitionChat && (
            <ChatColumn>
              <EntityChat {...definitionChat} {...FILL_COLUMN} onHide={() => defChat.setOpen(false)} />
            </ChatColumn>
          )}
        </div>
        </>
  );
}
