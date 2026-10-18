import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { BellRing, GitBranch, Link2, Ticket } from 'lucide-react';

import TrackersConnector from '../components/connectors/TrackersConnector';
import GitConnector from '../components/connectors/GitConnector';
import AlertRulesConnector from '../components/connectors/AlertRulesConnector';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useWorkspace } from '../components/workspace';
import { useI18n } from '../i18n';

/**
 * Connectors: systems this hub reaches out to (GitLab, Jira), and the alert
 * rules that raise a notification on their own.
 *
 * These lived as tabs inside Settings, next to model keys and log levels,
 * which is not where anyone looked for them.
 */

// A connector lives only in the workspace that defines it: the default
// workspace's definition works everywhere, another workspace's own
// definition works only there (connectors/channels/store.py). Every card
// below says which one it is (ConnectorSourceBadge); the page's ⓘ says it
// once for the page, naming the workspace these tabs are about to change.
function WorkspaceScopeNote() {
  const { t } = useI18n();
  const { selectedWorkspace } = useWorkspace();
  const workspace = selectedWorkspace || 'default';
  return (
    <span className="block mt-2" data-testid="connectors-workspace-banner">
      {t('connectors.workspaceBanner.title', { workspace })}{' '}
      {t('connectors.workspaceBanner.rule')}
    </span>
  );
}

const TABS = [
  { id: 'git', icon: GitBranch, Component: GitConnector },
  { id: 'trackers', icon: Ticket, Component: TrackersConnector },
  { id: 'alerts', icon: BellRing, Component: AlertRulesConnector },
];

export default function Connectors() {
  const { t } = useI18n();
  // ?tab=trackers opens a tab directly (a link from a project's page).
  const [params] = useSearchParams();
  const [active, setActive] = useState(() => {
    const wanted = params.get('tab');
    return TABS.some((tab) => tab.id === wanted) ? wanted : 'git';
  });
  const { Component } = TABS.find((tab) => tab.id === active) || TABS[0];

  return (
    <PageContainer>
      <PageHeader
        icon={Link2}
        title={t('connectors.title')}
        description={(
          <>
            {t('connectors.description')}
            <WorkspaceScopeNote />
          </>
        )}
      />


      <div className="flex flex-wrap gap-1 border-b border-gray-200">
        {TABS.map(({ id, icon: Icon }) => (
          <button
            key={id}
            type="button"
            onClick={() => setActive(id)}
            className={`flex items-center gap-2 px-3 py-2 text-sm font-semibold border-b-2 -mb-px ${
              active === id
                ? 'border-indigo-600 text-indigo-700'
                : 'border-transparent text-gray-500 hover:text-gray-700'
            }`}
          >
            <Icon className="w-4 h-4" />
            {t(`connectors.tabs.${id}`)}
          </button>
        ))}
      </div>

      <Component />
    </PageContainer>
  );
}
