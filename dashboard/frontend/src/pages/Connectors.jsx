import { useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { BookOpen, Boxes, Calendar, Database, GitBranch, Hash, Link2, Mail, MessageCircle, Send, Ticket, Users, Webhook } from 'lucide-react';

import BlenderConnector from '../components/connectors/BlenderConnector';
import ChannelConnector from '../components/connectors/ChannelConnector';
import DatabasesConnector from '../components/connectors/DatabasesConnector';
import GoogleConnector from '../components/connectors/GoogleConnector';
import KnowledgeConnector from '../components/connectors/KnowledgeConnector';
import MicrosoftConnector from '../components/connectors/MicrosoftConnector';
import TrackersConnector from '../components/connectors/TrackersConnector';
import GitConnector from '../components/connectors/GitConnector';
import TelegramConnector from '../components/connectors/TelegramConnector';
import WebhooksConnector from '../components/connectors/WebhooksConnector';
import { ConnectionProposalCard } from '../components/chat/ConnectionProposalCard';
import { listConnectionProposals } from '../api/connectionProposals';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useWorkspace } from '../components/workspace';
import { useI18n } from '../i18n';

/**
 * Connectors: services this hub reaches out to.
 *
 * The other half of the Connect group, and the opposite direction from
 * [Connections]: there something of yours runs elsewhere and reports in, here
 * this service goes out to a system you already use. Both answer "how do I
 * attach something that is not defined in here", which is why they share a
 * group and why each page says which way it points in one line.
 *
 * These three lived as tabs inside Settings, next to model keys and log levels,
 * which is not where anyone looked for them.
 */

// The chat channels after Telegram share one component and one API
// (components/connectors/ChannelConnector.jsx, routes/channels.py).
const channel = (id, icon) => ({ id, icon, Component: () => <ChannelConnector name={id} /> });

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

// The agent's own proposals for this workspace (connection-proposals-contract.md):
// shown above the tabs so a pending one is not missed under whichever tab
// happens to be open. A failed read just means nothing to show here yet; the
// tab strip below still works.
function ProposedConnections() {
  const { t } = useI18n();
  const { selectedWorkspace: workspace } = useWorkspace();
  const [proposals, setProposals] = useState([]);

  const load = useCallback(() => {
    listConnectionProposals({ status: 'pending', workspace })
      .then(({ data }) => setProposals(data?.proposals || []))
      .catch(() => setProposals([]));
  }, [workspace]);
  useEffect(() => { load(); }, [load]);

  if (proposals.length === 0) return null;
  return (
    <div className="space-y-2" data-testid="connection-proposals-panel">
      <h2 className="text-sm font-semibold text-gray-800">{t('connectionProposal.panelTitle')}</h2>
      {proposals.map((p) => (
        <ConnectionProposalCard key={p.approval_id} approval={p} live onSettled={load} />
      ))}
    </div>
  );
}

const TABS = [
  { id: 'telegram', icon: Send, Component: TelegramConnector },
  channel('slack', Hash),
  channel('discord', MessageCircle),
  channel('teams', Users),
  channel('mail', Mail),
  { id: 'git', icon: GitBranch, Component: GitConnector },
  { id: 'trackers', icon: Ticket, Component: TrackersConnector },
  { id: 'google', icon: Calendar, Component: GoogleConnector },
  { id: 'microsoft', icon: Calendar, Component: MicrosoftConnector },
  { id: 'knowledge', icon: BookOpen, Component: KnowledgeConnector },
  { id: 'databases', icon: Database, Component: DatabasesConnector },
  { id: 'blender', icon: Boxes, Component: BlenderConnector },
  { id: 'webhooks', icon: Webhook, Component: WebhooksConnector },
];

export default function Connectors() {
  const { t } = useI18n();
  // ?tab=google opens a tab directly: the Google OAuth callback lands there,
  // and the mail forms link to it for "Connect with Gmail".
  const [params] = useSearchParams();
  const [active, setActive] = useState(() => {
    const wanted = params.get('tab');
    return TABS.some((tab) => tab.id === wanted) ? wanted : 'telegram';
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

      <ProposedConnections />

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
