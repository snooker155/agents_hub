import React from 'react';
import { Activity } from 'lucide-react';

import InstanceList from '../components/InstanceList';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useWorkspace } from '../components/workspace';
import { useI18n } from '../i18n';

/*
 * Every live copy of every agent in the workspace, on one page.
 *
 * Nodes and Containers show the *carriers* — the processes and images an agent
 * can run inside. This page shows the copies themselves, whichever carrier they
 * happen to sit on, including the ones parked in standby with no run in flight
 * and the ones that finished but still hold their context. Clicking one opens
 * it; from there you can write to it.
 */
export default function Instances() {
  const { t } = useI18n();
  const { workspaceFilter, selectedWorkspace, liveUpdates } = useWorkspace();

  return (
    <PageContainer>
      <PageHeader
        icon={Activity}
        title={t('instances.title')}
        description={selectedWorkspace && selectedWorkspace !== 'default'
          ? t('instances.descriptionInWorkspace', { workspace: selectedWorkspace })
          : t('instances.description')}
      />
      <InstanceList workspace={workspaceFilter} liveUpdates={liveUpdates} />
    </PageContainer>
  );
}
