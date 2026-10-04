// The Isolation section of workspace settings (common/isolation.py,
// dashboard/backend/routes/isolation.py), the "Isolated" badge next to the
// workspace name, and the tool picker's gating hint.
export default {
  title: 'Isolation',
  explainer: 'Inside this workspace, an agent may do anything. Its shell and code run in a sandbox container with no network at all. The internet can only be read on the sites listed below, through the hub, GET only. Tools that work around the perimeter are turned off and cannot be added here.',
  switchLabel: 'Isolated workspace',
  statusOn: 'This workspace is isolated.',
  statusOff: 'This workspace is not isolated.',
  confirmOff: "Turn off isolation? Shell commands and code will run on the hub's host again, agents get back the tools that reach outside, and MCP servers, channels and widgets can be attached to this workspace again.",
  readiness: 'Hub readiness',
  checks: {
    docker: 'Docker',
    sandbox_image: 'Sandbox image',
  },
  domains: 'Sites the agent may read',
  domainsHint: 'One host per line. A host also allows its own subdomains.',
  saveDomains: 'Save sites',
  domainsSaved: 'Saved.',
  offendingAgents: 'Agents with tools outside the perimeter',
  offendingAgentsHint: 'Remove these tools from the agent, or move it to another workspace, before turning isolation on.',
  badge: 'Isolated',
  toolHint: 'Not available in an isolated workspace',
  errors: {
    load: 'Could not load the isolation state.',
    save: 'Could not save.',
  },
};
