import { Link } from 'react-router-dom';
import { GitBranch, Lock, Users } from 'lucide-react';
import { useI18n } from '../../../i18n';

/**
 * The one line under the agent page header that says where this agent's
 * setup comes from: "Inherits from <parent> · latest | v12" linking to the
 * parent, and how many agents extend this one, linking to the Inheritance
 * tab for both. Renders nothing for a standalone agent with no children:
 * the Inheritance tab is still reachable from the tab bar for attaching one.
 */
export default function InheritanceHeaderLine({ agent, onManage }) {
  const { t } = useI18n();
  if (!agent) return null;
  const hasParent = !!agent.extends;
  const childrenCount = agent.children_count || 0;
  if (!hasParent && childrenCount === 0) return null;

  return (
    <div className="flex items-center gap-2 text-xs text-gray-500 -mt-3 mb-4" data-testid="inheritance-header-line">
      <GitBranch className="w-3.5 h-3.5 text-gray-400" />
      {hasParent && (
        <span className="inline-flex items-center gap-1">
          {t('agentInheritance.header.inheritsFrom')}
          <Link to={`/agents/${agent.extends}`} className="font-semibold text-indigo-600 hover:text-indigo-800">
            {agent.extends}
          </Link>
          <span className="inline-flex items-center gap-0.5 text-gray-400">
            ·
            {agent.extends_version != null ? (
              <span className="inline-flex items-center gap-0.5">
                <Lock className="w-2.5 h-2.5" /> {t('agentInheritance.versionN', { n: agent.extends_version })}
              </span>
            ) : t('agentInheritance.latest')}
          </span>
        </span>
      )}
      {hasParent && childrenCount > 0 && <span className="text-gray-300">•</span>}
      {childrenCount > 0 && (
        <button type="button" onClick={onManage} className="inline-flex items-center gap-1 text-indigo-600 hover:text-indigo-800">
          <Users className="w-3 h-3" />
          {t('agentInheritance.header.childrenCount', { count: childrenCount })}
        </button>
      )}
      <button type="button" onClick={onManage} className="text-gray-400 hover:text-indigo-600 ml-1">
        {t('agentInheritance.header.manage')}
      </button>
    </div>
  );
}
