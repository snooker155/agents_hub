import React, { useState } from 'react';
import { CheckSquare } from 'lucide-react';
import { useWorkspace } from '../components/workspace';
import TaskBoard from '../components/TaskBoard';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { useI18n } from '../i18n';

// ─── Main TaskManager page ────────────────────────────────────────────────────
// Thin wrapper around the reusable <TaskBoard>, scoped to the workspace selected
// in the global workspace switcher. The same board is embedded (filtered by
// workspace / project) in the Workspace and Project detail pages — there it
// portals its toolbar into the tab row, here into the shared page header.
const TaskManager = () => {
  const { t } = useI18n();
  const { selectedWorkspace, workspaceFilter, liveUpdates } = useWorkspace();
  const [toolbar, setToolbar] = useState(null);

  return (
    <PageContainer fill>
      <PageHeader
        icon={CheckSquare}
        title={t('taskManager.tasks')}
        description={t('taskManager.everythingTheAgentsHaveBeen')}
        actions={<div ref={setToolbar} className="flex items-center" />}
      />
      <div className="flex-1 min-h-0 overflow-y-auto">
        <TaskBoard
          workspace={workspaceFilter}
          selectedWorkspace={selectedWorkspace}
          showWorkspaceColumn={selectedWorkspace === 'default'}
          liveUpdates={liveUpdates}
          toolbarTarget={toolbar}
        />
      </div>
    </PageContainer>
  );
};

export default TaskManager;
