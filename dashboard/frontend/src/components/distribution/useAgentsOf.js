import { useEffect, useState } from 'react';
import { getAgents } from '../../api';

// The agents usable in a workspace; none until one is chosen.
export function useAgentsOf(workspace) {
  const [agents, setAgents] = useState([]);
  useEffect(() => {
    let live = true;
    if (!workspace) return undefined;
    getAgents(workspace)
      .then(({ data }) => { if (live) setAgents(Array.isArray(data) ? data : []); })
      .catch(() => { if (live) setAgents([]); });
    return () => { live = false; };
  }, [workspace]);
  return workspace ? agents : [];
}
