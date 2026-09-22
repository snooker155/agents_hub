import InstanceList from '../InstanceList';
import { useAgentPage } from './context';

/** The live copies of this agent that are running right now. */
export default function InstancesTab() {
  const { id, liveUpdates, workspaceFilter } = useAgentPage();
  return (
        <InstanceList
          agentId={id}
          workspace={workspaceFilter}
          liveUpdates={liveUpdates}
          showAgentColumn={false}
        />
  );
}
