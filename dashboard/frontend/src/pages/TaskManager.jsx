import React, { useState } from 'react';
import { createPortal } from 'react-dom';
import { CheckSquare, PanelRightClose, PanelRightOpen } from 'lucide-react';
import { useWorkspace } from '../components/workspace';
import TaskBoard from '../components/TaskBoard';
import PlannerChat from '../components/flow/PlannerChat';
import { PageContainer, PageHeader } from '../components/PageLayout';
import { usePageChatHost } from '../components/pageChat/pageChat';
import { useI18n } from '../i18n';
import { usePhone } from '../lib/phone';

// ─── Main TaskManager page ────────────────────────────────────────────────────
// Thin wrapper around the reusable <TaskBoard>, scoped to the workspace selected
// in the global workspace switcher. The same board is embedded (filtered by
// workspace / project) in the Workspace and Project detail pages — there it
// portals its toolbar into the tab row, here into the shared page header.
// The task manager's chat is the page chat: the board's button and the corner
// launcher open the same floating panel, as on a project's Tasks tab. It plans
// over the whole workspace; the board picks its tasks up through live updates.
const TaskManager = () => {
  const { t } = useI18n();
  const { selectedWorkspace, workspaceFilter, liveUpdates } = useWorkspace();
  const [toolbar, setToolbar] = useState(null);
  const plannerWorkspace = selectedWorkspace || 'default';
  const planner = usePageChatHost({ title: t('flowPlannerChat.taskPlanner') });
  // The board's toolbar sits in the header's actions; on a phone, where the
  // header's first line is the app's top bar, it takes a line of the page.
  const phone = usePhone();
  const toolbarSlot = <div ref={setToolbar} className="flex items-center w-full sm:w-auto" />;

  return (
    <PageContainer fill>
      <PageHeader
        icon={CheckSquare}
        title={t('taskManager.tasks')}
        description={t('taskManager.everythingTheAgentsHaveBeen')}
        actions={phone ? undefined : toolbarSlot}
      >
        {phone ? toolbarSlot : null}
      </PageHeader>
      <div className="flex-1 min-h-0 flex gap-3">
        <div className="flex-1 min-w-0 min-h-0 overflow-y-auto">
          <TaskBoard
            workspace={workspaceFilter}
            selectedWorkspace={selectedWorkspace}
            showWorkspaceColumn={selectedWorkspace === 'default'}
            liveUpdates={liveUpdates}
            toolbarTarget={toolbar}
            // On a phone the corner chat button is the one way in.
            extraControls={(
              <button onClick={planner.toggle} aria-pressed={planner.open}
                className={`flex phone:hidden items-center gap-1.5 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                  planner.open ? 'text-emerald-700 bg-emerald-50' : 'text-emerald-600 hover:bg-emerald-50'}`}
                title={planner.open ? t('flowPlannerChat.hidePlanner') : t('projectDetails.showTheTaskPlanner')}>
                {planner.open ? <PanelRightClose className="w-4 h-4" /> : <PanelRightOpen className="w-4 h-4" />} <span className="hidden sm:inline">{t('projectDetails.planner')}</span>
              </button>
            )}
          />
        </div>
        {planner.slot && createPortal(
          <PlannerChat key={plannerWorkspace} workspace={plannerWorkspace} tools={planner.tools} />,
          planner.slot,
        )}
      </div>
    </PageContainer>
  );
};

export default TaskManager;
