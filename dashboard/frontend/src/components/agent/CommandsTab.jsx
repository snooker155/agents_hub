import { Hash, Terminal } from 'lucide-react';
import { useAgentPage } from './context';

/** The slash commands this agent answers to. */
export default function CommandsTab() {
  const { agent, t } = useAgentPage();
        const agentCmds = agent?.commands || [];
        const globalCmds = [
          { name: '/help', description: t('chat.commands.help'), template: '/help' },
          { name: '/clear', description: t('chat.commands.clear'), template: '/clear' },
          { name: '/new', description: t('chat.commands.new'), template: '/new' },
          { name: '/config', description: t('chat.commands.config'), template: '/config' },
        ];
        return (
          <div className="space-y-6">
            <div className="bg-white p-6 shadow-md rounded-lg">
              <h3 className="text-lg font-bold mb-1 flex items-center gap-2">
                <Terminal className="w-5 h-5 text-indigo-600" /> {t('agentDetails.slashCommands')}
              </h3>
              <p className="text-sm text-gray-500 mb-5">
                Type <span className=" bg-gray-100 px-1 rounded">/</span> {t('agentDetails.inTheChatToTrigger')} <kbd className="text-xs bg-gray-100 border border-gray-200 rounded px-1">↑↓</kbd> {t('agentDetails.toNavigate')} <kbd className="text-xs bg-gray-100 border border-gray-200 rounded px-1">{t('agentDetails.enter')}</kbd> {t('agentDetails.or')} <kbd className="text-xs bg-gray-100 border border-gray-200 rounded px-1">{t('agentDetails.tab')}</kbd> {t('agentDetails.toSelect')} <kbd className="text-xs bg-gray-100 border border-gray-200 rounded px-1">{t('agentDetails.esc')}</kbd> {t('agentDetails.toDismiss')}
              </p>

              {agentCmds.length > 0 && (
                <div className="mb-6">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-3">{t('agentDetails.agentCommands')}</h4>
                  <div className="divide-y divide-gray-100 border border-gray-200 rounded-xl overflow-hidden">
                    {agentCmds.map((cmd) => (
                      <div key={cmd.name} className="flex items-start gap-4 px-4 py-3 bg-white hover:bg-gray-50 transition-colors">
                        <span className=" text-sm font-semibold text-indigo-600 shrink-0 w-40">{cmd.name}</span>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm text-gray-700">{cmd.description}</p>
                          <p className="text-xs text-gray-400 mt-0.5 truncate">{t('agentDetails.template')}: {cmd.template}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {agentCmds.length === 0 && (
                <div className="mb-6 flex items-center gap-3 p-4 bg-amber-50 border border-amber-100 rounded-xl text-sm text-amber-700">
                  <Hash className="w-4 h-4 shrink-0" />
                  {t('agentDetails.noAgentCommands')} <span className=" mx-1">"commands"</span> {t('agentDetails.arrayToThisAgentIn')} <span className=" ml-1">.agents_hub/agents.json</span>.
                </div>
              )}

              <div>
                <h4 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-3">{t('agentDetails.globalCommands')}</h4>
                <div className="divide-y divide-gray-100 border border-gray-200 rounded-xl overflow-hidden">
                  {globalCmds.map((cmd) => (
                    <div key={cmd.name} className="flex items-start gap-4 px-4 py-3 bg-white hover:bg-gray-50 transition-colors">
                      <span className=" text-sm font-semibold text-gray-600 shrink-0 w-40">{cmd.name}</span>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-gray-700">{cmd.description}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        );
}
