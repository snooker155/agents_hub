import { useState } from 'react';
import { Boxes, GitBranch, Link2, Send } from 'lucide-react';

import BlenderConnector from '../components/connectors/BlenderConnector';
import GitConnector from '../components/connectors/GitConnector';
import TelegramConnector from '../components/connectors/TelegramConnector';
import { PageContainer, PageHeader } from '../components/PageLayout';
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

const TABS = [
  { id: 'telegram', icon: Send, Component: TelegramConnector },
  { id: 'git', icon: GitBranch, Component: GitConnector },
  { id: 'blender', icon: Boxes, Component: BlenderConnector },
];

export default function Connectors() {
  const { t } = useI18n();
  const [active, setActive] = useState('telegram');
  const { Component } = TABS.find((tab) => tab.id === active) || TABS[0];

  return (
    <PageContainer>
      <PageHeader
        icon={Link2}
        title={t('connectors.title')}
        description={t('connectors.description')}
      />

      <div className="flex flex-wrap gap-1 mb-5 border-b border-gray-200">
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
