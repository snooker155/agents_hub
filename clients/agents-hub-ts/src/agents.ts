import type { Transport } from "./transport.ts";
import type { AgentDetail, AgentListItem, AgentPage } from "./generated/types.ts";

export interface AgentListParams {
  workspace?: string;
  /** Paginate the assembled list; with neither set the full list comes back (dashboard/backend/routes/agents.py). */
  limit?: number;
  offset?: number;
}

/** `agents.list()`: `AgentListItem[]` with no paging, `AgentPage` (`{items, total, limit, offset}`) with either `limit` or `offset` set. */
export type AgentList = AgentListItem[] | AgentPage;

/** `GET /api/agents`, `GET /api/agents/{agent_id}` (dashboard/backend/routes/agents.py). */
export class AgentsResource {
  private readonly transport: Transport;

  constructor(transport: Transport) {
    this.transport = transport;
  }

  list(params: AgentListParams = {}): Promise<AgentList> {
    return this.transport.request("get", "/api/agents", { query: { ...params } });
  }

  get(agentId: string): Promise<AgentDetail> {
    return this.transport.request("get", "/api/agents/{agent_id}", { params: { agent_id: agentId } });
  }

  delete(agentId: string): Promise<unknown> {
    return this.transport.request("delete", "/api/agents/{agent_id}", { params: { agent_id: agentId } });
  }
}

export type { AgentDetail, AgentListItem, AgentPage };
